from typing import Optional, Tuple, List

import numpy as np
import pandas as pd
from PyQt6.QtGui import QColor

from qtdisplay.chart.config import PlotConfig
from qtdisplay.chart.model.base import BaseChartModel, DirtyFlags
from qtdisplay.chart.model.data import points


class XYChartModel(BaseChartModel[points.PointsVector]):
    """
    Model for XY-coordinate based charts (Line, Scatter).
    Uses PointsVector for efficient (x, y) point storage with incremental range tracking.
    """

    def __init__(self, config: PlotConfig) -> None:
        super().__init__(config)
        # Incremental range tracking
        self._cached_range: Optional[Tuple[float, float, float, float]] = None
        self._range_valid = False

    def _create_empty_series(self) -> 'PointsVector':
        """Create empty PointsVector."""
        return points.PointsVector(self.config.max_points)

    def _invalidate_range(self) -> None:
        """Invalidate cached range, requiring recalculation."""
        self._range_valid = False
        self._cached_range = None

    def _update_range_incremental(self, series_name: str) -> None:
        """
        Update range incrementally when a single series changes.
        For real-time charts, this ensures range reflects current data.
        Optimized for minimal overhead and memory efficiency.
        """
        # Early return optimization - check all conditions at once
        if not self.config.is_real_time or not self._range_valid:
            self._invalidate_range()
            return

        # Cache attribute lookups to avoid repeated dictionary access
        vector = self._series_data.get(series_name)
        if not vector or not len(vector) or not self._series_visibility.get(
                series_name, True):
            return

        # Get bounds once (avoid multiple method calls)
        (vec_x_min, vec_x_max), (vec_y_min, vec_y_max) = vector.get_bounds()

        # Direct tuple unpacking is faster than indexing
        if self._cached_range is None:
            # Direct assignment - no computation needed
            self._cached_range = (vec_x_min, vec_x_max, vec_y_min, vec_y_max)
        else:
            # Unpack once, compute min/max, repack
            # This avoids repeated tuple indexing operations
            x_min, x_max, y_min, y_max = self._cached_range
            self._cached_range = (
                vec_x_min if vec_x_min < x_min else x_min,
                vec_x_max if vec_x_max > x_max else x_max,
                vec_y_min if vec_y_min < y_min else y_min,
                vec_y_max if vec_y_max > y_max else y_max
            )

    def _flush_signals(self) -> None:
        """Override to emit range change signal."""
        # Emit data change signals first
        # Then emit range change if needed
        if self._dirty_flags & DirtyFlags.RANGE:
            try:
                x_min, x_max, y_min, y_max = self.get_data_range()
                self.rangeChanged.emit(x_min, x_max, y_min, y_max)
            except ValueError:
                pass

        super()._flush_signals()

    # ==================== Data Operations ====================

    def append_point(self, series_name: str, x: float, y: float) -> None:
        """Append a point to a series."""
        # s = timeit.default_timer()
        if series_name in self._series_data:
            self._series_data[series_name].append(x, y)
            self._invalidate_range()
            if self.config.is_real_time or self._cached_range is None:
                self._update_range_incremental(series_name)
            else:
                self._invalidate_range()

            self._mark_dirty(DirtyFlags.DATA | DirtyFlags.RANGE, series_name)
        # logger.debug(f"Model only update time (ms): "
        #              f"{(timeit.default_timer()-s)*1000}")

    def append_points(self, series_name: str, x_values: List[float],
                      y_values: List[float]) -> None:
        """Append multiple points to a series efficiently."""
        if series_name not in self._series_data:
            return

        if len(x_values) != len(y_values):
            raise ValueError("x_values and y_values must have the same length")

        self.begin_update()
        try:
            pv_append = self._series_data[series_name].append
            for x, y in zip(x_values, y_values):
                pv_append(x, y)

            if self.config.is_real_time:
                self._update_range_incremental(series_name)
            else:
                self._invalidate_range()

            self._mark_dirty(DirtyFlags.DATA | DirtyFlags.RANGE, series_name)
        finally:
            self.end_update()

    def replace_series_data(self, series_name: str, x_values: np.ndarray,
                            y_values: np.ndarray) -> None:
        """Replace all data in a series."""
        if series_name not in self._series_data:
            return

        if len(x_values) != len(y_values):
            raise ValueError("x_values and y_values must have the same length")

        # Clear and refill the PointsVector
        vector = self._series_data[series_name]
        vector.clear()

        x_values = np.asarray(x_values, dtype=np.float64)
        y_values = np.asarray(y_values, dtype=np.float64)

        for x, y in zip(x_values, y_values):
            vector.append(x, y)

        self._invalidate_range()
        self._mark_dirty(DirtyFlags.DATA | DirtyFlags.RANGE, series_name)

    def clear_series_data(self, series_name: str) -> None:
        """Clear all data from a series without removing the series."""
        if series_name in self._series_data:
            self._series_data[series_name] = self._create_empty_series()
            self._invalidate_range()
            self._mark_dirty(DirtyFlags.DATA | DirtyFlags.RANGE, series_name)

    def set_series_visibility(self, series_name: str, visible: bool) -> None:
        """Override to invalidate range on visibility change."""
        if series_name in self._series_visibility:
            old_visible = self._series_visibility[series_name]
            if old_visible != visible:
                self._series_visibility[series_name] = visible
                self._invalidate_range()
                self._mark_dirty(
                    DirtyFlags.DATA | DirtyFlags.RANGE | DirtyFlags.METADATA)

    def get_data_range(self) -> Tuple[float, float, float, float]:
        """
        Get overall data range across all visible series.
        Uses cached value when valid.

        Returns:
            (x_min, x_max, y_min, y_max)
        Raises:
            ValueError: if no visible series have data.
        Note:
            Visibility defaults to True for series absent from
            _series_visibility, so orphaned entries are included.
        """
        if self._range_valid and self._cached_range is not None:
            return self._cached_range

        x_min, x_max = float('inf'), float('-inf')
        y_min, y_max = float('inf'), float('-inf')

        for name, vector in self._series_data.items():
            if not (len(vector) > 0 and self._series_visibility.get(name,
                                                                    True)):
                continue
            (vec_x_min, vec_x_max), (vec_y_min, vec_y_max) = vector.get_bounds()
            x_min = min(x_min, vec_x_min)
            x_max = max(x_max, vec_x_max)
            y_min = min(y_min, vec_y_min)
            y_max = max(y_max, vec_y_max)

        if x_min == float('inf'):
            raise ValueError("No visible series with data")

        self._cached_range = (x_min, x_max, y_min, y_max)
        self._range_valid = True
        return self._cached_range

    def get_series_data_range(self, name: str) -> Tuple[
        float, float, float, float]:
        """
        Get the data range for a single series by name.

        Unlike get_data_range, this is not cached — per-series bounds are
        cheap (delegated to vector.get_bounds()) and series-level cache
        invalidation would add complexity for little gain.

        Args:
            name: The series name to query.
        Returns:
            (x_min, x_max, y_min, y_max)
        Raises:
            KeyError:   if `name` is not a known series.
            ValueError: if the series is empty or not visible.
        """
        if name not in self._series_data:
            raise KeyError(f"Unknown series: {name!r}")

        if not self._series_visibility.get(name, True):
            raise ValueError(f"Series {name!r} is not visible")

        vector = self._series_data[name]
        # if len(vector) == 0:
        #     raise ValueError(f"Series {name!r} has no data")

        (x_min, x_max), (y_min, y_max) = vector.get_bounds()
        return (x_min, x_max, y_min, y_max)
    def _build_dataframe(self) -> pd.DataFrame:
        """Build DataFrame from current data (lazy construction)."""
        if not self._series_data:
            return pd.DataFrame()

        series_arrays = {}
        all_x_values = []

        for series_name, vector in self._series_data.items():
            if len(vector) > 0:
                x_array, y_array = vector.to_arrays()
                if len(x_array) > 0:
                    series_arrays[str(series_name)] = (x_array, y_array)
                    all_x_values.append(x_array)

        if not all_x_values:
            return pd.DataFrame()

        x_values = np.unique(np.concatenate(all_x_values))

        # Build as 2D numpy array
        column_names = list(series_arrays.keys())
        n_rows = len(x_values)
        n_cols = len(column_names)

        data_array = np.empty((n_rows, n_cols), dtype=np.float64)
        data_array.fill(np.nan)

        for col_idx, series_name in enumerate(column_names):
            x_array, y_array = series_arrays[series_name]
            indices = np.searchsorted(x_values, x_array)
            mask = (indices < n_rows) & (x_values[indices] == x_array)
            data_array[indices[mask], col_idx] = y_array[mask]

        df = pd.DataFrame(
            data_array,
            index=x_values.astype(np.float64, copy=True),
            columns=column_names,
            copy=True
        )
        df.index.name = 'X'

        return df

    # ==================== NPZ Persistence ====================

    def save_to_npz(self, filepath: str) -> Tuple[bool, Optional[str]]:
        """Save data to NPZ format."""
        try:
            data_dict = {}
            metadata = {
                'series_names': list(self._series_data.keys()),
                'config_title': self.config.title,
                'config_x_title': self.config.x_title,
                'config_y_title': self.config.y_title,
                'config_x_unit': self.config.x_unit,
                'config_y_unit': self.config.y_unit,
            }

            for name, vector in self._series_data.items():
                x_data, y_data = vector.to_arrays()
                data_dict[f'{name}_x'] = x_data
                data_dict[f'{name}_y'] = y_data
                data_dict[f'{name}_color_r'] = np.array(
                    [self._series_colors[name].red()])
                data_dict[f'{name}_color_g'] = np.array(
                    [self._series_colors[name].green()])
                data_dict[f'{name}_color_b'] = np.array(
                    [self._series_colors[name].blue()])

            for key, value in metadata.items():
                if isinstance(value, list):
                    data_dict[key] = np.array(value, dtype=object)
                else:
                    data_dict[key] = np.array([value], dtype=object)

            np.savez_compressed(filepath, **data_dict)
            return True, None

        except Exception as e:
            return False, f"Error saving NPZ: {e}"

    def load_from_npz(self, filepath: str) -> Tuple[bool, Optional[str]]:
        """Load data from NPZ format."""
        try:
            data = np.load(filepath, allow_pickle=True)

            if 'series_names' not in data:
                raise ValueError("Invalid NPZ file: missing series_names")

            series_names = data['series_names'].tolist()

            self.begin_update()
            try:
                self._series_data.clear()
                self._series_colors.clear()
                self._series_visibility.clear()
                self._invalidate_range()

                for name in series_names:
                    x_key = f'{name}_x'
                    y_key = f'{name}_y'

                    if x_key not in data or y_key not in data:
                        raise ValueError(
                            f"Invalid NPZ file: missing data for series {name}")

                    x_data = data[x_key]
                    y_data = data[y_key]

                    if len(x_data) != len(y_data):
                        raise ValueError(
                            f"Invalid data for series {name}: x and y lengths don't match")

                    if not np.all(np.isfinite(x_data)) or not np.all(
                            np.isfinite(y_data)):
                        raise ValueError(
                            f"Invalid data for series {name}: contains non-finite values")

                    color_r = int(data.get(f'{name}_color_r', [255])[0])
                    color_g = int(data.get(f'{name}_color_g', [0])[0])
                    color_b = int(data.get(f'{name}_color_b', [0])[0])
                    color = QColor(color_r, color_g, color_b)

                    vector = points.PointsVector(self.config.max_points)

                    # Fill vector with data
                    for x, y in zip(x_data, y_data):
                        vector.append(x, y)

                    self._series_data[name] = vector
                    self._series_colors[name] = color
                    self._series_visibility[name] = True

            finally:
                self.end_update()

            return True, None

        except Exception as e:
            return False, f"Error loading NPZ: {e}"


LineChartModel = XYChartModel
SplineChartModel = XYChartModel
ScatterChartModel = XYChartModel

from typing import List, Tuple, Optional

import numpy as np
import pandas as pd

from qtdisplay.chart.config import PlotConfig
from qtdisplay.chart.model.base import BaseChartModel, \
    DirtyFlags, logger
from qtdisplay.chart.model.data import points


class AreaChartModel(BaseChartModel['DualPointVector']):
    """
    Model for area charts using correct DualPointVector API
    """

    def __init__(self, config: PlotConfig) -> None:
        super().__init__(config)
        self._global_bounds_dirty = True
        self._cached_bounds = ((0.0, 0.0), (0.0, 0.0))

    def _create_empty_series(self) -> 'DualPointVector':
        """Create an empty DualPointVector for series data."""
        return points.DualPointVector(max_size=self.config.max_points)

    def append_point(self, series_name: str, x: float, y_upper: float,
                     y_lower: float) -> None:
        """Append a point to a series."""
        if series_name not in self._series_data:
            self.add_series(series_name)

        series_data = self._series_data[series_name]
        series_data.append(x, y_upper, y_lower)

        self._global_bounds_dirty = True
        self._mark_dirty(DirtyFlags.DATA | DirtyFlags.RANGE, series_name)

    def set_series_data(self, series_name: str, x_data: List[float],
                        y_upper_data: List[float],
                        y_lower_data: List[float]) -> None:
        """Replace all data in a series."""
        if len(x_data) != len(y_upper_data) or len(x_data) != len(y_lower_data):
            raise ValueError("All data arrays must have same length")

        if series_name not in self._series_data:
            self.add_series(series_name)

        series_data = self._series_data[series_name]
        series_data.clear()

        for x, y_upper, y_lower in zip(x_data, y_upper_data, y_lower_data):
            series_data.append(x, y_upper, y_lower)

        self._global_bounds_dirty = True
        self._mark_dirty(DirtyFlags.DATA | DirtyFlags.RANGE, series_name)

    def get_global_bounds(self) -> Tuple[
        Tuple[float, float], Tuple[float, float]]:
        """Get global bounds across all visible series."""
        if self._global_bounds_dirty:
            self._recalculate_global_bounds()
        return self._cached_bounds

    def _recalculate_global_bounds(self) -> None:
        """Recalculate global bounds from all visible series - OPTIMIZED"""
        if not any(self.get_series_visibility(name) and len(data) > 0
                   for name, data in self._series_data.items()):
            self._cached_bounds = ((0.0, 1.0), (0.0, 1.0))
            self._global_bounds_dirty = False
            return

        # Use numpy for vectorized bounds calculation
        all_bounds = np.array([
            [series_data.bounds[0][0], series_data.bounds[0][1],  # x_min, x_max
             series_data.bounds[1][0], series_data.bounds[1][1]]  # y_min, y_max
            for name, series_data in self._series_data.items()
            if self.get_series_visibility(name) and len(series_data) > 0
        ])

        if len(all_bounds) == 0:
            self._cached_bounds = ((0.0, 1.0), (0.0, 1.0))
        else:
            min_x, max_x = np.min(all_bounds[:, 0]), np.max(all_bounds[:, 1])
            min_y, max_y = np.min(all_bounds[:, 2]), np.max(all_bounds[:, 3])

            self._cached_bounds = ((min_x, max_x), (min_y, max_y))

        self._global_bounds_dirty = False

    def _build_dataframe(self) -> pd.DataFrame:
        """Build DataFrame from area chart data - OPTIMIZED with batch operations"""
        rows = []

        for series_name, series_data in self._series_data.items():
            upper_x, upper_y = series_data.get_upper_arrays()
            lower_x, lower_y = series_data.get_lower_arrays()
            color = self.get_series_color(series_name).name()
            visible = self.get_series_visibility(series_name)

            # Batch create upper points
            upper_rows = [{
                'series': series_name, 'x': x, 'y': y,
                'bound_type': 'upper', 'color': color, 'visible': visible
            } for x, y in zip(upper_x, upper_y)]

            # Batch create lower points (reversed)
            lower_rows = [{
                'series': series_name, 'x': x, 'y': y,
                'bound_type': 'lower', 'color': color, 'visible': visible
            } for x, y in zip(reversed(lower_x), reversed(lower_y))]

            rows.extend(upper_rows)
            rows.extend(lower_rows)

        return pd.DataFrame(rows)

    def get_series_arrays(self, series_name: str) -> Tuple[
        Tuple[np.ndarray, np.ndarray],  # upper (x, y)
        Tuple[np.ndarray, np.ndarray]  # lower (x, y)
    ]:
        """Get numpy arrays for a specific series using CORRECT API."""
        if series_name not in self._series_data:
            return ((np.array([]), np.array([])), (np.array([]), np.array([])))

        series_data = self._series_data[series_name]
        # USE THE CORRECT METHOD NAMES
        upper_x, upper_y = series_data.get_upper_arrays()
        lower_x, lower_y = series_data.get_lower_arrays()

        return ((upper_x, upper_y), (lower_x, lower_y))

    # def _build_dataframe(self) -> pd.DataFrame:
    #     """Build DataFrame from area chart data."""
    #     rows = []
    #     for series_name, series_data in self._series_data.items():
    #         # USE CORRECT METHOD CALLS
    #         upper_x, upper_y = series_data.get_upper_arrays()
    #         lower_x, lower_y = series_data.get_lower_arrays()
    #
    #         for x, y in zip(upper_x, upper_y):
    #             rows.append({
    #                 'series': series_name, 'x': x, 'y': y,
    #                 'bound_type': 'upper',
    #                 'color': self.get_series_color(series_name).name(),
    #                 'visible': self.get_series_visibility(series_name)
    #             })
    #
    #         for x, y in zip(reversed(lower_x), reversed(lower_y)):
    #             rows.append({
    #                 'series': series_name, 'x': x, 'y': y,
    #                 'bound_type': 'lower',
    #                 'color': self.get_series_color(series_name).name(),
    #                 'visible': self.get_series_visibility(series_name)
    #             })
    #
    #     return pd.DataFrame(rows)

    def _mark_dirty(self, flags: DirtyFlags,
                    series_name: Optional[str] = None) -> None:
        """Mark model as dirty and invalidate cached bounds."""
        if flags & (DirtyFlags.DATA | DirtyFlags.RANGE):
            self._global_bounds_dirty = True
        super()._mark_dirty(flags, series_name)

    def _flush_signals(self) -> None:
        """Override to emit range change signal."""

        range_changed = self._dirty_flags & DirtyFlags.RANGE
        super()._flush_signals()

        if range_changed:
            try:
                (x_min, x_max), (y_min, y_max) = self.get_global_bounds()
                self.rangeChanged.emit(x_min, x_max, y_min, y_max)
                logger.debug(
                    f"AreaChartModel: Range emitted - x({x_min:.2f}, {x_max:.2f}) y({y_min:.2f}, {y_max:.2f})")
            except Exception as e:
                logger.error(f"AreaChartModel: Error emitting range signal: "
                             f"{e}")

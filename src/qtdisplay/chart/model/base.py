import logging
from abc import abstractmethod
from enum import IntFlag
from typing import Dict, List, Optional, Generic, TypeVar

import pandas as pd
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor
from mypy.binder import defaultdict

from qtcore.meta import QABCMeta
from qtdisplay.chart.config import PlotConfig

# Assuming PointsVector is imported from Cython module
# from your_cython_module import PointsVector

logger = logging.getLogger(__name__)

# Type variable for series data storage
TSeriesData = TypeVar('TSeriesData')


class DirtyFlags(IntFlag):
    """Flags to track what has changed in the model."""
    CLEAN = 0
    DATA = 1
    RANGE = 2
    METADATA = 4


# Default color palette
DEFAULT_CHART_COLORS = [
    QColor(31, 119, 180),  # Blue
    QColor(255, 127, 14),  # Orange
    QColor(44, 160, 44),  # Green
    QColor(214, 39, 40),  # Red
    QColor(148, 103, 189),  # Purple
    QColor(140, 86, 75),  # Brown
    QColor(227, 119, 194),  # Pink
    QColor(127, 127, 127),  # Gray
    QColor(188, 189, 34),  # Olive
    QColor(23, 190, 207),  # Cyan
]


class BaseChartModel(QObject, Generic[TSeriesData], metaclass=QABCMeta):
    """
    Base model for chart data with optimized signal handling.

    Generic over series data type - subclasses define what TSeriesData is:
    - XY charts use PointsVector
    - Bar charts use List[float]
    - Pie charts use float
    """

    dataChanged = pyqtSignal()
    seriesDataChanged = pyqtSignal(str)  # Signal for single series update
    seriesAdded = pyqtSignal(str)
    seriesRemoved = pyqtSignal(str)
    rangeChanged = pyqtSignal(float, float, float, float)

    def __init__(self, config: PlotConfig) -> None:
        super().__init__()
        self.config = config
        self._series_data: Dict[str, TSeriesData] = defaultdict(None)
        self._series_colors: Dict[str, QColor] = defaultdict(None)
        self._series_visibility: Dict[str, bool] = defaultdict(None)
        self._update_batch_depth = 0
        self._dirty_flags = DirtyFlags.CLEAN
        self._dirty_series: set = set()
        self._color_index = 0
        self._dataframe: Optional[pd.DataFrame] = None
        self._dataframe_dirty = True

    # Batch Updates

    def begin_update(self) -> None:
        """Begin batch update - signals deferred until end_update()."""
        self._update_batch_depth += 1

    def end_update(self) -> None:
        """End batch update and emit accumulated signals."""
        if self._update_batch_depth > 0:
            self._update_batch_depth -= 1
            if self._update_batch_depth == 0:
                self._flush_signals()

    def get_dirty_series(self):
        return self._dirty_series

    def _mark_dirty(self, flags: DirtyFlags,
                    series_name: Optional[str] = None) -> None:
        """Mark model as dirty with specific flags."""
        self._dirty_flags |= flags
        if series_name is not None:
            self._dirty_series.add(series_name)

        if flags & DirtyFlags.DATA:
            self._dataframe_dirty = True

        if self._update_batch_depth == 0:
            self._flush_signals()

    def _flush_signals(self) -> None:
        """Emit accumulated signals based on dirty flags."""
        if self._dirty_flags == DirtyFlags.CLEAN:
            return

        # Per series updates if changed
        if self._dirty_series and not (self._dirty_flags & DirtyFlags.METADATA):
            for series_name in self._dirty_series:
                self.seriesDataChanged.emit(series_name)

        if self._dirty_flags & DirtyFlags.DATA:
            self.dataChanged.emit()
        self._dirty_series.clear()
        self._dirty_flags = DirtyFlags.CLEAN

    def _get_next_color(self) -> QColor:
        """Get next color from palette, cycling through."""
        color = DEFAULT_CHART_COLORS[
            self._color_index % len(DEFAULT_CHART_COLORS)]
        self._color_index += 1
        return color

    # ==================== Series Management ====================

    def add_series(self, name: str, color: Optional[QColor] = None) -> None:
        """Add a new series to the model."""
        if name not in self._series_data:
            self._series_data[name] = self._create_empty_series()
            self._series_colors[
                name] = color if color is not None else self._get_next_color()
            self._series_visibility[name] = True
            self.seriesAdded.emit(name)
            self._mark_dirty(DirtyFlags.METADATA & ~DirtyFlags.DATA)

    def remove_series(self, name: str) -> None:
        """Remove a series from the model."""
        if name in self._series_data:
            del self._series_data[name]
            del self._series_colors[name]
            del self._series_visibility[name]
            self.seriesRemoved.emit(name)
            self._mark_dirty(DirtyFlags.DATA | DirtyFlags.RANGE, name)

    def clear_all_series(self) -> None:
        """Remove all series from the model."""
        self.begin_update()
        try:
            for name in list(self._series_data.keys()):
                self.remove_series(name)
        finally:
            self.end_update()

    def get_series_names(self) -> List[str]:
        """Get list of all series names."""
        return list(self._series_data.keys())

    def get_series_data(self, series_name: str) -> Optional[TSeriesData]:
        """Get data for a specific series."""
        return self._series_data.get(series_name)

    def get_series_data_map(self):
        return self._series_data.copy()

    # ==================== Metadata ====================

    def get_series_color(self, series_name: str) -> Optional[QColor]:
        """Get color for a specific series."""
        return self._series_colors.get(series_name)

    def set_series_color(self, series_name: str, color: QColor) -> None:
        """Set color for a specific series."""
        if series_name in self._series_colors:
            self._series_colors[series_name] = color
            self._mark_dirty(DirtyFlags.METADATA)

    def set_series_visibility(self, series_name: str, visible: bool) -> None:
        """Set visibility of a series."""
        if series_name in self._series_visibility:
            old_visible = self._series_visibility[series_name]
            if old_visible != visible:
                self._series_visibility[series_name] = visible
                self._mark_dirty(
                    DirtyFlags.DATA | DirtyFlags.RANGE | DirtyFlags.METADATA)

    def get_series_visibility(self, series_name: str) -> bool:
        """Get visibility of a series."""
        return self._series_visibility.get(series_name, True)

    def get_series_visibility_map(self):
        return self._series_visibility.copy()

    # ==================== DataFrame with Lazy Construction ====================

    def get_dataframe(self, force_rebuild: bool = False) -> pd.DataFrame:
        """
        Get data as pandas DataFrame with lazy construction.

        Args:
            force_rebuild: Force rebuild even if cache is valid

        Returns:
            DataFrame representation of data
        """
        if not self._dataframe_dirty and not force_rebuild and self._dataframe is not None:
            return self._dataframe

        self._dataframe = self._build_dataframe()
        self._dataframe_dirty = False
        return self._dataframe

    # ==================== Abstract Methods ====================

    @abstractmethod
    def _create_empty_series(self) -> TSeriesData:
        """Create empty series data structure. Implemented by subclasses."""
        pass

    @abstractmethod
    def _build_dataframe(self) -> pd.DataFrame:
        """Build DataFrame from current data. Implemented by subclasses."""
        pass


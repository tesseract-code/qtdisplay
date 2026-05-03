from typing import Optional, List

from PyQt6.QtCharts import (QAreaSeries, QLineSeries, QDateTimeAxis)
from PyQt6.QtCore import QDateTime, pyqtSlot, Qt
from PyQt6.QtGui import QColor

from pycore.log.ctx import with_logger
from qtdisplay.chart.config import PlotConfig
from qtdisplay.chart.controller.base import BaseChartController
from qtdisplay.chart.model.area import AreaChartModel


@with_logger
class QAreaChartController(BaseChartController):
    """Controller for area charts."""

    def __init__(self,
                 config: PlotConfig,
                 model: Optional[AreaChartModel] = None):

        super().__init__(config, model)

    def append_point(self, name: str, x: float, y: float,
                     y_lower: Optional[float] = None):
        """
        Append a point to the series (buffered for performance).

        Args:
            name: Series name
            x: X coordinate (timestamp for datetime axis)
            y: Upper Y bound
            y_lower: Lower Y bound (defaults to 0.0)
        """
        if name not in self.series_map:
            raise KeyError(f"Area series '{name}' not found")

        if y_lower is None:
            y_lower = 0.0

        self.models.get(name).append_point(name, x, y, y_lower)

    def _update_axes_range(self, x_min, x_max, y_min, y_max):
        """Update chart axis ranges based on model data."""
        self._logger.debug(f"Updating area chart range "
                           f"{(x_min, x_max, y_min, y_max)}")

        if isinstance(self.plot.axis_x, QDateTimeAxis):
            x_max_ms = x_max * 1_000
            x_min_ms = x_min * 1_000  # noqa: F841  (kept for symmetry / future use)

            time_window_ms = (self.config.max_points - 1) * 1_000
            x_left_ms = x_max_ms - time_window_ms

            x_left_dt = QDateTime.fromMSecsSinceEpoch(int(x_left_ms),
                                                      Qt.TimeSpec.UTC)
            x_right_dt = QDateTime.fromMSecsSinceEpoch(int(x_max_ms),
                                                       Qt.TimeSpec.UTC)

            self.plot.axis_x.setRange(x_left_dt, x_right_dt)
        else:
            x_right = x_max
            x_left = x_right - self.config.max_points
            self.plot.axis_x.setRange(x_left, x_right)

        if y_min != float('inf') and y_max != -float('inf'):
            self.plot.axis_y.setMin(y_min)
            self.plot.axis_y.setMax(y_max)

        self.plot.update()

    def remove_series(self, name: str):
        """Remove a series from the chart."""
        if name not in self.series_map:
            return

        # FIX 2: series_map stores a QAreaSeries, not a dict
        area: QAreaSeries = self.series_map.pop(name)
        self.plot.chart.removeSeries(area.upperSeries())
        self.plot.chart.removeSeries(area.lowerSeries())
        self.plot.chart.removeSeries(area)

        # FIX 3: call remove_series exactly once, guarded by membership check
        if name in self.model.get_series_names():
            self.model.remove_series(name)

    def set_series_visibility(self, name: str, visible: bool):
        """Toggle series visibility."""
        if name in self.series_map:
            self.series_map[name].setVisible(visible)

    def clear_series_data(self, name: str):
        """Clear all data from a series."""
        self.model.clear_series_data(name)

        if name in self.series_map:
            area: QAreaSeries = self.series_map[name]
            area.upperSeries().clear()
            area.lowerSeries().clear()

    # ==================== Signal Handlers ====================

    def _on_series_added(self, series_name: str):
        """Build Qt UI from model data when series is added."""
        metadata = getattr(self, '_series_metadata', {}).pop(series_name, {})
        show_lines = metadata.get('show_lines', False)

        model = self.models.get(series_name)

        (upper_x, upper_y), (lower_x, lower_y) = model.get_series_arrays(
            series_name)
        color = model.get_series_color(series_name)
        if not isinstance(color, QColor):
            color = QColor(65, 105, 225, 140)

        upper = QLineSeries()
        lower = QLineSeries()
        area = QAreaSeries(upper, lower)

        area.setName(series_name)
        area.setColor(color.lighter(170))
        area.setOpacity(0.5)
        area.setBorderColor(color)

        for x, y in zip(upper_x, upper_y):
            upper.append(self._convert_x_for_series(x), float(y))
        for x, y in zip(lower_x, lower_y):
            lower.append(self._convert_x_for_series(x), float(y))

        # Store the QAreaSeries directly; sub-series are recoverable via
        # area.upperSeries() / area.lowerSeries()
        self.series_map[series_name] = area

        self.plot.chart.addSeries(area)
        self.plot.chart.addSeries(upper)
        self.plot.chart.addSeries(lower)
        upper.setVisible(show_lines)
        lower.setVisible(show_lines)

        self._attach_shared_axes([area, upper, lower])

        for marker in self.plot.chart.legend().markers():
            if marker.series() == area:
                marker.setVisible(True)

    def _on_series_removed(self, series_name: str):
        """Clean up UI when model removes series."""

        if hasattr(self, '_series_metadata'):
            self._series_metadata.pop(series_name, None)

        if series_name in self.series_map:
            # FIX 2 (same fix): series_map stores QAreaSeries, not a dict
            area: QAreaSeries = self.series_map.pop(series_name)
            self.plot.chart.removeSeries(area.upperSeries())
            self.plot.chart.removeSeries(area.lowerSeries())
            self.plot.chart.removeSeries(area)

    @pyqtSlot(str)
    def _update_series_data(self, series_name: str):
        """Update an area series when model data changes."""
        if series_name not in self.series_map:
            return

        area: QAreaSeries = self.series_map[series_name]
        upper_series = area.upperSeries()
        lower_series = area.lowerSeries()

        (upper_x, upper_y), (lower_x, lower_y) = (
            self.models.get(series_name).get_series_arrays(series_name)
        )

        if len(upper_x) > 0:
            last_x = float(upper_x[-1])
            left_bound = last_x - self.config.max_points

            upper_series.clear()
            lower_series.clear()

            for x, y in zip(upper_x, upper_y):
                if x >= left_bound:
                    upper_series.append(self._convert_x_for_series(x), float(y))

            for x, y in zip(lower_x, lower_y):
                if x >= left_bound:
                    lower_series.append(self._convert_x_for_series(x), float(y))

    @pyqtSlot()
    def _on_data_changed(self):
        """Handle data changed signal from model."""
        self._logger.debug(f"Area Chart Data changed"
                      f" {self.model.get_dirty_series()}")
        for series in self.model.get_dirty_series():
            self._logger.debug(f"updating series : {series}")
            # FIX 5: was calling non-existent _on_series_data_changed
            self._update_series_data(series)

        self.plot.view.viewport().update()

    # ==================== Utility Methods ====================

    def get_series_names(self) -> List[str]:
        """Get list of all series names."""
        return list(self.series_map.keys())

    def get_point_count(self, series_name: str) -> int:
        """Get number of points in a series."""
        return self.model.get_series_point_count(series_name)

    def export_data(self) -> dict:
        """Export all data from the model."""
        data = {}
        for name in self.get_series_names():
            (upper_x, upper_y), (
                lower_x, lower_y) = self.model.get_series_arrays(name)
            data[name] = {
                'upper': list(zip(upper_x.tolist(), upper_y.tolist())),
                'lower': list(zip(lower_x.tolist(), lower_y.tolist())),
                'color': self.model.get_series_color(name).name(),
                'visible': self.model.get_series_visibility(name)}
        return data
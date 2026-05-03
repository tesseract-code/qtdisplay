from typing import Optional, Dict, Tuple

from PyQt6.QtCharts import QDateTimeAxis, QAreaSeries
from PyQt6.QtCore import QPointF, QPoint, Qt, QDateTime

from qtdisplay.chart.model.utils import qdatetime_to_timestamp, \
    timestamp_to_qdatetime
from qtdisplay.chart.view.features.strategy import \
    ChartInteractionStrategy


class AreaChartStrategy(ChartInteractionStrategy):
    """
    Strategy specifically for area charts with proper upper/lower bound handling.

    Area charts require different tooltip logic since they represent ranges
    rather than single values.
    """

    def supports_zoom(self) -> bool:
        return not self.view.is_real_time

    def supports_crosshair(self) -> bool:
        return True

    def supports_tooltips(self) -> bool:
        return True

    def supports_panning(self) -> bool:
        return not self.view.is_real_time

    def handle_wheel_zoom(self, event, mouse_scene: QPointF) -> bool:
        """Zoom implementation for area charts - same as line charts."""
        plot_area = self.view.chart().plotArea()
        if not plot_area.contains(mouse_scene):
            return False

        mouse_chart_before = self.view.chart().mapToValue(mouse_scene)
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15

        axes = self.view.chart().axes()
        if len(axes) < 2:
            return False

        x_axis, y_axis = axes[0], axes[1]
        x_min, x_max = x_axis.min(), x_axis.max()
        y_min, y_max = y_axis.min(), y_axis.max()

        is_datetime = False
        if isinstance(x_min, QDateTime):
            is_datetime = True
            x_min = qdatetime_to_timestamp(x_min)
            x_max = qdatetime_to_timestamp(x_max)

        x_range, y_range = x_max - x_min, y_max - y_min
        x_frac = (
                         mouse_chart_before.x() - x_min) / x_range if x_range > 0 else 0.5
        y_frac = (
                         mouse_chart_before.y() - y_min) / y_range if y_range > 0 else 0.5

        new_x_range, new_y_range = x_range / factor, y_range / factor

        if is_datetime:
            new_x_min = (qdatetime_to_timestamp(mouse_chart_before.x()) -
                         new_x_range * x_frac)
            new_x_max = qdatetime_to_timestamp((mouse_chart_before.x()) +
                                                  new_x_range * (1 - x_frac))
        else:
            new_x_min = mouse_chart_before.x() - new_x_range * x_frac
            new_x_max = mouse_chart_before.x() + new_x_range * (1 - x_frac)
        new_y_min = mouse_chart_before.y() - new_y_range * y_frac
        new_y_max = mouse_chart_before.y() + new_y_range * (1 - y_frac)

        x_axis.setRange(timestamp_to_qdatetime(new_x_min),
                        timestamp_to_qdatetime(new_x_max))
        y_axis.setRange(new_y_min, new_y_max)

        return True

    def handle_mouse_move_tooltip(self, chart_pos: QPointF,
                                  global_pos: QPoint) -> Optional[str]:
        """
        Generate tooltip for area charts showing upper/lower bounds and range.

        Area charts represent data ranges, so we show:
        - Upper bound value
        - Lower bound value
        - Range (difference)
        """
        x_val = chart_pos.x()

        x_axes = self.view.chart().axes(Qt.Orientation.Horizontal)
        if any(isinstance(axis, QDateTimeAxis) for axis in x_axes):
            date_from_timestamp = QDateTime.fromMSecsSinceEpoch(
                int(x_val)).toString()
            tooltip_parts = [f"<b>X: {date_from_timestamp}</b>"]
        else:
            tooltip_parts = [f"<b>X: {x_val:.3f}</b>"]

        series_found = False

        # Iterate through all area series in the chart
        for series in self.view.chart().series():
            if not isinstance(series, QAreaSeries):
                continue

            if not series.isVisible():
                continue

            # Get upper and lower series
            upper_series = series.upperSeries()
            lower_series = series.lowerSeries()

            if not upper_series or not lower_series:
                continue

            # Get caches for both upper and lower series
            upper_cache = self.cache_manager.get_or_build(
                f"{upper_series.name()}_upper", upper_series
            )
            lower_cache = self.cache_manager.get_or_build(
                f"{lower_series.name()}_lower", lower_series
            )

            if not upper_cache or not lower_cache:
                continue

            # Find nearest points in both series
            upper_point = self.point_finder.find_nearest_1d(x_val, upper_cache)
            lower_point = self.point_finder.find_nearest_1d(x_val, lower_cache)

            if upper_point is None or lower_point is None:
                continue

            series_found = True

            # Calculate range
            range_val = upper_point.y() - lower_point.y()

            # Get color from area series
            color = series.color()
            color_hex = color.name()

            # Format tooltip for area series (range information)
            tooltip_parts.extend([
                f'<span style="color: {color_hex}">▰</span> '
                f'{series.name()}:',
                f'  Upper: {upper_point.y():.3f}',
                f'  Lower: {lower_point.y():.3f}',
                f'  Range: {range_val:.3f}'
            ])

        if not series_found:
            return None

        return "<br>".join(tooltip_parts)

    def get_series_bounds_at_x(self, x_val: float) -> Dict[
        str, Tuple[float, float]]:
        """
        Get upper and lower bounds for all area series at specific x value.

        Returns:
            Dict mapping series names to (upper, lower) tuples
        """
        bounds = {}

        for series in self.view.chart().series():
            if not isinstance(series, QAreaSeries) or not series.isVisible():
                continue

            upper_series = series.upperSeries()
            lower_series = series.lowerSeries()

            if not upper_series or not lower_series:
                continue

            upper_cache = self.cache_manager.get_or_build(
                f"{upper_series.name()}_upper", upper_series
            )
            lower_cache = self.cache_manager.get_or_build(
                f"{lower_series.name()}_lower", lower_series
            )

            if not upper_cache or not lower_cache:
                continue

            upper_point = self.point_finder.find_nearest_1d(x_val, upper_cache)
            lower_point = self.point_finder.find_nearest_1d(x_val, lower_cache)

            if upper_point and lower_point:
                bounds[series.name()] = (upper_point.y(), lower_point.y())

        return bounds

    def get_total_range_at_x(self, x_val: float) -> Optional[
        Tuple[float, float]]:
        """
        Calculate the overall min/max bounds across all area series at x.

        Useful for showing the total data envelope.
        """
        bounds = self.get_series_bounds_at_x(x_val)
        if not bounds:
            return None

        all_upper = [upper for upper, _ in bounds.values()]
        all_lower = [lower for _, lower in bounds.values()]

        return min(all_lower), max(all_upper)

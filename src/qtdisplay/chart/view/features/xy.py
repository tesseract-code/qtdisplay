import math
import timeit
from typing import Optional

from PyQt6.QtCharts import QAreaSeries, QLineSeries, QSplineSeries, \
    QScatterSeries
from PyQt6.QtCore import QPointF, QPoint

from qtdisplay.chart.view.features.strategy import \
    ChartInteractionStrategy, logger


class XYChartStrategy(ChartInteractionStrategy):
    """
    Strategy for line charts with optimized nearest-point lookups.

    Uses binary search on x-sorted data for O(log n) nearest neighbor queries.
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
        """Implement zoom with mouse wheel centered on cursor."""
        start_t = timeit.default_timer()
        plot_area = self.view.chart().plotArea()
        if not plot_area.contains(mouse_scene):
            return False
        logger.debug(
            f"{self.__class__.__name__}: time to get chart plot area {(timeit.default_timer() - start_t) * 1000} ms")
        map_start_t = timeit.default_timer()
        mouse_chart_before = self.view.chart().mapToValue(mouse_scene)
        logger.debug(f"{self.__class__.__name__}: time to map mouse scene: "
                     f"{(timeit.default_timer() - map_start_t) * 1000} ms")
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15

        axes = self.view.chart().axes()
        if len(axes) < 2:
            return False

        calc_start_t = timeit.default_timer()
        x_axis, y_axis = axes[0], axes[1]
        x_min, x_max = x_axis.min(), x_axis.max()
        y_min, y_max = y_axis.min(), y_axis.max()

        x_range, y_range = x_max - x_min, y_max - y_min
        x_frac = (
                         mouse_chart_before.x() - x_min) / x_range if x_range > 0 else 0.5
        y_frac = (
                         mouse_chart_before.y() - y_min) / y_range if y_range > 0 else 0.5

        new_x_range, new_y_range = x_range / factor, y_range / factor

        new_x_min = mouse_chart_before.x() - new_x_range * x_frac
        new_x_max = mouse_chart_before.x() + new_x_range * (1 - x_frac)
        new_y_min = mouse_chart_before.y() - new_y_range * y_frac
        new_y_max = mouse_chart_before.y() + new_y_range * (1 - y_frac)
        logger.debug(
            f"{self.__class__.__name__}: time to calculate new range {(timeit.default_timer() - calc_start_t) * 1000} ms")

        range_start_t = timeit.default_timer()
        x_axis.setRange(new_x_min, new_x_max)
        y_axis.setRange(new_y_min, new_y_max)
        logger.debug(f"{self.__class__.__name__}: time to set the range "
                     f"{(timeit.default_timer() - range_start_t) * 1000} ms")
        logger.debug(f"XYChartStrategy: time to handle wheel zoom: "
                     f"{(timeit.default_timer() - start_t) * 1000} ms")

        return True

    def handle_mouse_move_tooltip(self, chart_pos: QPointF,
                                  global_pos: QPoint) -> Optional[str]:
        """Generate tooltip showing nearest point values."""
        x_val = chart_pos.x()
        tooltip_parts = [f"<b>X: {x_val:.3f}, Y: {chart_pos.y():.3f}</b>"]

        series_found = False

        # Iterate through all line series in the chart
        for series in self.view.chart().series():
            # Skip area series - they have their own strategy
            if isinstance(series, QAreaSeries):
                continue

            if not isinstance(series, (QLineSeries, QSplineSeries)):
                continue

            if not series.isVisible():
                continue

            # Get or build cache
            cache = self.cache_manager.get_or_build(series.name(), series)
            if cache is None:
                continue

            series_found = True

            # Use optimized nearest point finder
            nearest = self.point_finder.find_nearest_1d(x_val, cache)

            if nearest is None:
                continue

            # Get color from series
            color = series.color()
            color_hex = color.name()
            tooltip_parts.append(
                f'<span style="color: {color_hex}">●</span> '
                f'{series.name()}: {nearest.y():.3f}'
            )

        if not series_found:
            return None

        return "<br>".join(tooltip_parts)


class ScatterChartStrategy(XYChartStrategy):
    """
    Strategy for scatter charts with 2D spatial search optimization.

    Uses binary search on x-axis followed by vectorized 2D distance
    calculation for efficient nearest-neighbor queries.
    """

    def handle_mouse_move_tooltip(self, chart_pos: QPointF,
                                  global_pos: QPoint) -> Optional[str]:
        """Generate tooltip showing nearest scatter point - NumPy vectorized."""
        x_val, y_val = chart_pos.x(), chart_pos.y()
        tooltip_parts = [f"<b>X: {x_val:.3f}, Y: {y_val:.3f}</b>"]

        # Track global minimum across all series
        global_min_distance = float('inf')
        global_nearest = None

        series_count = 0

        # Search all visible scatter series
        for series in self.view.chart().series():
            if not isinstance(series, QScatterSeries):
                continue

            if not series.isVisible():
                continue

            # Get or build cache
            cache = self.cache_manager.get_or_build(series.name(), series)
            if cache is None:
                continue

            series_count += 1

            # Use optimized 2D nearest point finder with vectorization
            nearest_point, min_dist_sq = self.point_finder.find_nearest_2d(
                x_val, y_val, cache
            )

            # Update global minimum
            if nearest_point and min_dist_sq < global_min_distance:
                global_min_distance = min_dist_sq
                global_nearest = (series, nearest_point)

        # Build tooltip with nearest point
        if global_nearest:
            series, point = global_nearest
            color = series.color()
            color_hex = color.name()
            tooltip_parts.append(
                f'<br> <span style="color: {color_hex}">● </span>'
                f' {series.name()}: '
                f'({point.x():.3f}, {point.y():.3f}) </br>'
            )

            # Log performance info
            distance = math.sqrt(global_min_distance)
            logger.debug(
                f"Scatter tooltip: searched {series_count} series, "
                f"nearest at distance {distance:.3f}"
            )
        elif series_count > 0:
            tooltip_parts.append("<i>No points nearby</i>")
        else:
            return None

        return "<br>".join(tooltip_parts)

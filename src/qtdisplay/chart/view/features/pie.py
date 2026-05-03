from typing import Optional

import math

from PyQt6.QtCharts import QPieSeries
from PyQt6.QtCore import QPointF, QPoint

from qtdisplay.chart.view.features.strategy import \
    ChartInteractionStrategy


class PieChartStrategy(ChartInteractionStrategy):
    """
    Strategy for pie charts with geometric slice detection.

    Uses angle calculations to determine which slice is under the cursor.
    """

    def supports_zoom(self) -> bool:
        return False

    def supports_crosshair(self) -> bool:
        return False

    def supports_tooltips(self) -> bool:
        return True

    def supports_panning(self) -> bool:
        return False

    def _find_slice_at_position(self, chart_pos: QPointF):
        """
        Find which pie slice is at the given position using angle calculation.

        Uses polar coordinates and cumulative angle tracking for accurate
        slice detection even with custom start angles and hole sizes.
        """
        # Get the pie series
        pie_series = None
        for series in self.view.chart().series():
            if isinstance(series, QPieSeries):
                pie_series = series
                break

        if not pie_series:
            return None

        # Get pie geometry
        plot_area = self.view.chart().plotArea()
        center_x = plot_area.center().x()
        center_y = plot_area.center().y()

        # Calculate distance from center
        dx = chart_pos.x() - center_x
        dy = chart_pos.y() - center_y
        distance = math.sqrt(dx * dx + dy * dy)

        # Estimate radius (pie takes up most of plot area)
        radius = min(plot_area.width(), plot_area.height()) / 2.0

        # Check if point is within the pie (respecting hole size)
        inner_radius = radius * pie_series.holeSize()
        if distance < inner_radius or distance > radius:
            return None

        # Calculate angle from center (in degrees, 0 = right, counterclockwise)
        angle = math.degrees(
            math.atan2(-dy, dx))  # Negative dy for screen coords
        if angle < 0:
            angle += 360

        # Adjust for pie start angle
        start_angle = pie_series.startAngle()
        adjusted_angle = (angle - start_angle) % 360

        # Find which slice contains this angle
        current_angle = 0.0
        for slice_obj in pie_series.slices():
            slice_angle = slice_obj.percentage() * 360.0

            if current_angle <= adjusted_angle < current_angle + slice_angle:
                return slice_obj

            current_angle += slice_angle

        return None

    def handle_mouse_move_tooltip(self, chart_pos: QPointF,
                                  global_pos: QPoint) -> Optional[str]:
        """Generate tooltip for pie slices showing label, value, and percentage."""
        slice_obj = self._find_slice_at_position(chart_pos)

        if not slice_obj:
            return None

        # Build tooltip with slice information
        label = slice_obj.label()
        value = slice_obj.value()
        percentage = slice_obj.percentage() * 100
        color = slice_obj.color()
        color_hex = color.name()

        tooltip_text = f"""
        <b>{label}</b><br>
        <span style="color: {color_hex}">● Value: {value:.2f}</span><br>
        <span style="color: {color_hex}">Percentage: {percentage:.1f}%</span>
        """

        return tooltip_text

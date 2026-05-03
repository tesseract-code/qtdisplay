from typing import Optional

from PyQt6.QtCore import QPointF, QPoint

from qtdisplay.chart.view.features.strategy import \
    ChartInteractionStrategy


class BarChartStrategy(ChartInteractionStrategy):
    """Strategy for bar charts - limited interactions."""

    def supports_zoom(self) -> bool:
        return True

    def supports_crosshair(self) -> bool:
        return False

    def supports_tooltips(self) -> bool:
        return True

    def supports_panning(self) -> bool:
        return True

    def handle_wheel_zoom(self, event, mouse_scene: QPointF) -> bool:
        """Bar charts can zoom, but typically only on value axis."""
        if self.view.is_real_time:
            return False

        plot_area = self.view.chart().plotArea()
        if not plot_area.contains(mouse_scene):
            return False

        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15

        axes = self.view.chart().axes()
        if len(axes) < 2:
            return False

        # Typically only zoom Y-axis for bar charts (value axis)
        y_axis = axes[1]
        y_min, y_max = y_axis.min(), y_axis.max()
        y_center = (y_min + y_max) / 2
        y_range = (y_max - y_min) / factor

        y_axis.setRange(y_center - y_range / 2, y_center + y_range / 2)

        return True

    def handle_mouse_move_tooltip(self, chart_pos: QPointF,
                                  global_pos: QPoint) -> Optional[str]:
        """Generate tooltip for bar charts."""
        return f"<b>Bar Chart</b><br>X: {chart_pos.x():.2f}<br>Y: {chart_pos.y():.2f}"

import logging

from PyQt6.QtCore import (QMargins)

from pycore.log.ctx import with_logger
from qtdisplay.chart.view.features.composite import CompositeChartStrategy

logger = logging.getLogger(__name__)

from typing import Optional, Dict

from PyQt6.QtCore import pyqtSignal, QPointF, QRectF, QPoint, Qt, QElapsedTimer
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QImage
from PyQt6.QtWidgets import (QSizePolicy)
from PyQt6.QtCharts import (QChartView, QAreaSeries)

from PyQt6.QtCharts import QChart, QAbstractSeries, QLineSeries, QScatterSeries, \
    QSplineSeries


def is_chart_empty(chart: QChart) -> bool:
    """
    Efficiently check if a QChart has any visible data.
    Optimized for common series types.

    Args:
        chart (QChart): The chart to check

    Returns:
        bool: True if chart is empty, False if it has data
    """
    if not chart:
        return True

    series_list = chart.series()
    if not series_list:
        return True

    for series in series_list:
        if not series.isVisible():
            continue

        # Fast path for common series types with count() method
        if isinstance(series, (QLineSeries, QScatterSeries, QSplineSeries)):
            if series.count() > 0:
                return False
        else:
            if isinstance(series, QAreaSeries):
                upper = series.upperSeries()
                lower = series.lowerSeries()
                if upper.count() >0 or lower.count()>0:
                    return False
            # Fallback for other series types
            if hasattr(series, 'count') and callable(series.count):
                if series.count() > 0:
                    return False
            elif hasattr(series, 'points') and callable(
                    getattr(series, 'points')):
                points = series.points()
                if points and len(points) > 0:
                    return False

    return True


@with_logger
class BaseChartView(QChartView):
    """
    Multi-series chart view with strategy pattern for interactions.

    Key Features:
    - Supports multiple series types on the same chart
    - Strategy-based interaction handling
    - Compatible series types share the same view/interaction behavior
    - Efficient rendering with configurable optimizations
    """

    # Signals
    mouseMoved = pyqtSignal(QPointF)
    crosshair_pos_changed = pyqtSignal(QPointF)
    zoomRectChanged = pyqtSignal(QRectF)
    zoomStarted = pyqtSignal()
    zoomFinished = pyqtSignal()

    def __init__(self):
        """Initialize chart view with primary series type.
        """
        super().__init__(QChart())
        self.strategy: Optional[CompositeChartStrategy] = (
            CompositeChartStrategy(self))

        # Common state
        self.is_real_time = False

        # Crosshair state (only used if supported)
        self.crosshair_scene_pos: Optional[QPointF] = None
        self.current_chart_pos: Optional[QPointF] = None
        self.show_crosshair = True

        # Zoom state (only used if supported)
        self.zoom_origin_scene: Optional[QPointF] = None
        self.zoom_current_scene: Optional[QPointF] = None
        self.is_zooming = False

        self._layout_state: Optional[Dict] = None

        self._paint_timer = QElapsedTimer()
        self._paint_duration = 0

        # Locked axis tracking (for real-time mode)
        self.locked_axis: Optional[str] = None
        self.locked_x_value: Optional[float] = None

        # Setup
        self.setMouseTracking(True)
        self._configure_rendering()
        self._configure_rubber_band()

    def _configure_rendering(self):
        """Configure rendering optimizations."""
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setOptimizationFlag(
            QChartView.OptimizationFlag.DontAdjustForAntialiasing, True)
        self.setOptimizationFlag(
            QChartView.OptimizationFlag.DontSavePainterState, True)

    def _configure_rubber_band(self):
        """Configure rubber band based on zoom support and real-time mode."""
        if self.strategy.supports_zoom() and not self.is_real_time:
            self.setRubberBand(QChartView.RubberBand.RectangleRubberBand)
        else:
            self.setRubberBand(QChartView.RubberBand.NoRubberBand)

    def register_series_type(self, series_type: QAbstractSeries.SeriesType):
        """Register a new series type with this view.

        Note: Strategy is determined by first series type and doesn't change.
        This method tracks which types are present for informational purposes.

        Args:
            series_type: Series type being added
        """
        self.series_types.add(series_type)

    def unregister_series_type(self, series_type: QAbstractSeries.SeriesType):
        """Unregister a series type when the last series of that type is removed.

        Args:
            series_type: Series type being removed
        """
        if series_type in self.series_types:
            self.series_types.discard(series_type)

    def _ensure_state_saved(self):
        """Ensure state is saved before first modification."""
        if self._layout_state is None:
            self._save_layout_state()

    def _save_layout_state(self):
        """Save the current chart state for later restoration."""
        layout = self.chart().layout()
        legend = self.chart().legend()

        self._layout_state = {
            'title': self.chart().title(),
            'chart_margins': self.chart().margins(),
            'chart_layout_margins': layout.getContentsMargins(),
            'legend_visible': legend.isVisible(),
            'legend_margins': legend.getContentsMargins(),
            'view_margins': self.contentsMargins(),
            'axes_state': [
                {
                    'visible': axis.isVisible(),
                    'labels_visible': axis.labelsVisible(),
                    'title': axis.titleText()
                } for axis in self.chart().axes()
            ]
        }

    def set_real_time_mode(self, is_real_time: bool):
        """Set real-time mode.

        Args:
            is_real_time: Whether to enable real-time mode
        """
        self.is_real_time = is_real_time
        if self.strategy:
            self._configure_rubber_band()

    def set_crosshair_visible(self, visible: bool):
        """Toggle crosshair visibility.

        Args:
            visible: Whether crosshair should be visible
        """
        self.show_crosshair = visible
        self.viewport().update()

    def set_minimal_mode(self):
        """Configure chart for minimal/embedded display with no decorations."""
        self._ensure_state_saved()

        # Remove title
        self.chart().setTitle("")

        # Hide and clear all axes
        for axis in self.chart().axes():
            axis.setVisible(False)
            axis.setLabelsVisible(False)
            axis.setTitleText("")

        # Hide legend
        self.chart().legend().setVisible(False)
        self.chart().legend().setContentsMargins(0, 0, 0, 0)

        # Remove all margins and padding
        self.chart().setMargins(QMargins(0, 0, 0, 0))
        self.chart().layout().setContentsMargins(0, 0, 0, 0)

        # Configure chart view
        self.setContentsMargins(0, 0, 0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

        # Reset zoom and update
        self.chart().zoomReset()
        self.chart().update(QRectF(self.rect()))

    def set_normal_mode(self):
        """Restore chart to normal display mode with all decorations."""
        if self._layout_state is None:
            return  # Nothing to restore

        state = self._layout_state

        # Restore title
        self.chart().setTitle(state['title'])

        # Restore chart margins
        self.chart().setMargins(state['chart_margins'])

        # Restore layout margins
        margins = state['chart_layout_margins']
        self.chart().layout().setContentsMargins(
            margins[0], margins[1], margins[2], margins[3]
        )

        # Restore legend
        self.chart().legend().setVisible(state['legend_visible'])
        margins = state['legend_margins']
        self.chart().legend().setContentsMargins(
            margins[0], margins[1], margins[2], margins[3]
        )

        # Restore view margins
        margins = state['view_margins']
        self.setContentsMargins(margins)

        # Restore axis states
        axes = self.chart().axes()
        for i, axis in enumerate(axes):
            if i < len(state['axes_state']):
                axis_state = state['axes_state'][i]
                axis.setVisible(axis_state['visible'])
                axis.setLabelsVisible(axis_state['labels_visible'])
                axis.setTitleText(axis_state['title'])

        # Update chart
        self.chart().update(QRectF(self.rect()))

    def wheelEvent(self, event):
        """Handle mouse wheel - delegates to strategy."""
        if self.strategy:
            if not self.strategy.supports_zoom():
                event.ignore()
                return

            mouse_scene = QPointF(event.position())

            if self.strategy.handle_wheel_zoom(event, mouse_scene):
                event.accept()
                self.viewport().update()
                return

        event.ignore()

    def mousePressEvent(self, event):
        """Handle mouse press for zoom rectangle."""
        if self.strategy:
            if (event.button() == Qt.MouseButton.LeftButton and
                    self.strategy.supports_zoom() and
                    not self.is_real_time):

                pos_f = QPointF(event.position())
                plot_area = self.chart().plotArea()

                if plot_area.contains(pos_f):
                    self.zoom_origin_scene = pos_f
                    self.is_zooming = True
                    self.zoomStarted.emit()
                    event.accept()
                    return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse move for crosshairs, tooltips, and zoom rectangle."""
        if self.strategy:
            pos_f = QPointF(event.position())
            plot_area = self.chart().plotArea()

            if plot_area.contains(pos_f):
                # Update crosshair position if supported
                if self.strategy.supports_crosshair():
                    self.crosshair_scene_pos = pos_f
                    chart_pos = self.chart().mapToValue(pos_f)
                    self.current_chart_pos = chart_pos
                    self.mouseMoved.emit(chart_pos)
                    self.crosshair_pos_changed.emit(chart_pos)

                # Update tooltip if supported
                if self.strategy.supports_tooltips() and self.current_chart_pos:
                    tooltip_text = self.strategy.handle_mouse_move_tooltip(
                        self.current_chart_pos,
                        event.globalPosition().toPoint()
                    )
                    if tooltip_text:
                        self.strategy.show_tooltip(tooltip_text,
                                                   event.globalPosition().toPoint())

                # Update zoom rectangle if zooming
                if self.strategy.supports_zoom() and not self.is_real_time and self.is_zooming and self.zoom_origin_scene:
                    self.zoom_current_scene = pos_f
                    chart_origin = self.chart().mapToValue(
                        self.zoom_origin_scene)
                    chart_current = self.chart().mapToValue(
                        self.zoom_current_scene)
                    chart_rect = QRectF(chart_origin,
                                        chart_current).normalized()
                    self.zoomRectChanged.emit(chart_rect)
            else:
                self.crosshair_scene_pos = None
                self.current_chart_pos = None
                if self.strategy.supports_crosshair():
                    self.crosshair_pos_changed.emit(QPointF())

            self.viewport().update()
        event.accept()

    def mouseReleaseEvent(self, event):
        """Handle mouse release to finalize zoom."""
        if self.strategy:
            if (event.button() == Qt.MouseButton.LeftButton and
                    self.is_zooming and
                    self.strategy.supports_zoom() and
                    not self.is_real_time):

                if self.zoom_origin_scene and self.zoom_current_scene:
                    chart_origin = self.chart().mapToValue(
                        self.zoom_origin_scene)
                    chart_current = self.chart().mapToValue(
                        self.zoom_current_scene)

                    dx = abs(chart_current.x() - chart_origin.x())
                    dy = abs(chart_current.y() - chart_origin.y())

                    axes = self.chart().axes()
                    if len(axes) >= 2:
                        x_axis, y_axis = axes[0], axes[1]
                        x_range = x_axis.max() - x_axis.min()
                        y_range = y_axis.max() - y_axis.min()

                        # Only zoom if selection is significant
                        if dx > x_range * 0.02 and dy > y_range * 0.02:
                            rect = QRectF(chart_origin,
                                          chart_current).normalized()
                            x_axis.setRange(rect.left(), rect.right())
                            y_axis.setRange(rect.top(), rect.bottom())

                self.is_zooming = False
                self.zoom_origin_scene = None
                self.zoom_current_scene = None
                self.zoomFinished.emit()
                self.viewport().update()
                event.accept()
                return
        return super().mouseReleaseEvent(event)





























    def mouseDoubleClickEvent(self, event):
        """Double-click to reset zoom."""
        if self.strategy:
            if (event.button() == Qt.MouseButton.LeftButton and
                    self.strategy.supports_zoom() and
                    not self.is_real_time):
                self.chart().zoomReset()
                event.accept()
                self.viewport().update()
                return
        return super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event):
        """Clear crosshair when mouse leaves view."""
        if self.strategy:
            self.crosshair_scene_pos = None
            self.current_chart_pos = None
            if self.strategy.supports_crosshair():
                self.crosshair_pos_changed.emit(QPointF())
            self.viewport().update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        """Paint chart with overlays."""
        super().paintEvent(event)

        plot_area = self.chart().plotArea()
        if not plot_area.isValid():
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(plot_area.toRect())

        if not is_chart_empty(self.chart()) and self.strategy:
            # Draw crosshair if supported and enabled
            if (self.show_crosshair and
                    self.strategy.supports_crosshair() and
                    self.crosshair_scene_pos and
                    plot_area.contains(self.crosshair_scene_pos)):
                self._draw_crosshair(painter, plot_area)

            # Draw zoom rectangle if zooming
            if (self.strategy.supports_zoom() and
                    not self.is_real_time and
                    self.zoom_origin_scene and
                    self.zoom_current_scene and
                    self.is_zooming):
                self._draw_zoom_rect(painter)

            # Draw chart-specific overlays
            self.strategy.draw_overlay(painter, plot_area)

    def _draw_crosshair(self, painter: QPainter, plot_area: QRectF):
        """Draw crosshair lines."""
        painter.setPen(QPen(QColor("gray"), 1, Qt.PenStyle.DashLine))

        x_pos = self.crosshair_scene_pos.x()
        y_pos = self.crosshair_scene_pos.y()

        # Draw vertical line (unless X axis is locked)
        if self.locked_axis != 'y':
            painter.drawLine(int(x_pos), int(plot_area.top()),
                             int(x_pos), int(plot_area.bottom()))

        # Draw horizontal line (unless Y axis is locked)
        if self.locked_axis != 'x':
            painter.drawLine(int(plot_area.left()), int(y_pos),
                             int(plot_area.right()), int(y_pos))

    def _draw_zoom_rect(self, painter: QPainter):
        """Draw zoom selection rectangle."""
        scene_rect = QRectF(self.zoom_origin_scene,
                            self.zoom_current_scene).normalized()

        painter.setPen(
            QPen(QColor(66, 133, 244, 220), 2, Qt.PenStyle.SolidLine))
        painter.setBrush(QColor(66, 133, 244, 40))
        painter.drawRect(scene_rect)

        # Draw delta label
        chart_origin = self.chart().mapToValue(self.zoom_origin_scene)
        chart_current = self.chart().mapToValue(self.zoom_current_scene)

        dx = abs(chart_current.x() - chart_origin.x())
        dy = abs(chart_current.y() - chart_origin.y())

        font = QFont()
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)

        label_text = f"ΔX: {dx:.2f}  ΔY: {dy:.2f}"
        text_rect = painter.fontMetrics().boundingRect(label_text)
        text_rect.moveCenter(QPoint(int(scene_rect.center().x()),
                                    int(scene_rect.top())))

        bg_rect = text_rect.adjusted(-5, -2, 5, 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(66, 133, 244, 200))
        painter.drawRoundedRect(bg_rect, 3, 3)

        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label_text)

    def capture_snapshot(self) -> QImage:
        """Capture the chart view as an image."""
        return self.grab().toImage()

# ============================================================================
# Plot Widget - Container for chart view and controls
# ============================================================================

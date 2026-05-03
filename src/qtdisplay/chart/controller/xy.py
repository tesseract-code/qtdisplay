from PyQt6.QtCharts import (QLineSeries, QScatterSeries, QSplineSeries,
                            QLegend, QAbstractSeries)
from PyQt6.QtCore import Qt
from PyQt6.QtCore import pyqtSlot
from PyQt6.QtGui import QPen
from PyQt6.QtWidgets import QSlider, QSpacerItem, QSizePolicy

from pycore.log.ctx import with_logger
from qtcore.reference import has_qt_cpp_binding
from qtdisplay.chart.config import (PlotConfig,
                                    AxesDisplaySettings)
from qtdisplay.chart.controller.base import BaseChartController
from qtdisplay.chart.model.data import points
from qtdisplay.chart.model.data.preprocess import (
    preprocess_timeseries)
from qtdisplay.chart.view.plot import PlotWidget
from qtgui.slider import RangeSlider


@with_logger
class XYChartController(BaseChartController):
    """
    Controller for XY-axis charts supporting Line, Scatter, and Spline series.
    """

    def __init__(self,
                 config: PlotConfig,
                 parent=None):
        """Initialize XY chart controller.

        Args:
            config: Plot-level configuration
            view: Optional pre-configured view widget
            parent: Qt parent object
        """
        view = PlotWidget(config)
        super().__init__(config=config, view=view, parent=parent)

        self._setup_range_controls(self.axes_config or AxesDisplaySettings())

    def _wire_range_control_signals(self) -> None:
        self.x_range_control.rangeChanged.connect(
            self._on_x_range_changed)
        self.x_range_control.sliderPressed.connect(
            self._on_slider_pressed)
        self.x_range_control.sliderReleased.connect(
            self._on_slider_released)
        self.y_range_control.rangeChanged.connect(
            self._on_y_range_changed)
        self.y_range_control.sliderPressed.connect(
            self._on_slider_pressed)
        self.y_range_control.sliderReleased.connect(
            self._on_slider_released)

    @pyqtSlot(str)
    def _on_series_added(self, series_name: str):
        """Create and configure a new XY series in the chart.

        Creates appropriate series type (Line, Scatter, or Spline) based on
        the SeriesConfig, applies styling, and attaches to appropriate axes
        based on alignment settings.

        Args:
            series_name: Name of the series to create
        """
        config = self.series_configs[series_name]
        series_type = config.series_type
        axes_config = config.axes_config

        # Create appropriate series type
        if series_type == QAbstractSeries.SeriesType.SeriesTypeScatter:
            series = QScatterSeries(self.plot.view.chart())
            series.setMarkerShape(QScatterSeries.MarkerShape.MarkerShapeCircle)
            series.setMarkerSize(10)
        elif series_type == QAbstractSeries.SeriesType.SeriesTypeSpline:
            series = QSplineSeries(self.plot.view.chart())
        else:  # Default to line
            series = QLineSeries(self.plot.view.chart())

        series.setName(series_name)

        # Configure pen and rendering
        if series_type == QAbstractSeries.SeriesType.SeriesTypeSpline:
            pen = QPen(Qt.PenStyle.SolidLine)
            pen.setWidth(2)
            series.setPen(pen)
            series.setUseOpenGL(True)
            series.setPointsVisible(False)
        elif series_type == QAbstractSeries.SeriesType.SeriesTypeScatter:
            pen = QPen(Qt.PenStyle.NoPen)
            series.setPen(pen)
            series.setUseOpenGL(False)  # OpenGL can cause issues with scatter
            series.setPointsVisible(True)
        else:  # Line
            pen = QPen(Qt.PenStyle.SolidLine)
            pen.setWidth(2)
            series.setPen(pen)
            series.setUseOpenGL(True)
            series.setPointsVisible(False)

        # Use model's auto-generated color
        model = self.models[series_name]
        color = model.get_series_color(series_name)
        if color:
            series.setColor(color)

        # Add to chart
        if has_qt_cpp_binding(series):
            self.plot.view.chart().addSeries(series)

            # Attach to appropriate axes based on alignment settings

            x_axis = self.axes_map.get(axes_config.axisX_alignment)
            y_axis = self.axes_map.get(axes_config.axisY_alignment)

            if x_axis:
                series.attachAxis(x_axis)
            else:
                self._logger.warning(f"X axis not found for key: "
                                     f"{axes_config.axisX_alignment}")

            if y_axis:
                series.attachAxis(y_axis)
            else:
                self._logger.warning(
                    f"Y axis not found for key: {axes_config.axisY_alignment}")

            self.series_map[series_name] = series

            # Enable legend click for visibility toggle
            markers = self.plot.chart.legend().markers(series)
            if markers:
                markers[0].setShape(QLegend.MarkerShape.MarkerShapeCircle)
                markers[0].clicked.connect(
                    lambda: self._toggle_series_visibility(series_name))

    def _update_series_data(self, series_name: str):
        """Update data for a specific XY series.

        Handles data preprocessing, downsampling, and efficient Qt series updates.

        Args:
            series_name: Name of the series to update
        """
        if series_name not in self.models or series_name not in self.series_map:
            return

        model = self.models[series_name]
        series = self.series_map[series_name]

        # Check if this series has dirty data
        dirty_series = model.get_dirty_series()
        if series_name not in dirty_series:
            return

        # Get data from model
        vector = model.get_series_data(series_name)
        if not vector or len(vector) == 0:
            return

        points_count = len(vector)

        # Disable updates during data replacement for performance
        self.plot.view.setUpdatesEnabled(False)

        try:
            x_arr, y_arr = vector.to_arrays()

            # Apply preprocessing if configured
            if self.config.data_process_config:
                x_arr, y_arr, _ = preprocess_timeseries(
                    x_arr, y_arr, self.config.data_process_config)

            # Apply downsampling if configured
            if (self.config.data_process_config and
                    self.config.data_process_config.downsample and
                    self.config.data_process_config.downsample_factor > 1):
                downsampled_points = points.downsample_to_qpointf(
                    x_arr, y_arr,
                    (points_count //
                     self.config.data_process_config.downsample_factor))
                series.replace(downsampled_points)
            else:
                series.replace(vector.to_qpointf())

        finally:
            # Re-enable updates
            self.plot.view.setUpdatesEnabled(True)

        # Schedule data table update
        # QTimer.singleShot(30, self._update_data_table)

    def _update_axes_range(self, x_min: float, x_max: float,
                           y_min: float, y_max: float):
        """Update axis ranges with series-type-specific padding.

        Different series types need different padding:
        - Splines: More Y padding (12%) due to curve interpolation overshooting
        - Scatter: Minimal padding (3%) since points don't extend beyond data
        - Line: No extra padding, just data range

        Args:
            x_min, x_max: X axis data range
            y_min, y_max: Y axis data range
        """
        x_min_adj = x_min
        x_max_adj = x_max
        y_min_adj = y_min
        y_max_adj = y_max

        # Ensure valid ranges (min < max)
        if x_min_adj >= x_max_adj:
            x_min_adj = x_min - 1.0
            x_max_adj = x_max + 1.0

        if y_min_adj >= y_max_adj:
            y_min_adj = y_min - 1.0
            y_max_adj = y_max + 1.0

        if bool(self.axes_display_settings.keys()):
            self.update_axes_range_with_padding(x_min_adj, x_max_adj,
                                                y_min_adj, y_max_adj)
        else:
            # Apply the adjusted ranges with no padding
            self._on_x_range_changed(x_min_adj, x_max_adj)
            self._on_y_range_changed(y_min_adj, y_max_adj)

        if (hasattr(self.config, 'is_real_time') and not
        self.config.is_real_time):
            x_axis = (
                self.get_axes_by_orientation(Qt.Orientation.Horizontal)[0])
            y_axis = (
                self.get_axes_by_orientation(Qt.Orientation.Vertical)[0])

            self.x_range_control.setRange(x_axis.min(), x_axis.max())
            self.x_range_control.setValues(x_axis.min(), x_axis.max())
            self.y_range_control.setRange(y_axis.min(), y_axis.max())
            self.y_range_control.setValues(y_axis.min(), y_axis.max())

    def _on_tick_changed(self, x_tick_count: int, y_tick_count: int):
        """Handle tick count changes for X and Y axes.

        Args:
            x_tick_count: Number of ticks on X axis
            y_tick_count: Number of ticks on Y axis
        """
        self.plot.axis_x.setTickCount(x_tick_count)
        self.plot.axis_y.setTickCount(y_tick_count)

        if (hasattr(self.config, 'is_real_time') and not
        self.config.is_real_time):
            self.x_range_control.setTickCount(x_tick_count)
            self.y_range_control.setTickCount(y_tick_count)
            self.x_range_control.update()
            self.y_range_control.update()

    def _on_x_range_changed(self, min_val: float, max_val: float):
        """Handle X range slider changes.

        Args:
            min_val: Minimum X value
            max_val: Maximum X value
        """
        if max_val > min_val:
            self.plot.axis_x.setRange(min_val, max_val)
            self.plot.view.viewport().update()

    def _on_y_range_changed(self, min_val: float, max_val: float):
        """Handle Y range slider changes.

        Args:
            min_val: Minimum Y value
            max_val: Maximum Y value
        """
        if max_val > min_val:
            self.plot.axis_y.setRange(min_val, max_val)
            # self.plot.view.viewport().update()

    def _should_have_range_controls(self) -> bool:
        """Determine if range controls should be shown."""
        return (not getattr(self.config, 'is_real_time', False) and
                self.plot.view.strategy.supports_zoom())

    def _setup_range_controls(self, axes_settings: 'AxesSettings'):
        """Setup range slider controls for axes.

        Args:
            axes_settings: Axes configuration
        """
        if not getattr(self.config, 'is_real_time', False):
            # Y-axis control (vertical)
            self.y_range_control = RangeSlider(Qt.Orientation.Vertical)
            self.y_range_control.setTickPosition(
                QSlider.TickPosition.TicksBothSides)
            if axes_settings.axis_tick_count:
                self.y_range_control.setTickCount(axes_settings.axis_tick_count)

            self.y_range_control.hide()
            self.y_range_control.setLabelsVisible(False)

            # X-axis control (horizontal)
            self.x_range_control = RangeSlider(Qt.Orientation.Horizontal)
            self.x_range_control.setTickPosition(
                QSlider.TickPosition.TicksBothSides)
            if axes_settings.axis_tick_count:
                self.x_range_control.setTickCount(axes_settings.axis_tick_count)
            self.x_range_control.hide()
            self.x_range_control.setLabelsVisible(False)

            # Add to layout with spacers
            y_spacer = QSpacerItem(24, 0, QSizePolicy.Policy.Fixed,
                                   QSizePolicy.Policy.Expanding)
            self.plot.chart_layout.addItem(y_spacer, 0, 1)

            x_spacer = QSpacerItem(0, 24, QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Fixed)
            self.plot.chart_layout.addItem(x_spacer, 1, 2)

            # Add controls to layout
            self.plot.chart_layout.addWidget(self.y_range_control, 0, 0)
            self.plot.chart_layout.addWidget(self.x_range_control, 2, 2)
            self._wire_range_control_signals()

    @pyqtSlot(bool)
    def _on_full_view_toggled(self, checked: bool) -> None:
        super()._on_full_view_toggled(checked)
        if not getattr(self.config, 'is_real_time', False):
            self.x_range_control.setVisible(checked)
            self.y_range_control.setVisible(checked)

    @pyqtSlot()
    def _on_slider_pressed(self) -> None:
        """Pause updates when user interacts with sliders."""
        self.user_is_zooming = True

    @pyqtSlot()
    def _on_slider_released(self) -> None:
        """Resume updates when user releases slider."""
        self.user_is_zooming = False


@with_logger
class ScatterChartController(XYChartController):
    """"""
    pass


@with_logger
class LineChartController(XYChartController):
    """Controller for line chart type.

    Handles line series data visualization with support for multiple series,
    real-time updates, and interactive features.
    """
    pass


@with_logger
class SplineChartController(XYChartController):
    pass

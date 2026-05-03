from typing import Optional, Dict

from PyQt6.QtCharts import (QChart, QAbstractAxis, QAbstractSeries,
                            QDateTimeAxis, QBarCategoryAxis, QValueAxis)
from PyQt6.QtCore import (Qt, QObject, QEvent,
                          QTimer)
from PyQt6.QtWidgets import (QFrame, QWidget, QVBoxLayout, QGridLayout,
                             QSplitter)

from qtdisplay.chart.config import (PlotConfig,
                                    ChartDisplaySettings)
from qtdisplay.chart.view.base import BaseChartView
from qtdisplay.chart.view.table import DataTableWidget
from qtdisplay.chart.view.toolbar import ChartToolBar
from qtgui.slider import RangeSlider


class PlotWidget(QFrame):
    """
    Container widget for chart view with toolbar, range controls, and data table.

    Now supports multiple series with different types on the same chart.
    Axes are configured based on the first series added (primary axes).
    """

    def __init__(self,
                 config: PlotConfig,
                 parent: Optional[QWidget] = None):
        """Initialize plot widget.

        Args:
            config: Plot-level configuration
            parent: Parent widget
        """
        super().__init__(parent)
        self.config = config
        self.settings: Optional[ChartDisplaySettings] = None

        # Chart UI elements
        self.view: Optional[BaseChartView] = None
        self.chart: Optional[QChart] = None
        self.axis_x: Optional[QAbstractAxis] = None
        self.axis_y: Optional[QAbstractAxis] = None

        # Track series and their axes attachments
        self.series_map: Dict[str, QAbstractSeries] = {}
        self.series_axes: Dict[
            str, tuple] = {}  # series_name -> (x_axis, y_axis)

        # Controls
        self.x_range_control: Optional[RangeSlider] = None
        self.y_range_control: Optional[RangeSlider] = None
        self.toolbar: Optional[ChartToolBar] = None

        # Data table
        self.data_table: Optional[DataTableWidget] = None

        # UI state
        self.show_range_controls = True
        self.show_coordinates = True
        self.show_data_table = False
        self.is_paused = False
        self.user_is_zooming = False

        # Axes initialized flag
        self._axes_initialized = False

        # Initialize UI (chart view only, axes created when first series added)
        self._setup_ui()

    def _setup_ui(self):
        """Set up the main UI layout."""
        QVBoxLayout(self)
        self.layout().setSpacing(0)
        self.layout().setContentsMargins(0, 0, 0, 0)

        # Grid layout for chart area
        chart_area = QWidget()
        chart_layout = QGridLayout(chart_area)
        chart_layout.setSpacing(0)
        chart_layout.setContentsMargins(0, 0, 0, 0)

        # Create empty chart view (axes added later)
        self._setup_chart_view()
        chart_layout.addWidget(self.view, 0, 2)
        chart_layout.setRowStretch(0, 1)
        chart_layout.setColumnStretch(2, 1)

        # Toolbar as floating overlay on chart view (using local coordinates)
        self.toolbar = ChartToolBar(parent=self.view)

        # Install event filter on view to detect resize, show, and move events
        self.view.installEventFilter(self)

        # Placeholders for range controls (created when axes are initialized)
        self.chart_layout = chart_layout

        # Data table setup in splitter
        self.data_table = DataTableWidget()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(chart_area)
        splitter.addWidget(self.data_table)
        splitter.setSizes([800, 200])
        self.data_table.hide()

        self.layout().addWidget(splitter, 1)

        # Position and show toolbar after a short delay to ensure view is ready
        QTimer.singleShot(0, self._position_and_show_toolbar)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Filter events to reposition toolbar.

        Args:
            obj: The object that received the event
            event: The event

        Returns:
            True if event was handled, False otherwise
        """
        if obj == self.view:
            event_type = event.type()
            if event_type in (QEvent.Type.Resize,
                              QEvent.Type.Show,
                              QEvent.Type.Move,
                              QEvent.Type.Paint):
                self._position_toolbar()

        return super().eventFilter(obj, event)

    def _position_and_show_toolbar(self):
        """Position and show the toolbar."""
        self._position_toolbar()
        self.toolbar.show()
        self.toolbar.raise_()

    def _position_toolbar(self):
        """Position toolbar at top right corner of chart view using local coordinates."""
        if not self.view or not self.toolbar:
            return

        # Use local coordinates relative to the view
        view_width = self.view.width()

        # Calculate toolbar position (top right with some padding)
        padding = 10
        toolbar_width = self.toolbar.sizeHint().width()

        x = view_width - toolbar_width - padding
        y = padding

        # Move toolbar using local coordinates
        self.toolbar.move(x, y)

    def resizeEvent(self, event):
        """Handle widget resize events to reposition toolbar.

        Args:
            event: The resize event
        """
        super().resizeEvent(event)
        if self.toolbar and self.toolbar.isVisible():
            self._position_toolbar()

    def showEvent(self, event):
        """Handle widget show events to reposition toolbar.

        Args:
            event: The show event
        """
        super().showEvent(event)
        if self.toolbar:
            self._position_toolbar()

    def set_chart_settings(self, chart_settings: ChartDisplaySettings):
        self.settings = chart_settings
        # Apply basic configuration
        self.chart.setTitle(
            self.settings.title if hasattr(self.settings, 'title') else "")

        if hasattr(self.settings, 'theme'):
            self.chart.setTheme(self.settings.theme)

        if hasattr(self.settings, 'show_legend'):
            self.chart.legend().setVisible(self.settings.show_legend)

        self.chart.legend().setAlignment(self.settings.legend_alignment)
        #
        if hasattr(self.settings, 'animated'):
            animation = (QChart.AnimationOption.AllAnimations
                         if self.settings.animated
                         else QChart.AnimationOption.NoAnimation)
            self.chart.setAnimationOptions(animation)

        self.chart.setDropShadowEnabled(False)

        # Configure view rendering
        # if hasattr(self.settings, 'antialiasing'):
        #     with QSignalBlocker(self.view):
        #         self.view.setRenderHints(QPainter.RenderHint.Antialiasing)

        if hasattr(self.settings, 'show_trackline'):
            self.view.set_crosshair_visible(self.settings.show_trackline)

        if hasattr(self.settings, "show_background"):
            self.view.chart().setBackgroundVisible(
                self.settings.show_background)

    def _setup_chart_view(self):
        """Create the chart view without axes (axes created when first series added)."""
        # Create view with a placeholder series type (will be updated)
        self.view = BaseChartView()
        self.chart = self.view.chart()

        if self.settings:
            self.set_chart_settings(self.settings)

        if hasattr(self.config, 'is_real_time'):
            self.view.set_real_time_mode(self.config.is_real_time)

    def initialize_axes(self, axes_settings: 'AxesSettings'):
        """Initialize axes based on first series configuration.

        Args:
            axes_settings: Axes configuration from first SeriesConfig
        """
        if self._axes_initialized:
            return

        # Create appropriate axis types
        if axes_settings.axisX_type == QAbstractAxis.AxisType.AxisTypeDateTime:
            self.axis_x = QDateTimeAxis()
            self.axis_x.setFormat("HH:mm:ss")
        elif axes_settings.axisX_type == QAbstractAxis.AxisType.AxisTypeBarCategory:
            self.axis_x = QBarCategoryAxis()
        else:
            self.axis_x = QValueAxis()
            if hasattr(self.config, 'precision'):
                self.axis_x.setLabelFormat(f"%.{self.config.precision}f")

        self.axis_y = QValueAxis()
        if hasattr(self.config, 'precision'):
            self.axis_y.setLabelFormat(f"%.{self.config.precision}f")

        # Configure axes
        if axes_settings.axisX_title:
            x_label = (
                f"{axes_settings.axisX_title} ({axes_settings.axisX_unit})"
                if axes_settings.axisX_unit else axes_settings.axisX_title)
            self.axis_x.setTitleText(x_label)

        if axes_settings.axisY_title:
            y_label = (
                f"{axes_settings.axisY_title} ({axes_settings.axisY_unit})"
                if axes_settings.axisY_unit else axes_settings.axisY_title)
            self.axis_y.setTitleText(y_label)

        # Set tick counts
        if axes_settings.axisX_tick_count and isinstance(self.axis_x,
                                                         QValueAxis):
            self.axis_x.setTickCount(axes_settings.axisX_tick_count)
        if axes_settings.axisY_tick_count:
            self.axis_y.setTickCount(axes_settings.axisY_tick_count)

        # Set grid visibility
        if hasattr(self.config, 'show_grid'):
            self.axis_x.setGridLineVisible(self.config.show_grid)
            self.axis_y.setGridLineVisible(self.config.show_grid)

        # Set initial ranges
        if not axes_settings.axisX_auto_range and axes_settings.axisX_min is not None:
            self.axis_x.setRange(axes_settings.axisX_min,
                                 axes_settings.axisX_max)
        if not axes_settings.axisY_auto_range and axes_settings.axisY_min is not None:
            self.axis_y.setRange(axes_settings.axisY_min,
                                 axes_settings.axisY_max)

        # Add axes to chart
        self.chart.addAxis(self.axis_x, Qt.AlignmentFlag.AlignBottom)
        self.chart.addAxis(self.axis_y, Qt.AlignmentFlag.AlignLeft)

        # Create range controls if applicable
        if self._should_have_range_controls():
            self._setup_range_controls(axes_settings)

        self._axes_initialized = True

    def register_series(self, series_name: str, series: QAbstractSeries):
        """Register a series with the plot widget.

        Args:
            series_name: Name of the series
            series: Qt series object
        """
        self.series_map[series_name] = series

        # Attach to default axes if available
        if self.axis_x and self.axis_y and hasattr(series, 'attachAxis'):
            series.attachAxis(self.axis_x)
            series.attachAxis(self.axis_y)
            self.series_axes[series_name] = (self.axis_x, self.axis_y)

        # Update view's registered series types
        self.view.register_series_type(series.type())

    def unregister_series(self, series_name: str):
        """Unregister a series from the plot widget.

        Args:
            series_name: Name of the series to unregister
        """
        if series_name in self.series_map:
            series = self.series_map[series_name]

            # Detach from axes
            if series_name in self.series_axes:
                x_axis, y_axis = self.series_axes[series_name]
                if hasattr(series, 'detachAxis'):
                    series.detachAxis(x_axis)
                    series.detachAxis(y_axis)
                del self.series_axes[series_name]

            # Remove from map
            del self.series_map[series_name]

            # Check if this was the last series of its type
            series_type = series.type()
            remaining_types = {s.type() for s in self.series_map.values()}
            if series_type not in remaining_types:
                self.view.unregister_series_type(series_type)

    def has_mouse_tracking_controls(self) -> bool:
        """Check if mouse tracking is supported.

        Returns:
            True if current chart type supports mouse tracking
        """
        return self.view.strategy.supports_crosshair()

    def get_chart(self) -> QChart:
        """Get the underlying QChart object.

        Returns:
            The QChart instance
        """
        return self.chart

    def get_view(self) -> BaseChartView:
        """Get the chart view.

        Returns:
            The BaseChartView instance
        """
        return self.view

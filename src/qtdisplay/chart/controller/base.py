"""
Refactored BaseChartController - Clean Separation of Immutable Config and Mutable Settings
===========================================================================================

Key Design Principles:
----------------------
1. IMMUTABLE CONFIG (AxesConfig, SeriesConfig, PlotConfig):
   - Structural settings set at initialization
   - Define what kind of chart, axes types, data types
   - Should not change during chart lifetime

2. MUTABLE SETTINGS (AxesDisplaySettings, SeriesDisplaySettings, ChartDisplaySettings):
   - Visual/display properties modifiable by user
   - Can be changed via settings dialog
   - Affect appearance, not structure

Storage Pattern:
---------------
- axes_map: Dict[str, QAbstractAxis] - Maps "x", "y_left", "y_right" to Qt axis objects
- axes_display_settings: Dict[str, AxesDisplaySettings] - Display settings per axis
- series_configs: Dict[str, SeriesConfig] - Immutable config per series
- series_display_settings: Dict[str, SeriesDisplaySettings] - Mutable display per series
"""

import abc
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import List, Set, Iterator, Optional

import PyQt6
from PyQt6.QtCharts import QAbstractSeries, QAbstractAxis
from PyQt6.QtCore import pyqtSlot, QPointF, QObject, QTimer, Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from pycore.log.ctx import with_logger
from qtcore.meta import QABCMeta
from qtdisplay.chart.config import (PlotConfig, ChartDisplaySettings,
                                    SeriesConfig, SeriesDisplaySettings,
                                    AxesConfig)
from qtdisplay.chart.controller.mixins.axes import AxesMixin
from qtdisplay.chart.controller.mixins.data_table import DataTableMixin
from qtdisplay.chart.controller.mixins.series import (SeriesMixin,
                                                      SeriesTypeCompatibilityError)
from qtdisplay.chart.controller.mixins.settings import SettingsDialogMixin
from qtdisplay.chart.model.base import BaseChartModel
from qtdisplay.chart.model.utils import get_chart_model_type, get_chart_model
from qtdisplay.chart.view.plot import PlotWidget

logger = logging.getLogger(__name__)

# Python 3.12 type aliases
type SeriesName = str


@with_logger
class BaseChartController(
    QObject,
    SeriesMixin,
    AxesMixin,
    SettingsDialogMixin,
    DataTableMixin,
    metaclass=QABCMeta
):
    """
    Base chart controller with modular design.

    Composition:
    - SeriesMixin: Series lifecycle and display
    - AxesMixin: Axes lifecycle and display
    - SettingsDialogMixin: Settings UI coordination
    - DataTableMixin: Table synchronization

    Core Responsibilities:
    - Overall lifecycle coordination
    - Plot/view management
    - Signal routing
    - Batch updates
    """

    MIN_BATCH_INTERVAL_MS: int = 1

    def __init__(
            self,
            config: PlotConfig,
            view: Optional[PlotWidget] = None,
            parent=None
    ):
        """
        Initialize controller with configuration.

        Args:
            config: Immutable plot configuration
            view: Optional pre-configured view
            parent: Qt parent object
        """
        super().__init__()

        # Core configuration and view
        self.config = config
        self.plot = view or PlotWidget(self.config)

        # Initialize all mixin capabilities
        self._init_series_management()
        self._init_axes_management()

        # Chart display settings
        self.chart_display_settings = ChartDisplaySettings()

        # State management
        self._batch_timer: Optional[QTimer] = None
        self._enable_update = True
        self.user_is_zooming = False

        # Wire UI and setup batch timer
        self._wire_ui_signals()

        if self.batch_update_configured:
            self._setup_batch_timer()
            self._batch_timer.start()

    # ========================================================================
    # PROPERTIES
    # ========================================================================

    @property
    def batch_update_configured(self) -> bool:
        """Whether batch updating is enabled."""
        return bool(self.config.batch_ms and self.config.batch_ms > 1)

    @property
    def updates_enabled(self) -> bool:
        """Whether updates are enabled."""
        return self._enable_update

    @updates_enabled.setter
    def updates_enabled(self, enabled: bool) -> None:
        """Enable or disable updates."""
        self._enable_update = enabled

    # ========================================================================
    # PUBLIC API - Series Management (Restored Critical Logic)
    # ========================================================================

    def add_series(self, series_config: SeriesConfig) -> Optional[
        QAbstractSeries]:
        """
        Add new series to chart.

        CRITICAL: This coordinates model creation, axes setup, and Qt series creation.
        """
        series_type = series_config.series_type
        series_name = series_config.name

        # Check compatibility (from SeriesMixin)
        if not self._is_series_type_compatible(series_type):
            compatible_types = ", ".join(
                str(t) for t in self._current_series_type_group)
            raise SeriesTypeCompatibilityError(
                f"Cannot add {series_type} to chart with existing series types: "
                f"{compatible_types}"
            )

        # Get or create model for this series type
        model_type = get_chart_model_type(series_type=series_type)
        model = None
        for m in self.models.values():
            if m.__class__ == model_type:
                model = m
                break

        if not model:
            model = get_chart_model(series_type=series_type, config=self.config)
            self._connect_model_signals(series_name, model)

        # Store model and config
        self.models[series_name] = model
        self.series_configs[series_name] = series_config

        # Setup axes if this is the first series (CRITICAL!)
        if self.axes_config is None:
            self.axes_config = series_config.axes_config
            self.setup_axes(series_config.axes_config)
        else:
            # Verify axes compatibility
            if not self._are_axes_compatible(series_config.axes_config):
                # TODO: raise error
                logger.warning(
                    f"Series {series_name} has different axes settings. "
                    f"Using primary axes configuration."
                )
            else:
                self._create_axes(series_config.axes_config)

        # Initialize display settings BEFORE adding to model
        if series_name not in self.series_display_settings:
            self.series_display_settings[series_name] = SeriesDisplaySettings(
            )

        # Add series to model - this triggers seriesAdded signal
        # which calls _on_series_added to create the Qt series
        model.add_series(name=series_name)

        # Update display settings with model's auto-generated color
        self.series_display_settings[
            series_name].color = model.get_series_color(series_name)

        logger.info(f"Added series: {series_name} (type: {series_type})")
        return self.series_map.get(series_name)

    def _are_axes_compatible(self, axes_config: AxesConfig) -> bool:
        """Check if axes config is compatible with primary axes."""
        if self.axes_config is None:
            return True
        return axes_config.axisX_type == self.axes_config.axisX_type

    def append_point(self, series_name: SeriesName, x: float, y: float) -> None:
        """Append data point to series."""
        model = self.models.get(series_name)
        if model:
            model.append_point(series_name, x, y)

    # ========================================================================
    # PUBLIC API - Chart Display
    # ========================================================================

    def set_chart_display_settings(
            self,
            settings: ChartDisplaySettings
    ) -> None:
        """Set and apply chart-level display settings."""
        self.chart_display_settings = settings
        self.plot.set_chart_settings(settings)

    # ========================================================================
    # SIGNAL WIRING - Single Connection Point
    # ========================================================================

    def _wire_ui_signals(self) -> None:
        """
        Wire all UI signals once.

        Critical for performance: Each signal connected exactly once.
        Uses direct slot references to avoid lambda overhead.
        """
        view = self.plot.view
        toolbar = self.plot.toolbar

        # View interactions
        view.mouseMoved.connect(self._on_mouse_moved)
        view.crosshair_pos_changed.connect(
            self._on_update_table_crosshair_highlight)

        # Toolbar actions
        toolbar.settingsRequested.connect(self._on_show_settings_dialog)
        toolbar.tableRequested.connect(self._on_toggle_data_table)
        toolbar.snapshotRequested.connect(self._on_snapshot_requested)
        toolbar.downloadRequested.connect(self._on_save_data_requested)
        toolbar.uploadRequested.connect(self._on_load_data_requested)
        toolbar.fullViewRequested.connect(self._on_full_view_toggled)

    def _connect_model_signals(
            self,
            series_name: SeriesName,
            model: BaseChartModel
    ) -> None:
        """
        Connect model signals efficiently.

        CRITICAL: Model signals drive the entire data flow.
        """
        # Data update signal (if not batched)
        if not self.batch_update_configured:
            model.seriesDataChanged.connect(self._on_series_data_changed)

        # Range change signal - CRITICAL for axes updates
        model.rangeChanged.connect(self._on_range_changed)

        # Series added signal - CRITICAL for creating Qt series objects
        model.seriesAdded.connect(self._on_series_added)

    def _disconnect_model_signals(self, model: BaseChartModel) -> None:
        """
        Disconnect all model signals.

        Proper cleanup prevents memory leaks and ghost signals.
        """
        try:
            model.seriesDataChanged.disconnect()
        except (TypeError, RuntimeError):
            pass

        try:
            model.rangeChanged.disconnect()
        except (TypeError, RuntimeError):
            pass

        try:
            model.seriesAdded.disconnect()
        except (TypeError, RuntimeError):
            pass

    # ========================================================================
    # SLOTS - Data Updates
    # ========================================================================

    @pyqtSlot()
    def _on_batch_update(self) -> None:
        """
        Handle batch timer - update all series.

        CRITICAL: This is how data gets from model to Qt series in batch mode.
        """
        if not self.updates_enabled:
            return

        # Update each series that has pending changes
        for series_name in self.models:
            self._update_series_data(series_name)

    @pyqtSlot(str)
    def _on_series_data_changed(self, series_name: SeriesName) -> None:
        """
        Handle data change for specific series.

        CRITICAL: In non-batch mode, this updates Qt series immediately.
        """
        self._update_series_data(series_name)
        self._update_data_table()

    @pyqtSlot(float, float, float, float)
    def _on_range_changed(
            self,
            x_min: float,
            x_max: float,
            y_min: float,
            y_max: float
    ) -> None:
        """
        Handle data range change from model.

        CRITICAL: This updates axes ranges as data changes.
        """
        if self.user_is_zooming:
            return

        # Call abstract method implemented by subclass
        self._update_axes_range(x_min, x_max, y_min, y_max)

    # ========================================================================
    # SLOTS - UI Interactions
    # ========================================================================

    @pyqtSlot()
    def _on_snapshot_requested(self) -> None:
        """Save chart as image."""
        file_path, _ = QFileDialog.getSaveFileName(
            None,
            "Save Chart Snapshot",
            "",
            "PNG Image (*.png);;JPEG Image (*.jpg *.jpeg);;BMP Image (*.bmp)"
        )

        if not file_path:
            return

        image = self.plot.view.capture_snapshot()
        extension = Path(file_path).suffix.lower()

        # Use match/case for format mapping (Python 3.10+)
        match extension:
            case '.png':
                fmt = 'PNG'
            case '.jpg' | '.jpeg':
                fmt = 'JPEG'
            case '.bmp':
                fmt = 'BMP'
            case _:
                fmt = 'PNG'

        success = image.save(file_path, fmt)
        msg = f"Snapshot saved to {file_path}" if success else "Failed to save snapshot"
        QMessageBox.information(None, "Success" if success else "Error", msg)

    @pyqtSlot()
    def _on_save_data_requested(self) -> None:
        """Save data to NPZ file."""
        file_path, _ = QFileDialog.getSaveFileName(
            None,
            "Save Chart Data",
            "",
            "NumPy Compressed Archive (*.npz)"
        )

        if not file_path:
            return

        if not file_path.endswith('.npz'):
            file_path += '.npz'

        if not self.models:
            QMessageBox.warning(None, "Error", "No data to save")
            return

        model = next(iter(self.models.values()))
        success, error = model.save_to_npz(file_path)

        msg = f"Data saved to {file_path}" if success else f"Failed: {error}"
        QMessageBox.information(None, "Success" if success else "Error", msg)

    @pyqtSlot()
    def _on_load_data_requested(self) -> None:
        """Load data from NPZ file."""
        pass  # Implement based on requirements

    @pyqtSlot(bool)
    def _on_full_view_toggled(self, checked: bool) -> None:
        """Toggle minimal/normal view mode."""
        if checked:
            self.plot.view.set_minimal_mode()
        else:
            self.plot.view.set_normal_mode()

    def _on_mouse_moved(self, pos: QPointF) -> None:
        """Handle mouse movement (override in subclass if needed)."""
        pass

    def _on_x_range_changed(self, min_val: float, max_val: float) -> None:
        """Handle X range change (override in subclass if needed)."""
        pass

    def _on_y_range_changed(self, min_val: float, max_val: float) -> None:
        """Handle Y range change (override in subclass if needed)."""
        pass

    # ========================================================================
    # BATCH TIMER SETUP
    # ========================================================================

    def _setup_batch_timer(self) -> None:
        """Setup batch update timer."""
        self._batch_timer = QTimer(self)
        self._batch_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._batch_timer.setInterval(self.config.batch_ms)
        self._batch_timer.timeout.connect(self._on_batch_update)

    # ========================================================================
    # ABSTRACT METHODS - Subclass Implementation Required
    # ========================================================================

    def _convert_x_for_series(self, x: float) -> float:
        """Convert x value to appropriate format for series data.

        For QDateTimeAxis, converts timestamp to milliseconds since epoch.
        For regular axes, returns the value unchanged.
        """
        if isinstance(self.plot.axis_x, PyQt6.QtCharts.QDateTimeAxis):
            # Convert seconds timestamp to milliseconds
            return x * 1000
        return x

    def _create_axes(self, axes_config: AxesConfig):
        """Create and configure X and Y axes based on settings.

        Creates axes of the specified type at the specified alignment if they
        don't already exist in the axes map. Supports multiple Y axes (left/right).

        Args:
            axes_config: Axes configuration specifying type, alignment, and properties
        """
        from PyQt6.QtCharts import QValueAxis, QDateTimeAxis, QBarCategoryAxis, \
            QLogValueAxis

        # Create X axis if it doesn't exist at this alignment
        if axes_config.axisX_alignment not in self.axes_map:
            # Create axis based on type
            if axes_config.axisX_type == QAbstractAxis.AxisType.AxisTypeDateTime:
                x_axis = QDateTimeAxis()
                x_axis.setFormat("HH:mm:ss")
            elif axes_config.axisX_type == QAbstractAxis.AxisType.AxisTypeBarCategory:
                x_axis = QBarCategoryAxis()
            elif axes_config.axisX_type == QAbstractAxis.AxisType.AxisTypeLogValue:
                x_axis = QLogValueAxis()
                x_axis.setBase(10)
            else:  # AxisTypeValue (default)
                x_axis = QValueAxis()
                if hasattr(self.config, 'precision'):
                    x_axis.setLabelFormat(f"%.{self.config.precision}f")

            # Add to chart
            self.plot.chart.addAxis(x_axis, axes_config.axisX_alignment)
            self.axes_map[axes_config.axisX_alignment] = x_axis

            # Set as primary X axis if first one
            if not hasattr(self.plot, 'axis_x') or self.plot.axis_x is None:
                self.plot.axis_x = x_axis

        # Create Y axis if it doesn't exist at this alignment
        if axes_config.axisY_alignment not in self.axes_map:
            # Create axis based on type
            if axes_config.axisY_type == QAbstractAxis.AxisType.AxisTypeLogValue:
                y_axis = QLogValueAxis()
                y_axis.setBase(10)
            else:  # AxisTypeValue (default for Y)
                y_axis = QValueAxis()
                if hasattr(self.config, 'precision'):
                    y_axis.setLabelFormat(f"%.{self.config.precision}f")

            # Add to chart
            self.plot.chart.addAxis(y_axis, axes_config.axisY_alignment)
            self.axes_map[axes_config.axisY_alignment] = y_axis

            # Set as primary Y axis if first one
            if not hasattr(self.plot, 'axis_y') or self.plot.axis_y is None:
                self.plot.axis_y = y_axis

    def _attach_shared_axes(self, series_list: List):
        for s in series_list:
            was_attached = s.attachAxis(self.plot.axis_x)
            if not was_attached:
                logger.error(f"{self.cls_name}: serie(s) could not be attached "
                             f"to plot axis X")
            s.attachAxis(self.plot.axis_y)

    @abc.abstractmethod
    def _on_series_added(self, series_name: SeriesName) -> None:
        """
        Handle series addition - create Qt series object.

        CRITICAL: This creates the QLineSeries/QScatterSeries/etc and adds to chart.
        Must populate self.series_map[series_name] with created series.
        """
        pass

    @abc.abstractmethod
    def _update_series_data(self, series_name: SeriesName) -> None:
        """
        Update Qt series with data from model.

        CRITICAL: This syncs model data to the visual series on chart.
        """
        pass

    @abc.abstractmethod
    def _update_axes_range(
            self,
            x_min: float,
            x_max: float,
            y_min: float,
            y_max: float
    ) -> None:
        """
        Update axes ranges (with padding from settings if desired).

        Subclass can use update_axes_range_with_padding() from AxesMixin.
        """
        pass


@contextmanager
def batch_update_series(
        series: List[str],
        controller: BaseChartController,
        ignore_missing: bool = True
) -> Iterator[None]:
    """
    Enhanced context manager for batch series updates with error handling.

    Args:
        series: List of series names to update
        controller: Chart controller instance
        ignore_missing: If True, skip missing series; if False, raise error

    Raises:
        ValueError: If ignore_missing=False and a series is not found
    """
    series_models: Set = set()
    missing_series: List[str] = []

    # Collect unique models
    for series_name in series:
        try:
            model = controller.get_model(series_name)
            if model is not None:
                series_models.add(model)
            elif not ignore_missing:
                missing_series.append(series_name)
        except Exception as e:
            logger.warning(
                f"Failed to get model for series '{series_name}': {e}")
            if not ignore_missing:
                raise

    if missing_series and not ignore_missing:
        raise ValueError(f"Series not found: {missing_series}")

    # Track which models we successfully began updating
    updated_models: Set = set()

    try:
        # Begin updates
        for model in series_models:
            try:
                if hasattr(model, 'begin_update'):
                    model.begin_update()
                    updated_models.add(model)
            except Exception as e:
                logger.error(f"Failed to begin update for model: {e}")
                if not ignore_missing:
                    raise

        yield  # Execute user code

    except Exception as e:
        logger.error(f"Error during batch update: {e}")
        raise

    finally:
        # End updates for successfully started models
        for model in updated_models:
            try:
                if hasattr(model, 'end_update'):
                    model.end_update()
            except Exception as e:
                logger.error(f"Failed to end update for model: {e}")
                # Don't raise in finally block to avoid masking original exception

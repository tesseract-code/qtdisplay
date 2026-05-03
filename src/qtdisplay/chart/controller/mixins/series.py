"""
SeriesMixin - Modular Series Management
========================================

Responsibilities:
- Series lifecycle (add, remove, configure)
- Series display settings (color, visibility, line width)
- Model-series coordination
- Series type compatibility validation

Python 3.12 Features:
- type statement for clean type aliases
- Match/case for series type handling
- Optimized dict operations
"""

from qtdisplay.chart.config import (SeriesConfig,
                                    SeriesDisplaySettings)
from qtdisplay.chart.model.base import (BaseChartModel)
from qtdisplay.chart.model.utils import get_chart_model_type, get_chart_model

"""
SeriesMixin - Modular Series Management
========================================

Responsibilities:
- Series lifecycle (add, remove, configure)
- Series display settings (color, visibility, line width)
- Model-series coordination
- Series type compatibility validation

Python 3.12 Features:
- type statement for clean type aliases
- Match/case for series type handling
- Optimized dict operations
"""

from typing import Optional, Protocol, Any

from PyQt6.QtCharts import QAbstractSeries

# Python 3.12 type statement for cleaner aliases
type SeriesName = str
type SeriesMap = dict[SeriesName, Any]
type SeriesConfigMap = dict[SeriesName, SeriesConfig]
type SeriesSettingsMap = dict[SeriesName, SeriesDisplaySettings]


class SeriesTypeCompatibilityError(ValueError):
    """Raised when incompatible series types are mixed."""
    pass


class SeriesControllerProtocol(Protocol):
    """Protocol defining what SeriesMixin needs from parent controller."""
    models: dict[SeriesName, BaseChartModel]
    series_map: SeriesMap
    series_configs: SeriesConfigMap
    series_display_settings: SeriesSettingsMap
    _current_series_type_group: Optional[set[QAbstractSeries.SeriesType]]

    def _connect_model_signals(self, series_name: str,
                               model: BaseChartModel) -> None: ...

    def _disconnect_model_signals(self, model: BaseChartModel) -> None: ...

    def _on_series_added(self, series_name: str) -> None: ...


class SeriesMixin:
    """
    Mixin providing series management capabilities.

    Handles series lifecycle, display settings, and type compatibility.
    Optimized for O(1) lookups and minimal signal overhead.
    """

    # Series type compatibility groups
    COMPATIBLE_SERIES_GROUPS: tuple[frozenset, ...] = (
        frozenset({
            QAbstractSeries.SeriesType.SeriesTypeLine,
            QAbstractSeries.SeriesType.SeriesTypeScatter,
            QAbstractSeries.SeriesType.SeriesTypeArea,
            QAbstractSeries.SeriesType.SeriesTypeSpline
        }),
        frozenset({
            QAbstractSeries.SeriesType.SeriesTypeBar,
            QAbstractSeries.SeriesType.SeriesTypeStackedBar,
            QAbstractSeries.SeriesType.SeriesTypePercentBar
        }),
        frozenset({QAbstractSeries.SeriesType.SeriesTypePie})
    )

    def _init_series_management(self) -> None:
        """Initialize series management structures. Call from __init__."""
        self.models: dict[SeriesName, BaseChartModel] = {}
        self.series_map: SeriesMap = {}
        self.series_configs: SeriesConfigMap = {}
        self.series_display_settings: SeriesSettingsMap = {}
        self._current_series_type_group: Optional[frozenset] = None

    # ========================================================================
    # PUBLIC API - Series Lifecycle
    # ========================================================================

    # NOTE: add_series() is implemented in BaseChartController because it requires
    # coordination with axes setup and model signal connections.
    # This mixin provides supporting methods only.

    def remove_series(self, series_name: SeriesName) -> bool:
        """
        Remove series from chart.

        Args:
            series_name: Name of series to remove

        Returns:
            True if removed, False if not found
        """
        if series_name not in self.models:
            return False

        # Cleanup model and signals
        model = self.models[series_name]
        # self._disconnect_model_signals(model)

        # Remove Qt series from chart (if exists)
        if series_name in self.series_map:
            series = self.series_map[series_name]
            self.plot.chart.removeSeries(series)
            del self.series_map[series_name]

        # Cleanup all references (O(1) deletions)
        del self.models[series_name]
        del self.series_configs[series_name]
        self.series_display_settings.pop(series_name, None)

        # Reset compatibility if no series remain
        if not self.models:
            self._current_series_type_group = None

        return True

    def get_series_names(self) -> list[SeriesName]:
        """Get list of all series names."""
        return list(self.models.keys())

    def get_model(self, series_name: SeriesName) -> Optional[BaseChartModel]:
        """Get model for specific series."""
        return self.models.get(series_name)

    # ========================================================================
    # PUBLIC API - Display Settings
    # ========================================================================

    def get_series_display_settings(
            self,
            series_name: SeriesName
    ) -> SeriesDisplaySettings:
        """Get display settings for series, creating default if needed."""
        if series_name not in self.series_display_settings:
            config = self.series_configs.get(series_name)
            self.series_display_settings[series_name] = SeriesDisplaySettings(
                name=series_name,
                color=config.color if config else None
            )
        return self.series_display_settings[series_name]

    def set_series_display_settings(
            self,
            series_name: SeriesName,
            settings: SeriesDisplaySettings
    ) -> None:
        """Set and apply display settings for series."""
        if series_name not in self.series_map:
            return

        print(series_name, settings)
        self.series_display_settings[series_name] = settings
        self._apply_series_display_settings(series_name)

    def get_all_series_display_settings(self) -> SeriesSettingsMap:
        """Get display settings for all series."""
        # Ensure all series have settings
        for series_name in self.series_map:
            if series_name not in self.series_display_settings:
                self.get_series_display_settings(series_name)
        return dict(self.series_display_settings)

    # ========================================================================
    # INTERNAL - Type Compatibility and Model Management
    # ========================================================================

    def _is_series_type_compatible(
            self,
            series_type: QAbstractSeries.SeriesType
    ) -> bool:
        """
        Check if series type compatible with current chart.

        Uses frozenset for O(1) membership checks.
        """
        if self._current_series_type_group is None:
            # First series - find its compatibility group
            for group in self.COMPATIBLE_SERIES_GROUPS:
                if series_type in group:
                    self._current_series_type_group = group
                    return True
            return False

        # Check against established group (O(1))
        return series_type in self._current_series_type_group

    def _get_or_create_model(
            self,
            series_type: QAbstractSeries.SeriesType,
            series_config: SeriesConfig
    ) -> BaseChartModel:
        """
        Get existing model or create new one for series type.

        Series of same type share a model for efficiency.
        """
        model_type = get_chart_model_type(series_type)

        # Check if we already have a model of this type (O(n) where n=models)
        # Since n is typically small (1-3), this is acceptable
        for model in self.models.values():
            if type(model) is model_type:
                return model

        # Create new model
        model = get_chart_model(series_type, self.config)
        # self._connect_model_signals(series_config.name, model)
        return model

    def _apply_series_display_settings(self, series_name: SeriesName) -> None:
        """Apply display settings to Qt series object."""
        series = self.series_map.get(series_name)
        settings = self.series_display_settings.get(series_name)

        if not series or not settings:
            return

        if settings.color:
            model = self.models.get(series_name)
            model.set_series_color(series_name, settings.color)
            series.setColor(settings.color)

        series.setVisible(settings.visible)

        # Line width (if applicable)
        if hasattr(series, 'pen'):
            pen = series.pen()
            pen.setWidth(settings.line_width)
            series.setPen(pen)

        self.plot.chart.update()

    def _reset_all_series_colors(self) -> None:
        """Restore custom colors after theme change."""
        for series_name in self.series_map:
            self._apply_series_display_settings(series_name)

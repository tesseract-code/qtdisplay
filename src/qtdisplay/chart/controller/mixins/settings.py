"""
SettingsDialogMixin - Settings Dialog Coordination
===================================================

Responsibilities:
- Settings dialog lifecycle
- Gathering current settings from all sources
- Applying settings changes from dialog
- Series icon mapping

Python 3.12 Features:
- Match/case for series type to icon mapping
- Type aliases for clarity
"""

from typing import Protocol

from PyQt6.QtCharts import QAbstractSeries
from PyQt6.QtCore import pyqtSlot

from qtdisplay.chart.config import (ChartDisplaySettings,
                                    SeriesDisplaySettings)
from qtdisplay.chart.view.dialog import ChartDialog

# Type aliases
type SeriesIconMap = dict[str, str]


class SettingsDialogControllerProtocol(Protocol):
    """Protocol defining what SettingsDialogMixin needs."""
    chart_display_settings: ChartDisplaySettings
    series_configs: dict

    def get_all_series_display_settings(self) -> dict[
        str, SeriesDisplaySettings]: ...

    def get_all_axes_display_settings(self) -> dict: ...

    def set_chart_display_settings(self,
                                   settings: ChartDisplaySettings) -> None: ...

    def set_series_display_settings(self, name: str,
                                    settings: SeriesDisplaySettings) -> None: ...

    def set_all_axes_display_settings(self, settings: dict) -> None: ...


class SettingsDialogMixin:
    """
    Mixin coordinating settings dialog interactions.

    Handles dialog creation, settings gathering, and applying changes.
    """

    # ========================================================================
    # PUBLIC SLOT - Show Dialog
    # ========================================================================

    @pyqtSlot()
    def _on_show_settings_dialog(self) -> None:
        """Show settings dialog with current settings."""
        # Gather current settings (all O(1) or O(n) with small n)
        series_icons = self._build_series_icon_map()

        dialog = ChartDialog(
            chart_settings=self.chart_display_settings,
            series_settings=self.get_all_series_display_settings(),
            series_icons=series_icons,
            axes_settings=self.get_all_axes_display_settings(),
            parent=self.plot
        )

        # Connect to single slot (avoids multiple signal connections)
        dialog.settings_applied.connect(self._on_settings_applied)

        # Show centered
        dialog.show_centered(self.plot.window())

    # ========================================================================
    # PRIVATE SLOT - Apply Settings
    # ========================================================================

    @pyqtSlot(ChartDisplaySettings, dict, dict)
    def _on_settings_applied(
            self,
            chart_settings: ChartDisplaySettings,
            series_settings: dict[str, SeriesDisplaySettings],
            axes_settings: dict
    ) -> None:
        """
        Apply settings from dialog.

        All operations are O(1) or O(n) with small n (number of series/axes).
        """
        print("HERE")
        # Apply chart settings
        self.set_chart_display_settings(chart_settings)

        # Apply series settings
        for series_name, settings in series_settings.items():
            self.set_series_display_settings(series_name, settings)

        # Apply axes settings
        self.set_all_axes_display_settings(axes_settings)

    # ========================================================================
    # INTERNAL - Series Icon Mapping
    # ========================================================================

    def _build_series_icon_map(self) -> SeriesIconMap:
        """
        Build mapping of series names to their icons.

        O(n) where n is number of series (typically small).
        """
        return {
            series_name: self._get_series_icon(config.series_type)
            for series_name, config in self.series_configs.items()
        }

    @staticmethod
    def _get_series_icon(series_type: QAbstractSeries.SeriesType) -> str:
        """
        Get icon for series type using optimized match/case.

        Match/case compiles to jump table for O(1) dispatch.
        """
        match series_type:
            case (QAbstractSeries.SeriesType.SeriesTypeBar |
                  QAbstractSeries.SeriesType.SeriesTypeStackedBar |
                  QAbstractSeries.SeriesType.SeriesTypePercentBar):
                return "bar-chart"

            case QAbstractSeries.SeriesType.SeriesTypePie:
                return "pie-chart"

            case QAbstractSeries.SeriesType.SeriesTypeScatter:
                return "bubble-chart"

            case (QAbstractSeries.SeriesType.SeriesTypeLine |
                  QAbstractSeries.SeriesType.SeriesTypeSpline):
                return "line-chart"

            case QAbstractSeries.SeriesType.SeriesTypeArea:
                return "line-chart"  # Fallback

            case _:
                return "line-chart"  # Default

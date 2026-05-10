"""
ChartSettingsDialog - Updated to Handle Mutable Settings Properly
==================================================================

This dialog now correctly handles:
1. ChartDisplaySettings (single instance)
2. SeriesDisplaySettings (dict of instances, one per series)
3. AxesDisplaySettings (dict of instances, one per axis)

Key Changes:
- Accept current settings instances (not just defaults)
- Use create_callable_from_dataclass_instance to show current values
- Emit structured settings back to controller
"""
import sys
from typing import Optional, Dict
from collections import defaultdict

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QScrollArea, QApplication

from pycore.log.utils import list_lock_files, scan_all_ports
from qtdisplay.chart.config import (
    ChartDisplaySettings,
    SeriesDisplaySettings,
    AxesDisplaySettings
)
from qtgui.dialogs import NavigableDialog
from qtgui.form.widget import create_form_for_callable, \
    create_callable_from_dataclass_instance
from cross_platform.dev.icons_legacy.svg_path import IconType


class ChartDialog(NavigableDialog):
    """Chart settings dialog with vertical navigation and mutable settings support."""

    # Emit structured settings: chart, series dict, axes dict
    settings_applied = pyqtSignal(ChartDisplaySettings, dict, dict)

    def __init__(
            self,
            chart_settings: Optional[ChartDisplaySettings] = None,
            series_settings: Optional[Dict[str, SeriesDisplaySettings]] = None,
            series_icons: Optional[Dict[str, IconType]] = None,
            axes_settings: Optional[Dict[Qt.AlignmentFlag, AxesDisplaySettings]] = None,
            parent: Optional[QWidget] = None
    ):
        """Initialize dialog with current settings.

        Args:
            chart_settings: Current chart display settings instance
            series_settings: Dict mapping series names to their display settings
            axes_settings: Dict mapping axis keys to their display settings
            parent: Parent widget
        """
        # Store current settings (these are the actual instances with current values)
        self.chart_settings = chart_settings or ChartDisplaySettings()
        self.series_settings = series_settings or defaultdict(None)
        self.axes_settings = axes_settings or defaultdict(None)
        self.series_icons = series_icons or defaultdict(None)

        # Forms will be created in add_pages()
        self.chart_form = None
        self.series_forms: Dict[str, QWidget] = defaultdict(None)
        self.axes_forms: Dict[Qt.AlignmentFlag, QWidget] = defaultdict(None)

        super().__init__(parent)

    def get_title_text(self) -> str:
        """Get title bar text."""
        return "Chart Settings"

    def get_title_icon(self):
        return QIcon()

    def get_apply_button_text(self) -> str:
        return "Apply"

    def add_pages(self):
        """Add all settings pages to the dialog."""
        self._add_chart_page()
        self._add_axes_pages()
        self._add_series_pages()

    # ========================================================================
    # CHART SETTINGS PAGE
    # ========================================================================

    def _add_chart_page(self):
        """Add the chart display settings page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create callable with current values
        callable_settings = create_callable_from_dataclass_instance(self.chart_settings)

        # Create form
        self.chart_form = create_form_for_callable(callable_settings)
        self.chart_form.setObjectName("chartForm")
        self.chart_form.setStyleSheet("""
            #chartForm { 
                background-color: palette(base); 
            }
        """)
        layout.addWidget(self.chart_form)

        self.add_page(IconType.LINE_IMAGE_EDIT, "Appearance", page)

    # ========================================================================
    # AXES SETTINGS PAGES
    # ========================================================================

    def _add_axes_pages(self):
        """Add axes settings pages (one per axis)."""
        if not self.axes_settings:
            return

        for alignment, axis_settings in self.axes_settings.items():
            page = self._create_axis_page(axis_settings)
            self.axes_forms[alignment] = page

            display_name = self._get_axis_display_name(alignment)
            self.add_page(IconType.LINE_RULER_2, display_name, page)

    def _get_axis_display_name(self, alignment: Qt.AlignmentFlag) -> str:
        """
        Get human-readable display name for axis alignment.

        Args:
            alignment: Qt.AlignmentFlag for axis position

        Returns:
            Display name like "X Axis (Bottom)" or "Y Axis (Left)"
        """
        if alignment == Qt.AlignmentFlag.AlignTop:
            return "X Axis (Top)"
        elif alignment == Qt.AlignmentFlag.AlignBottom:
            return "X Axis (Bottom)"
        elif alignment == Qt.AlignmentFlag.AlignLeft:
            return "Y Axis (Left)"
        elif alignment == Qt.AlignmentFlag.AlignRight:
            return "Y Axis (Right)"
        else:
            return f"Axis ({alignment.name})"

    def _create_axis_page(self, axis_settings: AxesDisplaySettings) -> QWidget:
        """Create a settings page for a single axis."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create scroll area for form
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: palette(base);
                border: none;
            }
        """)

        # Create callable with current values
        callable_settings = create_callable_from_dataclass_instance(axis_settings)

        # Create form
        form_widget = create_form_for_callable(callable_settings)
        form_widget.setStyleSheet("background-color: palette(base);")

        scroll_area.setWidget(form_widget)
        layout.addWidget(scroll_area)

        # Store form reference for later retrieval
        page.form_widget = form_widget

        return page

    # ========================================================================
    # SERIES SETTINGS PAGES
    # ========================================================================

    def _add_series_pages(self):
        """Add series settings pages (one per series)."""
        if not self.series_settings:
            return

        for series_name, series_config in self.series_settings.items():
            page = self._create_series_page(series_config)
            self.series_forms[series_name] = page
            series_icon = (self.series_icons.get(series_name) or
                     IconType.LINE_BUBBLE_CHART)
            self.add_page(series_icon, f"Series: {series_name}", page)

    def _create_series_page(self, series_config: SeriesDisplaySettings) -> QWidget:
        """Create a settings page for a single series."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create scroll area for form
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: palette(base);
                border: none;
            }
        """)

        # Create callable with current values
        callable_settings = create_callable_from_dataclass_instance(series_config)

        # Create form
        form_widget = create_form_for_callable(callable_settings)
        form_widget.setStyleSheet("background-color: palette(base);")

        scroll_area.setWidget(form_widget)
        layout.addWidget(scroll_area)

        # Store form reference for later retrieval
        page.form_widget = form_widget

        return page

    # ========================================================================
    # APPLY SETTINGS
    # ========================================================================

    def on_apply(self):
        """Gather settings from all forms and emit signal."""
        # Get chart settings
        chart_settings = ChartDisplaySettings(**self.chart_form.get_values())

        # Get all series settings
        series_settings = defaultdict(None)
        for series_name, page in self.series_forms.items():
            form_values = page.form_widget.get_typed_values()
            series_settings[series_name] = SeriesDisplaySettings(**form_values)


        # Get all axes settings
        axes_settings = defaultdict(None)
        for axis_key, page in self.axes_forms.items():
            form_values = page.form_widget.get_values()
            axes_settings[axis_key] = AxesDisplaySettings(**form_values)

        # Emit all settings as structured data
        self.settings_applied.emit(chart_settings, series_settings, axes_settings)
        self.accept()
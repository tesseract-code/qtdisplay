from collections import defaultdict
from typing import Optional

from PyQt6.QtCore import pyqtSlot, pyqtSignal
from PyQt6.QtGui import QColor, QAction
from PyQt6.QtWidgets import QApplication, QToolBar

from qtdisplay.chart.view.dialog import (ChartDisplaySettings)
from qtgui.icons import _get_line_icon

from qtgui.style.toolbar import StyledToolBar


class ChartToolBar(StyledToolBar):
    """ Chart controller toolbar."""

    snapshotRequested = pyqtSignal()
    downloadRequested = pyqtSignal()
    uploadRequested = pyqtSignal()
    fullViewRequested = pyqtSignal(bool)
    tableRequested = pyqtSignal(bool)
    settingsRequested = pyqtSignal()
    settingsUpdated = pyqtSignal(ChartDisplaySettings)

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.settings = ChartDisplaySettings()

        self.snapshot_action: Optional[QAction] = None
        self.full_screen_action: Optional[QAction] = None
        self.table_action: Optional[QAction] = None
        self.settings_actionn: Optional[QAction] = None
        self._isfullscreen = False

        self._setup_ui()
        self._wire_ui()

    def _setup_ui(self):
        """Set up the toolbar UI."""
        # Chart snapshot
        snapshot_icon = _get_line_icon("camera-lens", self.iconSize(), self.palette().accent().color())
        self.snapshot_action = QAction(snapshot_icon, "Snapshot", self)
        self.snapshot_action.setToolTip("Save an image of the chart")
        self.addAction(self.snapshot_action)

        # Chart full view
        fullscreen_icon = _get_line_icon("fullscreen", self.iconSize(), self.palette().accent().color())
        self.full_screen_action = QAction(fullscreen_icon, "Full View", self)
        self.full_screen_action.setToolTip("Show or hide chart axes and labels")
        self.full_screen_action.setCheckable(True)
        self.addAction(self.full_screen_action)

        # Chart table
        table_icon = _get_line_icon("table", self.iconSize(), self.palette().accent().color())
        self.table_action = QAction(table_icon, "Data Table", self)
        self.table_action.setToolTip("Show or hide chart data table")
        self.table_action.setCheckable(True)
        self.addAction(self.table_action)

        self.addSeparator()
        # Chart settings
        settings_icon = _get_line_icon("settings", self.iconSize(), self.palette().accent().color())
        self.settings_action = QAction(settings_icon, "Settings", self)
        self.settings_action.setToolTip("Show chart settings menu")
        self.addAction(self.settings_action)

        self.icon_map = defaultdict(None)
        self.icon_map.update({id(
            self.full_screen_action):"fullscreen-exit",
             id(self.table_action): "table",
             id(self.settings_action): "settings",
             id(self.snapshot_action): "camera-lens"})

    def get_text_color(self):
        app = QApplication.instance()

        # Force refresh from system
        style = app.style()
        system_palette = style.standardPalette()
        return QColor(system_palette.text().color())

    def _wire_ui(self):
        """Wire the toolbar actions' events."""
        self.snapshot_action.triggered.connect(
            lambda _: self.snapshotRequested.emit())
        self.full_screen_action.triggered.connect(self._on_fullscreen_toggle)
        self.settings_action.triggered.connect(lambda _: self.settingsRequested.emit())

        self.table_action.triggered.connect(
            lambda checked: self.tableRequested.emit(checked))

    @pyqtSlot(bool)
    def _on_fullscreen_toggle(self, checked: bool):
        """"""
        self._isfullscreen = checked
        if checked:
            self.full_screen_action.setIcon(_get_line_icon("fullscreen-exit",
                                                     self.iconSize(),
                                                     self.get_text_color()))
        else:
            self.full_screen_action.setIcon(
                _get_line_icon("fullscreen", self.iconSize(),
                         self.get_text_color()))

        self.fullViewRequested.emit(checked)


    @pyqtSlot(ChartDisplaySettings)
    def _on_apply_chart_settings(self, settings: ChartDisplaySettings):
        """Apply settings to chart."""
        self.settings = settings
        self.settingsUpdated.emit(self.settings)

"""
Data Table Coordination
=========================================
Responsibilities:
- Table visibility management
- Data updates and synchronization
- Series visibility reflection in table
- X-position highlighting
"""

from typing import Protocol
from PyQt6.QtCore import pyqtSlot, QPointF


class DataTableControllerProtocol(Protocol):
    """Protocol defining what DataTableMixin needs."""
    models: dict
    series_display_settings: dict
    plot: object  # PlotWidget


class DataTableMixin:
    """
    Mixin coordinating data table interactions.

    Handles table updates, visibility syncing, and crosshair integration.
    """

    # ========================================================================
    # PUBLIC SLOTS - Table Visibility
    # ========================================================================

    @pyqtSlot(bool)
    def _on_toggle_data_table(self, visible: bool) -> None:
        """
        Toggle data table visibility.

        Only updates data when becoming visible (lazy evaluation).
        """
        if not hasattr(self.plot, 'data_table'):
            return

        if visible:
            self.plot.data_table.show()
            self._update_data_table()
        else:
            self.plot.data_table.hide()

    @pyqtSlot(QPointF)
    def _on_update_table_crosshair_highlight(self, pos: QPointF) -> None:
        """
        Update table highlight based on crosshair position.

        Only processes if table is visible (avoids unnecessary work).
        """
        if not hasattr(self.plot, 'data_table'):
            return

        if not self.plot.data_table.isVisible() or not pos:
            return

        self.plot.data_table.highlight_x_position(pos.x())

    # ========================================================================
    # PUBLIC API - Table Updates
    # ========================================================================

    def update_data_table(self) -> None:
        """Public method to trigger table update."""
        self._update_data_table()

    # ========================================================================
    # INTERNAL - Data Updates
    # ========================================================================

    def _update_data_table(self) -> None:
        """
        Refresh table with current data.

        Optimizations:
        - Early return if not visible
        - Single model fetch
        - Batch visibility update
        """
        if not hasattr(self.plot, 'data_table'):
            return

        if not self.plot.data_table.isVisible():
            return

        if not self.models:
            return

        # Get any model (they share data structure per chart type)
        model = next(iter(self.models.values()))

        # Update table data
        self.plot.data_table.set_data(model)

        # Update visibility (single batch operation)
        visibility_map = {
            name: settings.visible
            for name, settings in self.series_display_settings.items()
        }
        self.plot.data_table.update_series_visibility(visibility_map)
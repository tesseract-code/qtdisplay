from PyQt6 import QtGui
from PyQt6.QtCore import (QAbstractTableModel, QModelIndex, Qt,
                          QSortFilterProxyModel, QItemSelectionModel, QTimer)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableView,
                             QFrame, QLabel, QAbstractItemView,
                             QHeaderView)
from PyQt6.QtGui import QFont, QColor, QBrush
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Optional

# Add this import if not already present
from PyQt6.QtCore import QItemSelection

from qtdisplay.chart.model.data.dataframe import PieDataFrameAdapter, \
    BarDataFrameAdapter, DataFrameAdapter, get_dataframe_adapter
from qtdisplay.chart.model.base import BaseChartModel
from qtgui.edit import SearchLineEdit


class HighlightFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_text = ""
        self._highlight_color = QColor(255, 255, 0,
                                       100)  # Yellow with transparency

    def setSearchText(self, text):
        self._search_text = text
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._search_text:
            return True

        model = self.sourceModel()
        if model is None:
            return True

        # Check if any cell in the row contains the search text
        for column in range(model.columnCount()):
            index = model.index(source_row, column, source_parent)
            value = model.data(index, Qt.ItemDataRole.DisplayRole)
            if value and self._search_text.lower() in str(value).lower():
                return True

        return False

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.BackgroundRole and self._search_text:
            source_index = self.mapToSource(index)
            model = self.sourceModel()
            if model:
                value = model.data(source_index, Qt.ItemDataRole.DisplayRole)
                if value and self._search_text.lower() in str(value).lower():
                    return QBrush(self._highlight_color)

        return super().data(index, role)


class PandasTableModel(QAbstractTableModel):
    def __init__(self, dataframe=None, decimal_precision=6,
                 scientific_notation=False):
        super().__init__()
        self._dataframe = dataframe if dataframe is not None else pd.DataFrame()
        self.decimal_precision = decimal_precision
        self.scientific_notation = scientific_notation

    def rowCount(self, parent=QModelIndex()):
        return len(self._dataframe)

    def columnCount(self, parent=QModelIndex()):
        return len(self._dataframe.columns)

    def data(self, index, role=Qt.ItemDataRole):
        if not index.isValid():
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            value = self._dataframe.iloc[index.row(), index.column()]
            return self._format_value(value)

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole):
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return str(self._dataframe.columns[section])
            elif orientation == Qt.Orientation.Vertical:
                # Format the index (x-column) with the same settings
                index_value = self._dataframe.index[section]
                return self._format_value(index_value)
        return None

    def _format_value(self, value):
        """Format a single value according to current settings."""
        if pd.isna(value):
            return "NaN"

        try:
            num_value = float(value)
            if self.scientific_notation:
                return f"{num_value:.{self.decimal_precision}e}"
            else:
                return f"{num_value:.{self.decimal_precision}f}"
        except (ValueError, TypeError):
            return str(value)

    def update_data(self, new_dataframe):
        """Efficiently update the entire dataset"""
        self.layoutAboutToBeChanged.emit()
        self._dataframe = new_dataframe.copy()
        if self.rowCount() > 0 and self.columnCount() > 0:
            self.dataChanged.emit(self.createIndex(0, 0),
                                  self.createIndex(self.rowCount() - 1,
                                                   self.columnCount() - 1))
        self.layoutChanged.emit()

    def update_format_settings(self, decimal_precision=None,
                               scientific_notation=None):
        """Update formatting settings and refresh display"""
        if decimal_precision is not None:
            self.decimal_precision = decimal_precision
        if scientific_notation is not None:
            self.scientific_notation = scientific_notation

        # Notify that all data and headers have changed
        if self.rowCount() > 0 and self.columnCount() > 0:
            self.dataChanged.emit(self.createIndex(0, 0),
                                  self.createIndex(self.rowCount() - 1,
                                                   self.columnCount() - 1))
            # Also update headers (especially vertical headers for index/x-column)
            self.headerDataChanged.emit(Qt.Orientation.Vertical, 0,
                                        self.rowCount() - 1)
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0,
                                        self.columnCount() - 1)


class TableStatsBar(QFrame):
    """Compact horizontal statistics bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._last_update_time = None
        self._setup_ui()

        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(False)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self.refresh_time_display)

    def _setup_ui(self):
        """Setup the compact statistics bar UI."""
        layout = QHBoxLayout(self)
        # layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(0)

        # Create stat displays
        self.stat_labels = {}
        stats = [
            ("Rows", "rows", 60),
            ("Cols", "cols", 40),
            ("Memory", "memory", 70),
            ("Type", "dtype", 80),
            ("Updated", "updated", 120),
        ]

        for label_text, key, width in stats:
            container = QWidget()
            container_layout = QHBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(4)

            name_label = QLabel(f"{label_text}:")
            name_label.setStyleSheet("color: #666; font-size: 11px;")

            value_label = QLabel("---")
            value_label.setFixedWidth(width)
            value_font = QFont()
            value_font.setBold(True)
            value_label.setFont(value_font)
            value_label.setStyleSheet("color: #2196F3; font-size: 11px;")
            value_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            container_layout.addWidget(name_label)
            container_layout.addWidget(value_label)

            layout.addWidget(container)
            layout.addStretch()
            self.stat_labels[key] = value_label

    def update_statistics(self, dataframe: pd.DataFrame, precision: int = 6,
                          scientific: bool = False):
        """Update statistics with current dataframe."""
        if dataframe.empty:
            for label in self.stat_labels.values():
                label.setText("---")
            self._stop_refresh_timer()
            return

        # Update last update time
        self._last_update_time = datetime.now()

        # 1. Rows count
        self.stat_labels["rows"].setText(f"{len(dataframe):,}")

        # 2. Columns count
        self.stat_labels["cols"].setText(str(len(dataframe.columns)))

        # 3. Memory usage (formatted as KB, MB, or GB)
        memory_bytes = dataframe.memory_usage(deep=True).sum()
        memory_str = self._format_memory(memory_bytes)
        self.stat_labels["memory"].setText(memory_str)

        # 4. Dominant dtype (most common dtype)
        dtype_str = self._get_dtype_summary(dataframe)
        self.stat_labels["dtype"].setText(dtype_str)

        # 5. Last updated (time ago format)
        updated_str = self._format_time_ago(self._last_update_time)
        self.stat_labels["updated"].setText(updated_str)

        # Start/restart the refresh timer
        self._start_refresh_timer()

    def _format_memory(self, bytes_size: int) -> str:
        """Format bytes to human-readable format."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_size < 1024.0:
                if unit == 'B':
                    return f"{bytes_size:.0f}{unit}"
                return f"{bytes_size:.1f}{unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.1f}TB"

    def _get_dtype_summary(self, dataframe: pd.DataFrame) -> str:
        """Get a summary string of dtypes in the dataframe."""
        # Count dtypes
        dtype_counts = dataframe.dtypes.astype(str).value_counts()

        if len(dtype_counts) == 0:
            return "---"

        # Get most common dtype
        dominant_dtype = dtype_counts.index[0]

        # If mixed types, show dominant + count
        if len(dtype_counts) > 1:
            return f"{dominant_dtype}+{len(dtype_counts) - 1}"

        return dominant_dtype

    def _format_time_ago(self, dt: datetime) -> str:
        """Format datetime as 'X ago' string."""
        if dt is None:
            return "Never"

        now = datetime.now()
        diff = now - dt

        seconds = diff.total_seconds()

        if seconds < 1:
            return "Just now"
        elif seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes}m ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours}h ago"
        else:
            days = int(seconds / 86400)
            return f"{days}d ago"

    def refresh_time_display(self):
        """Refresh the 'time ago' display without recalculating other stats."""
        if self._last_update_time:
            updated_str = self._format_time_ago(self._last_update_time)
            self.stat_labels["updated"].setText(updated_str)

            # Stop timer if we've reached 1+ day ago
            now = datetime.now()
            diff = now - self._last_update_time
            if diff.total_seconds() >= 86400:  # 24 hours
                self._stop_refresh_timer()

    def _start_refresh_timer(self):
        """Start the refresh timer if not already running."""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _stop_refresh_timer(self):
        """Stop the refresh timer."""
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()


class DataTableWidget(QWidget):
    """Table widget for displaying chart data with multi-model support."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dataframe = pd.DataFrame()
        self.current_x_row = -1

        # Format settings
        self.decimal_precision = 6
        self.scientific_notation = False

        # Model tracking
        self._current_model: Optional[BaseChartModel] = None
        self._current_adapter: Optional[DataFrameAdapter] = None

        self._setup_ui()

    def _setup_ui(self):
        """Setup the table UI - UNCHANGED"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Search bar
        search_layout = QHBoxLayout()
        search_layout.addStretch()

        self.search_edit = SearchLineEdit(parent=self)
        self.search_edit.setMinimumHeight(20)
        self.search_edit.textChanged.connect(self._on_search_text_changed)

        search_layout.addWidget(self.search_edit,
                                alignment=Qt.AlignmentFlag.AlignRight)

        layout.addLayout(search_layout)

        # Table with model and proxy
        self.table = QTableView()

        # Initialize source model
        self.table_model = PandasTableModel(
            self.dataframe,
            self.decimal_precision,
            self.scientific_notation
        )

        # Initialize proxy model
        self.proxy_model = HighlightFilterProxyModel()
        self.proxy_model.setSourceModel(self.table_model)
        self.table.setModel(self.proxy_model)

        # Table configuration
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)

        # Font
        try:
            self.table.setFont(
            QtGui.QFontDatabase.systemFont(
                QtGui.QFontDatabase.SystemFont.FixedFont
            )
        )
        except NameError:
            font = QFont("Courier New", 10)
            self.table.setFont(font)

        layout.addWidget(self.table)

        # Statistics bar
        self.stats_bar = TableStatsBar()
        layout.addWidget(self.stats_bar)

    def _on_search_text_changed(self, text):
        """Handle search text changes - UNCHANGED"""
        self.proxy_model.setSearchText(text)
        if text:
            self.table.selectionModel().clearSelection()

    def _on_precision_changed(self, value: int):
        """Handle precision change - UNCHANGED"""
        self.decimal_precision = value
        self.table_model.update_format_settings(decimal_precision=value)
        self._update_statistics()

    def _on_scientific_changed(self, state: int):
        """Handle scientific notation toggle - UNCHANGED"""
        self.scientific_notation = state == Qt.CheckState.Checked.value
        self.table_model.update_format_settings(
            scientific_notation=self.scientific_notation)
        self._update_statistics()

    def _update_statistics(self):
        """Update statistics - UNCHANGED"""
        self.stats_bar.update_statistics(
            self.dataframe,
            self.decimal_precision,
            self.scientific_notation
        )

    # ========================================================================
    # UPDATED METHODS - Multi-Model Support
    # ========================================================================

    def set_data(self, model: BaseChartModel):
        """
        Set data from chart model using appropriate adapter.

        Args:
            model: Any BaseChartModel subclass (XY, Area, Bar, Pie)
        """
        # Get adapter for this model type
        self._current_adapter = get_dataframe_adapter(model)
        self._current_model = model

        # Get normalized DataFrame
        dataframe = self._current_adapter.get_dataframe(model)

        # Update table
        self.dataframe = dataframe.copy()
        self.table_model.update_data(self.dataframe)
        self._update_statistics()

    def highlight_x_position(self, x_value: float):
        """
        Highlight row corresponding to x value.

        Only works for models that support X-highlighting (XY, Area).
        Bar and Pie charts ignore this.
        """
        # Check if we have an adapter and it supports highlighting
        if not self._current_adapter:
            return

        if not self._current_adapter.supports_x_highlighting():
            return  # Silently ignore for Bar/Pie charts

        if self.dataframe.empty:
            return

        # Existing highlighting logic
        x_values = self.dataframe.index.values
        if len(x_values) == 0:
            return

        closest_idx = np.abs(x_values - x_value).argmin()

        if 0 <= closest_idx < self.table_model.rowCount():
            source_index = self.table_model.index(closest_idx, 0)
            proxy_index = self.proxy_model.mapFromSource(source_index)

            if proxy_index.isValid():
                self.table.selectionModel().clearSelection()

                selection_model = self.table.selectionModel()
                model = self.proxy_model

                selection = QItemSelection()
                first_index = model.index(proxy_index.row(), 0)
                last_index = model.index(proxy_index.row(),
                                         model.columnCount() - 1)
                selection.select(first_index, last_index)

                selection_model.select(
                    selection,
                    QItemSelectionModel.SelectionFlag.Select |
                    QItemSelectionModel.SelectionFlag.Rows
                )

                self.table.setCurrentIndex(first_index)
                self.table.scrollTo(
                    first_index,
                    QAbstractItemView.ScrollHint.EnsureVisible
                )
                self.table.setFocus()

    def update_series_visibility(self, series_visibility: Dict[str, bool]):
        """
        Update table based on series visibility.

        For XY/Bar: Hide columns
        For Area: Hide Upper/Lower column pairs
        For Pie: Not applicable (handled in adapter)
        """
        if self.dataframe.empty or not self._current_adapter:
            return

        # For XY and Bar models - column-based hiding
        if self._current_adapter.supports_x_highlighting() or \
                isinstance(self._current_adapter, BarDataFrameAdapter):
            for col_idx, column in enumerate(self.dataframe.columns):
                # Handle both regular series names and Area's "SeriesName_Upper" format
                base_name = column.rsplit('_', 1)[
                    0] if '_' in column else column

                if base_name in series_visibility:
                    self.table.setColumnHidden(col_idx,
                                               not series_visibility[base_name])

        # For Pie: Need to refresh data since adapter filters visibility
        elif isinstance(self._current_adapter, PieDataFrameAdapter):
            if self._current_model:
                self.set_data(self._current_model)


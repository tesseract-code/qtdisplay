from typing import Dict, Optional
from typing import List

from PyQt6.QtCharts import (QBarSeries, QBarSet)
from PyQt6.QtCore import pyqtSlot
from PyQt6.QtGui import QColor

from cross_platform.qt6_utils.chart.config import PlotConfig
from cross_platform.qt6_utils.chart.controller.base import BaseChartController
from cross_platform.qt6_utils.chart.model.bar import BarChartModel


class QBarChartController(BaseChartController):
    """
    Controller for bar charts with proper BarChartModel integration.
    """

    def __init__(self, config: PlotConfig,
                 model: Optional[BarChartModel] = None):
        # Use provided model or create default
        if model is None:
            model = BarChartModel(config)

        super().__init__(config, model)

        self._bar_sets: Dict[str, QBarSet] = {}
        self._bar_series = QBarSeries()
        self._bar_series.setUseOpenGL(not self.config.is_real_time)

        # Add to chart
        self.plot.chart.addSeries(self._bar_series)
        self._bar_series.attachAxis(self.plot.axis_x)
        self._bar_series.attachAxis(self.plot.axis_y)

    def add_bar_set(self, name: str, color: Optional[QColor] = None) -> bool:
        """Add a new bar set that tracks model data."""
        if name in self._bar_sets:
            return False

        # Add to model - this will trigger _on_series_added via base controller
        success = self.model.add_series(name, color)
        if success:
            # Ensure bar set is properly initialized with current categories
            self._sync_bar_set_to_model(name)
        return success

    def set_categories(self, categories: List[str]):
        """Set the categories for the bar chart."""
        # Update model categories (this will resize series and mark dirty)
        self.model.update_categories(categories)

        # Update Qt axis
        self.plot.axis_x.clear()
        if categories:
            self.plot.axis_x.setCategories(categories)

        # The model update will trigger data_changed signal via base controller

    def append_category(self, category: str):
        """Append a new category to the end."""
        if category in self.model.categories:
            return

        new_categories = self.model.categories + [category]
        self.model.update_categories(new_categories)

        # Update Qt axis
        self.plot.axis_x.append(category)

        # Apply window limit
        if len(self.model.categories) > self.config.max_points:
            self._remove_oldest_category()

    def _remove_oldest_category(self):
        """Remove the oldest category when exceeding window size."""
        if not self.model.categories:
            return

        # Remove from model
        new_categories = self.model.categories[1:]
        self.model.update_categories(new_categories)

        # Update Qt axis
        self.plot.axis_x.clear()
        if new_categories:
            self.plot.axis_x.setCategories(new_categories)

    def set_value(self, set_name: str, category: str, value: float):
        """Set a bar value for a specific category."""
        if set_name not in self._bar_sets:
            raise KeyError(f"Bar set '{set_name}' not found")

        # Add category if new
        if category not in self.model.categories:
            self.append_category(category)

        # Use category index to set value in model
        category_index = self.model.categories.index(category)
        self.model.set_value(set_name, category_index, value)

    def set_values(self, set_name: str, values: List[float]):
        """Set all values for a bar set."""
        if set_name not in self._bar_sets:
            raise KeyError(f"Bar set '{set_name}' not found")

        if len(values) != len(self.model.categories):
            raise ValueError(
                f"Values length ({len(values)}) must match "
                f"categories length ({len(self.model.categories)})"
            )

        self.model.set_values(set_name, values)

    # ==================== Signal Handlers ====================

    @pyqtSlot()
    def _on_data_changed(self):
        """Update bar sets when model data changes."""
        # Sync all dirty series
        for series_name in self.model.get_dirty_series():
            self._sync_bar_set_to_model(series_name)

        # Request visual update
        self.plot.view.viewport().update()

    def _sync_bar_set_to_model(self, series_name: str):
        """Sync a single bar set with model data."""
        bar_set = self._bar_sets.get(series_name)
        if bar_set is None:
            return

        values = self.model.get_series_data(series_name)
        if values is None:
            return

        # Clear and rebuild bar set efficiently
        bar_set.remove(0, bar_set.count())  # Remove all current values

        for value in values:
            bar_set.append(float(value))

    def _on_series_added(self, series_name: str):
        """Handle when a series is added to the model."""
        if series_name in self._bar_sets:
            return  # Already exists

        # Create Qt bar set
        bar_set = QBarSet(series_name)

        # Set color from model
        color = self.model.get_series_color(series_name)
        if color:
            bar_set.setColor(color)

        # Initialize with model data
        values = self.model.get_series_data(series_name)
        if values:
            for value in values:
                bar_set.append(float(value))

        # Add to chart structures
        self._bar_sets[series_name] = bar_set
        self._bar_series.append(bar_set)

    def _on_series_removed(self, series_name: str):
        """Handle when a series is removed from the model."""
        if series_name in self._bar_sets:
            bar_set = self._bar_sets[series_name]
            self._bar_series.remove(bar_set)
            del self._bar_sets[series_name]

    # ==================== Range Management ====================

    def _update_axes_range(self, x_min: float, x_max: float, y_min: float,
                           y_max: float):
        """
        Update axes range - required by base controller.
        For bar charts, we calculate range from model data.
        """
        # Ignore the passed parameters and calculate from model data
        y_min_calc = float('inf')
        y_max_calc = -float('inf')
        has_data = False

        # Calculate global Y range from visible series
        for series_name, values in self.model._series_data.items():
            if not self.model.get_series_visibility(series_name):
                continue

            if values and len(values) > 0:
                has_data = True
                series_min = min(values)
                series_max = max(values)
                y_min_calc = min(y_min_calc, series_min)
                y_max_calc = max(y_max_calc, series_max)

        if has_data and y_min_calc != float('inf') and y_max_calc != -float(
                'inf'):
            # Add margin
            y_span = max(1e-6, y_max_calc - y_min_calc)
            y_margin = max(0.5, y_span * 0.1)
            self.plot.axis_y.setRange(y_min_calc - y_margin * 0.5,
                                      y_max_calc + y_margin)
        else:
            # Default range
            self.plot.axis_y.setRange(0.0, 10.0)

    # ==================== Utility Methods ====================

    def get_bar_set_names(self) -> List[str]:
        """Get list of all bar set names."""
        return list(self._bar_sets.keys())

    def get_categories(self) -> List[str]:
        """Get list of all categories."""
        return list(self.model.categories)

    def get_value(self, set_name: str, category: str) -> Optional[float]:
        """
        Get value for a specific bar.

        Args:
            set_name: Bar set name
            category: Category name

        Returns:
            Value if found, None otherwise
        """
        if category not in self.model.categories:
            return None

        category_index = self.model.categories.index(category)
        values = self.model.get_series_data(set_name)

        if values is None or category_index >= len(values):
            return None

        return values[category_index]

    def export_data(self) -> dict:
        """Export all data from the model."""
        data = {
            'categories': self.model.categories,
            'series': {}
        }

        for name in self.get_bar_set_names():
            values = self.model.get_series_data(name)
            data['series'][name] = {
                'values': list(values) if values else [],
                'color': self.model.get_series_color(name).name(),
                'visible': self.model.get_series_visibility(name)
            }

        return data

    def import_data(self, data: dict):
        """
        Import data into the chart.

        Args:
            data: Dictionary with 'categories' and 'series' keys
        """
        if 'categories' in data:
            self.set_categories(data['categories'])

        if 'series' in data:
            for name, series_data in data['series'].items():
                color = None
                if 'color' in series_data:
                    color = QColor(series_data['color'])

                self.add_bar_set(name, color)

                if 'values' in series_data:
                    self.set_values(name, series_data['values'])

                if 'visible' in series_data:
                    self.set_bar_set_visibility(name, series_data['visible'])

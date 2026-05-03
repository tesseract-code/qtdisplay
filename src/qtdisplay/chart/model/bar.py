from typing import List

import pandas as pd

from qtdisplay.chart.config import PlotConfig
from qtdisplay.chart.model.base import BaseChartModel, DirtyFlags


class BarChartModel(BaseChartModel[List[float]]):
    """
    Model for bar charts.
    Each series is a list of values for each category.
    """

    def __init__(self, config: PlotConfig) -> None:
        super().__init__(config)
        self.categories: List[str] = []  # Never None

    def _create_empty_series(self) -> List[float]:
        """Create empty series with zeros for each category."""
        return [0.0] * len(self.categories) if self.categories else []

    def set_value(self, series_name: str, category_index: int,
                  value: float) -> None:
        """Set value for a specific category in a series."""
        if series_name in self._series_data and 0 <= category_index < len(
                self.categories):
            # Ensure the series has the right length
            current_values = self._series_data[series_name]
            if len(current_values) != len(self.categories):
                # Resize series to match categories
                current_values = self._create_empty_series()
                self._series_data[series_name] = current_values

            current_values[category_index] = value
            self._mark_dirty(DirtyFlags.DATA, series_name)

    def set_values(self, series_name: str, values: List[float]) -> None:
        """Set all values for a series."""
        if series_name in self._series_data and len(values) == len(
                self.categories):
            self._series_data[series_name] = list(values)
            self._mark_dirty(DirtyFlags.DATA, series_name)

    def update_categories(self, categories: List[str]) -> None:
        """Update categories and resize all existing series."""
        old_len = len(self.categories)
        self.categories = list(categories)
        new_len = len(self.categories)

        # Resize all existing series to match new category count
        for series_name in list(self._series_data.keys()):
            current_values = self._series_data[series_name]
            if new_len > old_len:
                # Add zeros for new categories
                current_values.extend([0.0] * (new_len - old_len))
            elif new_len < old_len:
                # Truncate for fewer categories
                current_values = current_values[:new_len]
                self._series_data[series_name] = current_values

            self._mark_dirty(DirtyFlags.DATA, series_name)

    def _build_dataframe(self) -> pd.DataFrame:
        """Build DataFrame from current data."""
        if not self._series_data or not self.categories:
            return pd.DataFrame()

        return pd.DataFrame(
            {name: values for name, values in self._series_data.items()},
            index=self.categories
        )

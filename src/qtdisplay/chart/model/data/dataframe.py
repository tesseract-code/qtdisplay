"""
DataFrame Adapters for Chart Models
====================================

Normalizes different chart model DataFrame formats into table-friendly views.
Each adapter knows how to transform its model type's data into a displayable format.
"""

import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from typing import Optional
from PyQt6.QtCore import Qt

from qtdisplay.chart.model.base import (
    BaseChartModel
)
from qtdisplay.chart.model.pie import PieChartModel
from qtdisplay.chart.model.bar import BarChartModel
from qtdisplay.chart.model.area import AreaChartModel
from qtdisplay.chart.model.xy import XYChartModel


class DataFrameAdapter(ABC):
    """Base adapter for converting model data to table-friendly DataFrames."""

    @abstractmethod
    def get_dataframe(self, model: BaseChartModel) -> pd.DataFrame:
        """Get normalized DataFrame suitable for table display."""
        pass

    @abstractmethod
    def supports_x_highlighting(self) -> bool:
        """Whether this adapter supports X-position highlighting."""
        pass


class XYDataFrameAdapter(DataFrameAdapter):
    """Adapter for XY chart models (Line, Scatter, Spline)."""

    def get_dataframe(self, model: XYChartModel) -> pd.DataFrame:
        """
        Get DataFrame with X as index, series as columns.
        This is already the format XYChartModel provides.
        """
        return model.get_dataframe()

    def supports_x_highlighting(self) -> bool:
        return True


class AreaDataFrameAdapter(DataFrameAdapter):
    """Adapter for Area chart models."""

    def get_dataframe(self, model: AreaChartModel) -> pd.DataFrame:
        """
        Transform area chart data to table format.

        Creates a pivot with X as index, series as column groups with
        'Upper' and 'Lower' sub-columns.
        """
        if not model._series_data:
            return pd.DataFrame()

        # Collect all unique X values
        all_x = set()
        for series_data in model._series_data.values():
            upper_x, _ = series_data.get_upper_arrays()
            all_x.update(upper_x)

        if not all_x:
            return pd.DataFrame()

        x_values = np.sort(list(all_x))

        # Build columns: for each series, create Upper and Lower
        columns = []
        data_dict = {}

        for series_name in model.get_series_names():
            if not model.get_series_visibility(series_name):
                continue

            upper_col = f"{series_name}_Upper"
            lower_col = f"{series_name}_Lower"
            columns.extend([upper_col, lower_col])

            # Initialize with NaN
            data_dict[upper_col] = np.full(len(x_values), np.nan)
            data_dict[lower_col] = np.full(len(x_values), np.nan)

            # Get series arrays
            (upper_x, upper_y), (lower_x, lower_y) = model.get_series_arrays(
                series_name)

            # Map upper values
            for x, y in zip(upper_x, upper_y):
                idx = np.searchsorted(x_values, x)
                if idx < len(x_values) and x_values[idx] == x:
                    data_dict[upper_col][idx] = y

            # Map lower values
            for x, y in zip(lower_x, lower_y):
                idx = np.searchsorted(x_values, x)
                if idx < len(x_values) and x_values[idx] == x:
                    data_dict[lower_col][idx] = y

        df = pd.DataFrame(data_dict, index=x_values, columns=columns)
        df.index.name = 'X'
        return df

    def supports_x_highlighting(self) -> bool:
        return True


class BarDataFrameAdapter(DataFrameAdapter):
    """Adapter for Bar chart models."""

    def get_dataframe(self, model: BarChartModel) -> pd.DataFrame:
        """
        Get DataFrame with categories as index, series as columns.
        This is already the format BarChartModel provides.
        """
        return model.get_dataframe()

    def supports_x_highlighting(self) -> bool:
        return False  # Bar charts use categories, not continuous X


class PieDataFrameAdapter(DataFrameAdapter):
    """Adapter for Pie chart models."""

    def get_dataframe(self, model: PieChartModel) -> pd.DataFrame:
        """
        Get DataFrame with slice names as index, Value and Percentage columns.
        This is already the format PieChartModel provides, but we ensure
        only visible slices are shown.
        """
        df = model.get_dataframe()

        # Filter to only visible slices
        visible_mask = [
            model.get_series_visibility(name)
            for name in df.index
        ]

        return df[visible_mask]

    def supports_x_highlighting(self) -> bool:
        return False  # Pie charts don't have X positions


# Factory function
def get_dataframe_adapter(model: BaseChartModel) -> DataFrameAdapter:
    """
    Get appropriate adapter for a model type.

    Args:
        model: Chart model instance

    Returns:
        Appropriate DataFrameAdapter
    """
    if isinstance(model, XYChartModel):
        return XYDataFrameAdapter()
    elif isinstance(model, AreaChartModel):
        return AreaDataFrameAdapter()
    elif isinstance(model, BarChartModel):
        return BarDataFrameAdapter()
    elif isinstance(model, PieChartModel):
        return PieDataFrameAdapter()
    else:
        # Fallback: try to use model's get_dataframe directly
        return XYDataFrameAdapter()


# ============================================================================
# Integration with DataTableWidget
# ============================================================================

"""
Update DataTableWidget.set_data() to use adapter:

def set_data(self, model: BaseChartModel):
    '''Set data from chart model using appropriate adapter.'''
    # Get adapter for this model type
    adapter = get_dataframe_adapter(model)

    # Get normalized DataFrame
    dataframe = adapter.get_dataframe(model)

    # Store model reference for highlighting support
    self._current_model = model
    self._current_adapter = adapter

    # Update table
    self.dataframe = dataframe.copy()
    self.table_model.update_data(self.dataframe)
    self._update_statistics()


def highlight_x_position(self, x_value: float):
    '''Highlight row corresponding to x value.'''
    # Only highlight if adapter supports it
    if not hasattr(self, '_current_adapter'):
        return

    if not self._current_adapter.supports_x_highlighting():
        return

    # Existing highlighting logic...
"""
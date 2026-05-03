import pandas as pd

from qtdisplay.chart.model.base import BaseChartModel, DirtyFlags


class PieChartModel(BaseChartModel[float]):
    """
    Model for pie charts.
    Each series is a single value representing a slice.
    """

    def _create_empty_series(self) -> float:
        """Create empty series with zero value."""
        return 0.0

    def set_value(self, series_name: str, value: float) -> None:
        """Set value for a series (slice)."""
        if series_name in self._series_data:
            old_value = self._series_data[series_name]
            if abs(old_value - value) > 1e-9:  # Avoid unnecessary updates
                self._series_data[series_name] = value
                self._mark_dirty(DirtyFlags.DATA | DirtyFlags.RANGE,
                                 series_name)

    def get_total(self) -> float:
        """Get sum of all visible series values."""
        return sum(
            value for name, value in self._series_data.items()
            if self._series_visibility.get(name, True)  # Default to visible
        )

    def get_percentage(self, series_name: str) -> float:
        """Get percentage for a specific series."""
        value = self._series_data.get(series_name, 0.0)
        total = self.get_total()
        return (value / total * 100) if total > 0 else 0.0

    def _build_dataframe(self) -> pd.DataFrame:
        """Build DataFrame from current data."""
        if not self._series_data:
            return pd.DataFrame()

        total = self.get_total()
        data = {
            'Value': list(self._series_data.values()),
            'Percentage': [self.get_percentage(name) for name in
                           self._series_data.keys()]
        }

        return pd.DataFrame(data, index=list(self._series_data.keys()))

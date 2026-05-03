import logging
from typing import Dict, Optional, List, Any

from PyQt6.QtCharts import (QPieSeries, QPieSlice)
from PyQt6.QtCore import pyqtSlot
from PyQt6.QtGui import QColor

from cross_platform.qt6_utils.chart.config import PlotConfig
from cross_platform.qt6_utils.chart.controller.base import BaseChartController
from cross_platform.qt6_utils.chart.model.pie import PieChartModel

logger = logging.getLogger(__name__)


class QPieChartController(BaseChartController):
    """Optimized controller for pie charts with full model integration."""

    def __init__(self, config: PlotConfig,
                 model: Optional[PieChartModel] = None):
        # Type-safe model initialization
        if model is None:
            model = PieChartModel(config)
        elif not isinstance(model, PieChartModel):
            raise TypeError(
                f"Expected PieChartModel, got {type(model).__name__}")

        super().__init__(config, model)

        self._slices: Dict[str, QPieSlice] = {}
        self._pie_series = QPieSeries()

        # Configure pie series
        self._pie_series.setLabelsVisible(True)
        self._pie_series.setLabelsPosition(
            QPieSlice.LabelPosition.LabelInsideNormal)

        self.plot.chart.addSeries(self._pie_series)

        # Connect slice signals for interactivity
        self._pie_series.hovered.connect(self._on_slice_hovered)

        logger.debug(
            f"{self.__class__.__name__}: Initialized with {len(model.get_series_names())} slices")

    def add_slice(self, label: str, value: float,
                  color: Optional[QColor] = None) -> bool:
        """Add a slice that tracks model data."""
        if label in self._slices:
            logger.warning(f"Slice '{label}' already exists")
            return False

        try:
            self.model.add_series(label, color)
            self.model.set_value(label, value)
            logger.debug(f"Added slice: {label} = {value}")
            return True
        except Exception as e:
            logger.error(f"Failed to add slice '{label}': {e}")
            return False

    def set_slice_value(self, label: str, value: float) -> bool:
        """Update slice value via model."""
        if label not in self.model.get_series_names():
            return self.add_slice(label, value)

        try:
            self.model.set_value(label, value)
            return True
        except Exception as e:
            logger.error(f"Failed to set slice value '{label}': {e}")
            return False

    def get_slice_value(self, label: str) -> Optional[float]:
        """Get value for a specific slice."""
        return self.model.get_series_data(label)

    def remove_slice(self, label: str) -> bool:
        """Remove a slice from the chart."""
        if label in self._slices:
            try:
                self.model.remove_series(label)
                logger.debug(f"Removed slice: {label}")
                return True
            except Exception as e:
                logger.error(f"Failed to remove slice '{label}': {e}")
        return False

    def set_slice_visibility(self, label: str, visible: bool) -> None:
        """Toggle slice visibility."""
        if label in self._slices:
            self._slices[label].setVisible(visible)
        self.model.set_series_visibility(label, visible)

    def set_slice_exploded(self, label: str, exploded: bool = True,
                           explode_distance: float = 0.1) -> None:
        """Explode or collapse a slice."""
        if slice_obj := self._slices.get(label):
            slice_obj.setExploded(exploded)
            slice_obj.setExplodeDistanceFactor(explode_distance)

    def set_slice_label_visible(self, label: str, visible: bool) -> None:
        """Toggle label visibility for a slice."""
        if slice_obj := self._slices.get(label):
            slice_obj.setLabelVisible(visible)

    def set_all_labels_visible(self, visible: bool) -> None:
        """Toggle label visibility for all slices."""
        for slice_obj in self._slices.values():
            slice_obj.setLabelVisible(visible)

    # ==================== Signal Handlers ====================

    def _on_series_added(self, series_name: str) -> None:
        """Handle when a series (slice) is added to the model."""
        if series_name in self._slices:
            return

        value = self.model.get_series_data(series_name) or 0.0
        color = self.model.get_series_color(series_name)

        # Create and configure slice
        slice_obj = QPieSlice(series_name, value)

        if color:
            slice_obj.setColor(color)

        # Enhanced slice configuration
        slice_obj.setLabelVisible(True)
        slice_obj.setBorderColor(QColor(255, 255, 255))
        slice_obj.setBorderWidth(2)
        slice_obj.setLabelBrush(QColor(0, 0, 0))  # Black labels for readability

        # Connect slice signals
        slice_obj.hovered.connect(
            lambda state, s=slice_obj: self._on_slice_hovered(s, state))
        slice_obj.clicked.connect(lambda: self._on_slice_clicked(slice_obj))

        # Add to visualization
        self._pie_series.append(slice_obj)
        self._slices[series_name] = slice_obj

        self._update_slice_label(series_name)

    @pyqtSlot()
    def _on_data_changed(self) -> None:
        """Update pie slices when model data changes - optimized version."""
        # Only update dirty slices for efficiency
        dirty_series = self.model.get_dirty_series()

        for series_name in dirty_series:
            if series_name in self._slices:
                self._update_slice_data(series_name)

        self.plot.view.viewport().update()

    def _on_series_removed(self, series_name: str) -> None:
        """Handle when a series (slice) is removed from the model."""
        if slice_obj := self._slices.pop(series_name, None):
            try:
                self._pie_series.remove(slice_obj)
                # Proper cleanup of Qt object
                slice_obj.deleteLater()
            except Exception as e:
                logger.error(f"Error removing slice '{series_name}': {e}")

    def _update_slice_data(self, series_name: str) -> None:
        """Update a single slice's data and label."""
        if slice_obj := self._slices.get(series_name):
            value = self.model.get_series_data(series_name)
            if value is not None:
                slice_obj.setValue(value)
                self._update_slice_label(series_name)

    def _update_slice_label(self, series_name: str) -> None:
        """Update slice label with value and percentage."""
        if slice_obj := self._slices.get(series_name):
            value = self.model.get_series_data(series_name) or 0.0
            percentage = self.model.get_percentage(series_name)

            label = (f"{series_name}\n"
                     f"{value:.{self.config.precision}f} "
                     f"({percentage:.1f}%)")
            slice_obj.setLabel(label)

    def _on_slice_hovered(self, slice_obj: QPieSlice, state: bool) -> None:
        """Handle slice hover events."""
        if state:
            slice_obj.setExploded(True)
            slice_obj.setExplodeDistanceFactor(0.1)
        else:
            slice_obj.setExploded(False)

    def _on_slice_clicked(self, slice_obj: QPieSlice) -> None:
        """Handle slice click events."""
        logger.debug(f"Slice clicked: {slice_obj.label()}")

    # ==================== Range Management ====================

    def _update_axes_range(self, *args, **kwargs) -> None:
        """No-op for pie charts (no axes)."""
        pass

    # ==================== Enhanced Utility Methods ====================

    def get_slice_names(self) -> List[str]:
        """Get list of all slice labels."""
        return list(self._slices.keys())

    def get_total_value(self) -> float:
        """Get sum of all visible slice values."""
        return self.model.get_total()

    def get_slice_percentage(self, label: str) -> float:
        """Get percentage for a specific slice."""
        return self.model.get_percentage(label)

    def set_hole_size(self, size: float) -> None:
        """Set the size of the center hole (for donut charts)."""
        self._pie_series.setHoleSize(max(0.0, min(1.0, size)))

    def set_pie_size(self, size: float) -> None:
        """Set the overall size of the pie."""
        self._pie_series.setPieSize(max(0.0, min(1.0, size)))

    def clear_all_slices(self) -> None:
        """Remove all slices from the chart."""
        for slice_name in list(self._slices.keys()):
            self.remove_slice(slice_name)

    # ==================== Data Serialization ====================

    def export_data(self) -> Dict[str, Any]:
        """Export all data from the model."""
        return {
            'slices': {
                name: {
                    'value': self.model.get_series_data(name),
                    'percentage': self.get_slice_percentage(name),
                    'color': self.model.get_series_color(
                        name).name() if self.model.get_series_color(
                        name) else None,
                    'visible': self.model.get_series_visibility(name),
                    'exploded': self._slices[
                        name].isExploded() if name in self._slices else False
                }
                for name in self.get_slice_names()
            },
            'total': self.get_total_value(),
            'metadata': {
                'slice_count': len(self._slices),
                'hole_size': self._pie_series.holeSize(),
                'pie_size': self._pie_series.pieSize()
            }
        }

    def import_data(self, data: Dict[str, Any]) -> bool:
        """Import data into the chart with error handling."""
        try:
            if 'slices' not in data:
                return False

            # Clear existing data
            self.clear_all_slices()

            # Import new slices
            for name, slice_data in data['slices'].items():
                color = QColor(slice_data['color']) if slice_data.get(
                    'color') else None
                value = slice_data.get('value', 0.0)

                if self.add_slice(name, value, color):
                    # Set additional properties
                    if 'visible' in slice_data:
                        self.set_slice_visibility(name, slice_data['visible'])

                    if slice_data.get('exploded', False):
                        self.set_slice_exploded(name, True)

            # Restore visual settings
            if 'metadata' in data:
                self.set_hole_size(data['metadata'].get('hole_size', 0.0))
                self.set_pie_size(data['metadata'].get('pie_size', 1.0))

            logger.debug(f"Imported {len(data['slices'])} slices")
            return True

        except Exception as e:
            logger.error(f"Failed to import data: {e}")
            return False

    # ==================== Context Manager Support ====================

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Cleanup resources."""
        self.clear_all_slices()

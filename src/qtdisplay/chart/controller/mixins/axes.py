"""
AxesMixin - Modular Axes Management
====================================

Responsibilities:
- Axes lifecycle and configuration
- Axes display settings (titles, ticks, padding, grid)
- Alignment-based axis lookup
- Range updates with padding

Python 3.12 Features:
- type statement for type aliases
- Match/case for alignment handling
- Optimized lookups using alignment flags as keys
- Generic type parameters for better type safety
"""
from typing import Optional, Protocol
from dataclasses import dataclass

from PyQt6.QtCharts import (QAbstractAxis, QValueAxis, QDateTimeAxis,
    QLogValueAxis)
from PyQt6.QtCore import Qt

from qtdisplay.chart.config import (AxesConfig,
                                    AxesDisplaySettings)
from qtdisplay.chart.model.base import BaseChartModel

# Python 3.12 type aliases with improved clarity
type AxisAlignment = Qt.AlignmentFlag
type AxesMap = dict[AxisAlignment, QAbstractAxis]
type AxesSettingsMap = dict[AxisAlignment, AxesDisplaySettings]


@dataclass(frozen=True, slots=True)
class PaddingState:
    """
    Immutable padding state for efficient comparison.

    Using frozen dataclass allows hash-based comparison and
    slots reduces memory overhead (Python 3.10+).
    """
    min_ratio: float
    max_ratio: float

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PaddingState):
            return NotImplemented
        return (self.min_ratio == other.min_ratio and
                self.max_ratio == other.max_ratio)


@dataclass(frozen=True, slots=True)
class RangeState:
    """
    Immutable range state for tracking axis bounds.

    Prevents unnecessary range updates when data hasn't changed.
    """
    min_value: float
    max_value: float
    padding: PaddingState

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RangeState):
            return NotImplemented
        return (self.min_value == other.min_value and
                self.max_value == other.max_value and
                self.padding == other.padding)


def get_axis_display_name(alignment: AxisAlignment) -> str:
    """
    Get human-readable name for axis alignment.

    Uses match/case for clean, optimized dispatch (Python 3.10+).
    """
    match alignment:
        case Qt.AlignmentFlag.AlignTop:
            return "X Axis (Top)"
        case Qt.AlignmentFlag.AlignBottom:
            return "X Axis (Bottom)"
        case Qt.AlignmentFlag.AlignLeft:
            return "Y Axis (Left)"
        case Qt.AlignmentFlag.AlignRight:
            return "Y Axis (Right)"
        case _:
            return f"Axis ({alignment.name})"


class AxesControllerProtocol(Protocol):
    """Protocol defining what AxesMixin needs from parent controller."""
    axes_map: AxesMap
    axes_display_settings: AxesSettingsMap
    axes_config: Optional[AxesConfig]

    def _create_axes(self, axes_config: AxesConfig) -> None: ...

type SeriesName = str

class AxesMixin:
    """
    Mixin providing axes management capabilities.

    Handles axes lifecycle, display settings, and alignment-based lookups.
    Optimized for O(1) access using alignment flags as dict keys.

    State Management:
    - Uses immutable PaddingState and RangeState for efficient comparison
    - Tracks last applied state per axis to prevent redundant updates
    - Only applies changes when state actually differs
    """

    def _init_axes_management(self) -> None:
        """Initialize axes management structures. Call from __init__."""
        self.models: dict[SeriesName, BaseChartModel] = {}
        self.axes_map: AxesMap = {}
        self.axes_display_settings: AxesSettingsMap = {}
        self.axes_config: Optional[AxesConfig] = None

        # Track last applied state per axis for change detection
        self._axis_padding_state: dict[AxisAlignment, PaddingState] = {}
        self._axis_range_state: dict[AxisAlignment, RangeState] = {}

    # ========================================================================
    # PUBLIC API - Axes Lifecycle
    # ========================================================================

    def setup_axes(self, axes_config: AxesConfig) -> None:
        """
        Setup chart axes based on immutable configuration.

        Args:
            axes_config: Structural axes configuration
        """
        self.axes_config = axes_config
        self._create_axes(axes_config)

        # Store axes by alignment (O(1) lookups)
        if hasattr(self.plot, 'axis_x'):
            self.axes_map[axes_config.axisX_alignment] = self.plot.axis_x

        if hasattr(self.plot, 'axis_y'):
            self.axes_map[axes_config.axisY_alignment] = self.plot.axis_y

        # Initialize default display settings
        for alignment, axis in self.axes_map.items():
            if alignment not in self.axes_display_settings:
                settings = AxesDisplaySettings()
                self.axes_display_settings[alignment] = settings
            else:
                settings = self.axes_display_settings[alignment]
            self._apply_axis_display_settings(axis, alignment, settings)

    def get_axis(self, alignment: AxisAlignment) -> Optional[QAbstractAxis]:
        """Get axis at specified alignment (O(1) lookup)."""
        return self.axes_map.get(alignment)

    def get_axes_by_orientation(
            self,
            orientation: Qt.Orientation
    ) -> list[QAbstractAxis]:
        """
        Get all axes with specified orientation.

        Args:
            orientation: Horizontal or Vertical

        Returns:
            List of axes matching orientation
        """
        horizontal_alignments = {
            Qt.AlignmentFlag.AlignTop,
            Qt.AlignmentFlag.AlignBottom
        }

        if orientation == Qt.Orientation.Horizontal:
            return [
                axis for alignment, axis in self.axes_map.items()
                if alignment in horizontal_alignments
            ]
        else:
            return [
                axis for alignment, axis in self.axes_map.items()
                if alignment not in horizontal_alignments
            ]

    # ========================================================================
    # PUBLIC API - Display Settings
    # ========================================================================

    def get_axis_display_settings(
            self,
            alignment: AxisAlignment
    ) -> Optional[AxesDisplaySettings]:
        """Get display settings for axis at alignment."""
        return self.axes_display_settings.get(alignment)

    def set_axis_display_settings(
            self,
            alignment: AxisAlignment,
            settings: AxesDisplaySettings
    ) -> None:
        """
        Set and apply display settings for axis.

        Args:
            alignment: Axis position (AlignLeft, AlignRight, etc.)
            settings: Display settings to apply

        Raises:
            ValueError: If no axis at specified alignment
        """
        if alignment not in self.axes_map:
            raise ValueError(f"No axis found at alignment: {alignment}")

        self.axes_display_settings[alignment] = settings
        axis = self.axes_map[alignment]
        self._apply_axis_display_settings(axis, alignment, settings)

    def get_all_axes_display_settings(self) -> AxesSettingsMap:
        """Get display settings for all axes."""
        # Ensure all axes have settings
        for alignment in self.axes_map:
            if alignment not in self.axes_display_settings:
                self.axes_display_settings[alignment] = AxesDisplaySettings()
        return dict(self.axes_display_settings)

    def set_all_axes_display_settings(
            self,
            settings_map: AxesSettingsMap
    ) -> None:
        """Set display settings for multiple axes."""
        for alignment, settings in settings_map.items():
            if alignment in self.axes_map:
                self.set_axis_display_settings(alignment, settings)

    def update_axes_range_with_padding(
            self,
            x_min: float,
            x_max: float,
            y_min: float,
            y_max: float
    ) -> None:
        """
        Update axes ranges with padding from display settings.

        Applies padding based on each axis's padding_ratio setting.
        Only updates ranges when values actually change.
        """
        # Get horizontal axes
        x_axes = self.get_axes_by_orientation(Qt.Orientation.Horizontal)
        for axis in x_axes:
            alignment = self._get_alignment_for_axis(axis)
            if alignment and alignment in self.axes_display_settings:
                settings = self.axes_display_settings[alignment]
                self._apply_range_with_padding(
                    axis, alignment, x_min, x_max, settings
                )

        # Get vertical axes
        y_axes = self.get_axes_by_orientation(Qt.Orientation.Vertical)
        print("Y axes: ", y_axes)
        series = list(self.models.keys())
        for idx, axis in enumerate(y_axes):

            # TODO: sets the same axes for multiple axes
            #  Remove blue from model default list
            alignment = self._get_alignment_for_axis(axis)
            if alignment and alignment in self.axes_display_settings:
                settings = self.axes_display_settings[alignment]
                serie = series[idx]
                model = self.models[serie]
                range: tuple = model.get_series_data_range(serie)
                xmin, xmax, y_min, y_max = range
                self._apply_range_with_padding(
                    axis, alignment, y_min, y_max, settings
                )

    # ========================================================================
    # INTERNAL - Settings Application
    # ========================================================================

    def _apply_range_with_padding(
            self,
            axis: QAbstractAxis,
            alignment: AxisAlignment,
            data_min: float,
            data_max: float,
            settings: AxesDisplaySettings
    ) -> None:
        """
        Apply range with padding to numerical axes.

        Uses immutable state comparison to avoid redundant updates.

        Args:
            axis: Axis to update
            alignment: Axis alignment for state tracking
            data_min: Minimum data value
            data_max: Maximum data value
            settings: Display settings containing padding ratios
        """
        # Only apply to numerical axis types
        if not isinstance(axis, (QValueAxis, QLogValueAxis)):
            return

        data_range = data_max - data_min
        if data_range <= 0:
            return

        # Create immutable state for comparison
        new_padding = PaddingState(
            min_ratio=settings.axis_padding_ratio_min,
            max_ratio=settings.axis_padding_ratio_max
        )
        new_range = RangeState(
            min_value=data_min,
            max_value=data_max,
            padding=new_padding
        )

        # Check if state changed using immutable comparison
        last_state = self._axis_range_state.get(alignment)
        if last_state == new_range:
            return  # No change needed - O(1) comparison via frozen dataclass

        # Apply the new range with asymmetric padding
        padding_min = data_range * settings.axis_padding_ratio_min
        padding_max = data_range * settings.axis_padding_ratio_max
        axis.setRange(data_min - padding_min, data_max + padding_max)

        # Store new state for future comparisons
        self._axis_range_state[alignment] = new_range

    def _apply_axis_display_settings(
            self,
            axis: QAbstractAxis,
            alignment: AxisAlignment,
            settings: AxesDisplaySettings
    ) -> None:
        """
        Apply display settings to Qt axis.

        Args:
            axis: The axis to configure
            alignment: Axis alignment for state tracking
            settings: Display settings to apply
        """
        axis.setTitleText(settings.get_formatted_title())

        if isinstance(axis, (QValueAxis, QDateTimeAxis)):
            axis.setTickCount(max(2, settings.axis_tick_count))

        axis.setGridLineVisible(settings.show_gridline)

        # Apply padding only if changed
        if isinstance(axis, (QValueAxis, QLogValueAxis)):
            new_padding = PaddingState(
                min_ratio=settings.axis_padding_ratio_min,
                max_ratio=settings.axis_padding_ratio_max
            )

            last_padding = self._axis_padding_state.get(alignment)
            if last_padding != new_padding:
                current_min = axis.min()
                current_max = axis.max()

                data_range = current_max - current_min
                if data_range > 0:
                    padding_min = data_range * settings.axis_padding_ratio_min
                    padding_max = data_range * settings.axis_padding_ratio_max
                    axis.setRange(
                            current_min - padding_min,
                            current_max + padding_max
                        )

                self._axis_padding_state[alignment] = new_padding

    def _get_alignment_for_axis(
            self,
            axis: QAbstractAxis
    ) -> Optional[AxisAlignment]:
        """
        Get alignment for axis using O(1) reverse lookup.

        Note: Could be optimized with a bidirectional map if this
        becomes a performance bottleneck.
        """
        for alignment, mapped_axis in self.axes_map.items():
            if mapped_axis is axis:
                return alignment
        return None
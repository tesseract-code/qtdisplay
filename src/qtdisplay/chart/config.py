"""Configurations"""

from dataclasses import dataclass
from typing import Optional, Literal

from PyQt6.QtCharts import QChart, QAbstractSeries, QAbstractAxis
from PyQt6.QtCore import Qt

from cross_platform.qt6_utils.qtgui.src.qtgui.color_picker import Color, to_qcolor
from qtgui.form.group import form_group


@dataclass(frozen=True)
class DataProcessingConfig:  # Renamed!
    """Algorithm configuration - determines processing pipeline."""
    downsample: bool = True
    downsample_factor: int = 1

    remove_anomalies: bool = True
    anomaly_window_size: int = 15
    anomaly_std_threshold: float = 3.0
    anomaly_method: Literal['std_threshold', 'iqr', 'mad'] = 'std_threshold'

    apply_smoothing: bool = True
    smoothing_window_size: int = 5
    smoothing_method: Literal['mean', 'median', 'ewm'] = 'mean'

    keep_original_timestamps: bool = False

    def __post_init__(self):
        """Validate after frozen dataclass initialization."""
        if self.anomaly_window_size < 1:
            raise ValueError("anomaly_window_size must be at least 1")
        if self.smoothing_window_size < 1:
            raise ValueError("smoothing_window_size must be at least 1")
        if self.anomaly_std_threshold < 0:
            raise ValueError("anomaly_std_threshold must be non-negative")


@dataclass(frozen=True)
class PlotConfig:
    """Structural configuration - cannot change after initialization."""
    is_real_time: bool = False
    max_points: int = 1000
    batch_ms: Optional[int] = None
    data_process_config: Optional[DataProcessingConfig] = None


@dataclass(frozen=True)
class AxesConfig:
    """Axes structural configuration - cannot change after creation."""
    axisX_type: QAbstractAxis.AxisType = QAbstractAxis.AxisType.AxisTypeValue
    axisY_type: QAbstractAxis.AxisType = QAbstractAxis.AxisType.AxisTypeValue
    axisX_alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignBottom
    axisY_alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft


@dataclass(frozen=True)
class SeriesConfig:
    """Series structural configuration."""
    name: str
    series_type: QAbstractSeries.SeriesType  # No need for protection, frozen!
    axes_config: AxesConfig


# ============================================================================
# SETTINGS (frozen=False) - User preferences, mutable
# ============================================================================

@form_group("Series Appearance",
            "Visual properties of the series",
            ["name", "color", "line_width", "marker_size", "visible"])
@dataclass(frozen=False)
class SeriesDisplaySettings:
    """Series display properties - user adjustable."""
    color: Optional[Color] = None  # Auto-generated if None
    line_width: int = 2
    marker_size: int = 10
    visible: bool = True

    def __post_init__(self):
        """Convert color to QColor if needed."""
        if self.color is not None:
            self.color = to_qcolor(self.color)


@form_group("Chart Information",
            "Basic chart identification and display",
            ["title", "theme", "legend_alignment"])
@form_group("Rendering",
            "Visual rendering and performance settings",
            ["antialiasing", "animated"])
@form_group("Component Visibility",
            "Control which chart components are displayed",
            ["show_legend", "show_trackline", "show_background"])
@dataclass(frozen=False)
class ChartDisplaySettings:
    """Chart appearance settings - user adjustable."""
    title: str = "Chart"
    animated: bool = False
    antialiasing: bool = True
    show_legend: bool = True
    show_trackline: bool = True
    show_background: bool = True
    theme: Optional[QChart.ChartTheme] = QChart.ChartTheme.ChartThemeLight
    legend_alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignTop


@form_group("Axis Identification",
            "Set the title and measurement unit for this axis",
            ["axis_title", "axis_unit"])
@form_group("Tick Configuration",
            "Control the number and spacing of tick marks on this axis",
            ["axis_tick_count"])
@form_group("Padding and Spacing",
            "Adjust padding ratio to add space at the boundaries of this axis (as fraction of data range)",
            ["axis_padding_ratio"])
@dataclass(frozen=False)
class AxesDisplaySettings:
    """
    Display settings for a single chart axis with asymmetric padding support.

    Attributes:
        axis_title: Display title for this axis (e.g., "Time", "Temperature")
        axis_unit: Measurement unit (e.g., "seconds", "°C", "meters")
        axis_tick_count: Number of major tick marks (minimum: 2)
        axis_padding_ratio_min: Fraction of data range to add as bottom/left padding (0.0-1.0)
        axis_padding_ratio_max: Fraction of data range to add as top/right padding (0.0-1.0)
        show_gridline: Whether to show grid lines for this axis
    """

    axis_title: str = ""
    axis_unit: str = ""
    axis_tick_count: int = 4
    axis_padding_ratio_min: float = 0.0  # No padding at minimum by default
    axis_padding_ratio_max: float = 0.00  # Small padding at maximum by default
    show_gridline: bool = True

    def get_formatted_title(self) -> str:
        """Get title formatted with unit if present."""
        if self.axis_unit:
            return f"{self.axis_title} ({self.axis_unit})"
        return self.axis_title

    def clone(self) -> 'AxesDisplaySettings':
        """
        Create a deep copy of these settings.

        Returns:
            New AxesDisplaySettings instance with same values
        """
        return AxesDisplaySettings(
            axis_title=self.axis_title,
            axis_unit=self.axis_unit,
            axis_tick_count=self.axis_tick_count,
            axis_padding_ratio_min=self.axis_padding_ratio_min,
            axis_padding_ratio_max=self.axis_padding_ratio_max
        )

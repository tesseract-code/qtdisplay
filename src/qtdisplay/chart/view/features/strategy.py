"""
Optimized Chart Interaction Strategies with NumPy acceleration.

This module provides high-performance chart interaction strategies using
vectorized operations for spatial searching and nearest-neighbor queries.
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict

from PyQt6.QtCore import QPointF, QPoint, QRectF
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QToolTip

from qtdisplay.chart.model.data import points

logger = logging.getLogger(__name__)


class ChartInteractionStrategy(ABC):
    """
    Abstract strategy for chart-specific interactions.

    Each chart type implements only the interactions that make sense for it.
    """

    def __init__(self, view: 'BaseChartView'):
        self.view = view
        self.cache_manager = points.PointCacheManager()
        self.point_finder = points.NearestPointFinder()

    @abstractmethod
    def supports_crosshair(self) -> bool:
        """Whether this chart type supports crosshair tracking."""
        pass

    @abstractmethod
    def supports_tooltips(self) -> bool:
        """Whether this chart type supports tooltips."""
        pass

    def supports_zoom(self) -> bool:
        """Whether this chart type supports zooming."""
        return not self.view.is_real_time

    def supports_panning(self) -> bool:
        """Whether this chart type supports panning."""
        return not self.view.is_real_time

    def handle_wheel_zoom(self, event, mouse_scene: QPointF) -> bool:
        """
        Handle wheel zoom event.

        Returns:
            True if event was handled, False otherwise.
        """
        return self.view.is_real_time

    def handle_mouse_move_tooltip(self, chart_pos: QPointF,
                                  global_pos: QPoint) -> Optional[str]:
        """
        Generate tooltip text for current position.

        Returns:
            Tooltip HTML string or None.
        """
        return None

    def draw_overlay(self, painter: QPainter, plot_area: QRectF):
        """Draw chart-specific overlay elements."""
        pass

    def show_tooltip(self, tooltip_text: str, global_pos: QPoint):
        """Show tooltip with custom styling using palette shadow color."""
        if tooltip_text:
            stylesheet = f"""
                QToolTip {{
                    background-color: palette(base);
                    color: palette(text);
                }}
                """
            self.view.setStyleSheet(stylesheet)
            QToolTip.showText(global_pos, tooltip_text, self.view)

    def invalidate_cache(self, series_name: Optional[str] = None):
        """
        Invalidate point cache for series.

        Args:
            series_name: Specific series to invalidate, or None for all
        """
        self.cache_manager.invalidate(series_name)

    def get_cache_stats(self) -> Dict:
        """Get cache statistics for debugging/monitoring."""
        return self.cache_manager.get_stats()



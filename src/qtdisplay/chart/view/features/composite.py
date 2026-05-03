from typing import Dict, Optional, Set

from PyQt6.QtCharts import (QLineSeries, QSplineSeries, QAreaSeries,
                            QScatterSeries, QBarSeries, QPieSeries)
from PyQt6.QtCore import QPointF, QPoint, QRectF
from PyQt6.QtGui import QPainter

from pycore.log.ctx import with_logger
from qtdisplay.chart.view.features.area import AreaChartStrategy
from qtdisplay.chart.view.features.bar import BarChartStrategy
from qtdisplay.chart.view.features.pie import PieChartStrategy
from qtdisplay.chart.view.features.strategy import (
    ChartInteractionStrategy)
from qtdisplay.chart.view.features.xy import (XYChartStrategy,
                                                             ScatterChartStrategy)


@with_logger
class CompositeChartStrategy(ChartInteractionStrategy):
    """
    Stateless composite that dynamically delegates to series-type strategies.

    No registration needed - inspects chart.series() to determine what's active.
    Strategies are created lazily and cached by series type.
    """

    # Map series types to strategy classes
    _STRATEGY_MAP = {
        QLineSeries: XYChartStrategy,
        QSplineSeries: XYChartStrategy,
        QAreaSeries: AreaChartStrategy,
        QScatterSeries: ScatterChartStrategy,
        QBarSeries: BarChartStrategy,
        QPieSeries: PieChartStrategy,
    }

    def __init__(self, view: 'BaseChartView'):
        super().__init__(view)  # Initialize parent ABC
        # Cache strategies by their class (not series type, since some share)
        self._strategy_cache: Dict[type, ChartInteractionStrategy] = {}

    # ========================================================================
    # CAPABILITIES (computed from chart's current series)
    # ========================================================================

    def supports_crosshair(self) -> bool:
        """Check if any active series type supports crosshair."""
        for strategy in self._get_active_strategies():
            if strategy.supports_crosshair():
                return True
        return False

    def supports_tooltips(self) -> bool:
        """Check if any active series type supports tooltips."""
        for strategy in self._get_active_strategies():
            if strategy.supports_tooltips():
                return True
        return False

    def supports_zoom(self) -> bool:
        """Check if any active series type supports zoom."""
        for strategy in self._get_active_strategies():
            if strategy.supports_zoom():
                return True
        return False

    def supports_panning(self) -> bool:
        """Check if any active series type supports panning."""
        for strategy in self._get_active_strategies():
            if strategy.supports_panning():
                return True
        return False

    # ========================================================================
    # INTERACTION HANDLERS
    # ========================================================================

    def handle_wheel_zoom(self, event, mouse_scene: QPointF) -> bool:
        """Delegate to first zoom-capable strategy."""
        for strategy in self._get_active_strategies():
            if strategy.supports_zoom():
                return strategy.handle_wheel_zoom(event, mouse_scene)
        return False

    def handle_mouse_move_tooltip(self, chart_pos: QPointF,
                                  global_pos: QPoint) -> Optional[str]:
        """
        Aggregate tooltips from all active strategies.

        Each strategy's handle_mouse_move_tooltip already filters to its
        own series types, so we just collect and combine results.
        """
        active_strategies = self._get_active_strategies()
        if not active_strategies:
            return None

        tooltip_parts = []

        # Let each strategy contribute (they filter their own series)
        for strategy in active_strategies:
            if not strategy.supports_tooltips():
                continue

            strategy_tooltip = strategy.handle_mouse_move_tooltip(
                chart_pos, global_pos
            )

            if strategy_tooltip:
                tooltip_parts.append(strategy_tooltip)

        if not tooltip_parts:
            return None

        # Combine (remove duplicate headers if present)
        combined = "<br>".join(tooltip_parts)
        return self._deduplicate_header(combined)

    def draw_overlay(self, painter: QPainter, plot_area: QRectF):
        """Delegate overlay drawing to all active strategies."""
        for strategy in self._get_active_strategies():
            strategy.draw_overlay(painter, plot_area)

    def invalidate_cache(self, series_name: Optional[str] = None):
        """Invalidate caches across all strategies."""
        for strategy in self._strategy_cache.values():
            strategy.invalidate_cache(series_name)

    def get_cache_stats(self) -> Dict:
        """Get aggregated cache statistics."""
        return {
            strategy_class.__name__: strategy.get_cache_stats()
            for strategy_class, strategy in self._strategy_cache.items()
        }

    # ========================================================================
    # INTERNAL
    # ========================================================================

    def _get_active_strategies(self) -> list[ChartInteractionStrategy]:
        """
        Get strategies for currently active series types.

        Inspects chart.series() and returns appropriate strategies.
        Creates and caches strategies lazily.
        """
        # Get unique series types currently in chart
        series_types = self._get_active_series_types()

        if not series_types:
            return []

        # Get or create strategy for each type
        strategies = []
        for series_type in series_types:
            strategy = self._get_strategy_for_type(series_type)
            if strategy:
                strategies.append(strategy)

        return strategies

    def _get_active_series_types(self) -> Set[type]:
        """Get set of unique series types currently in chart."""
        return {type(series) for series in self.view.chart().series()}

    def _get_strategy_for_type(
            self,
            series_type: type
    ) -> Optional[ChartInteractionStrategy]:
        """
        Get strategy for a series type, creating if needed.

        Multiple series types can share a strategy class (e.g., Line and Spline
        both use XYChartStrategy), so we cache by strategy class not series type.
        """
        strategy_class = self._STRATEGY_MAP.get(series_type)
        if not strategy_class:
            self._logger.warning(f"No strategy mapping for"
                                 f" {series_type.__name__}")
            return None

        # Return cached or create new
        if strategy_class not in self._strategy_cache:
            self._strategy_cache[strategy_class] = strategy_class(self.view)
            self._logger.debug(
                f"Created {strategy_class.__name__} for {series_type.__name__}")

        return self._strategy_cache[strategy_class]

    def _deduplicate_header(self, combined_tooltip: str) -> str:
        """
        Remove duplicate X position headers from combined tooltips.

        Each strategy may include "<b>X: ...</b>" - keep only first occurrence.
        """
        lines = combined_tooltip.split("<br>")
        seen_header = False
        result = []

        for line in lines:
            # Check if this is a position header
            if line.startswith("<b>X:") and "</b>" in line:
                if not seen_header:
                    result.append(line)
                    seen_header = True
                # Skip duplicate headers
            else:
                result.append(line)

        return "<br>".join(result)


def create_chart_strategy(chart_type: str,
                          view: 'BaseChartView') -> ChartInteractionStrategy:
    """
    Factory function to create appropriate strategy based on chart type.

    Args:
        chart_type: Type identifier ('line', 'area', 'scatter', 'bar', 'pie')
        view: Chart view instance

    Returns:
        Appropriate ChartInteractionStrategy instance

    Raises:
        ValueError: If chart_type is not recognized
    """
    strategies = {
        'line': XYChartStrategy,
        'spline': XYChartStrategy,
        'area': AreaChartStrategy,
        'scatter': ScatterChartStrategy,
        'bar': BarChartStrategy,
        'pie': PieChartStrategy,
    }

    strategy_class = strategies.get(chart_type.lower())
    if strategy_class is None:
        raise ValueError(f"Unknown chart type: {chart_type}")

    return strategy_class(view)

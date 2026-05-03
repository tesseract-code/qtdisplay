import logging

from PyQt6.QtCharts import QAbstractSeries

from qtdisplay.chart.config import PlotConfig
from qtdisplay.chart.model.area import AreaChartModel
from qtdisplay.chart.model.bar import BarChartModel
from qtdisplay.chart.model.base import BaseChartModel
from qtdisplay.chart.model.pie import PieChartModel
from qtdisplay.chart.model.xy import (LineChartModel, ScatterChartModel,
                                      SplineChartModel, XYChartModel)


logger = logging.getLogger(__name__)

def get_chart_model_type(series_type: QAbstractSeries.SeriesType):
    match series_type:
        case QAbstractSeries.SeriesType.SeriesTypePie:
            return PieChartModel
        case QAbstractSeries.SeriesType.SeriesTypeBar:
            return BarChartModel
        case QAbstractSeries.SeriesType.SeriesTypeArea:
            return AreaChartModel
        case QAbstractSeries.SeriesType.SeriesTypeLine:
            return LineChartModel
        case QAbstractSeries.SeriesType.SeriesTypeScatter:
              return ScatterChartModel
        case QAbstractSeries.SeriesType.SeriesTypeSpline:
            return SplineChartModel
        case _:  # Default case
            raise ValueError(f'Chart type {series_type} not '
                             f'supported')


def get_chart_model(series_type: QAbstractSeries.SeriesType, config: PlotConfig):
    match series_type:
        case QAbstractSeries.SeriesType.SeriesTypePie:
            return PieChartModel(config)
        case QAbstractSeries.SeriesType.SeriesTypeBar:
            return BarChartModel(config)
        case QAbstractSeries.SeriesType.SeriesTypeArea:
            return AreaChartModel(config)
        case QAbstractSeries.SeriesType.SeriesTypeLine | (
        QAbstractSeries.SeriesType.SeriesTypeScatter) | (
             QAbstractSeries.SeriesType.SeriesTypeSpline):
            return XYChartModel(config)
        case _:  # Default case
            raise ValueError(f'Chart type {series_type} not '
                             f'supported')


def validate_chart_model(chart_type: QAbstractSeries.SeriesType,
                         model: BaseChartModel):
    match chart_type:
        case QAbstractSeries.SeriesType.SeriesTypePie:
            if not isinstance(model, PieChartModel):
                msg = (f'Invalid model, {model.__class__.__name__}, '
                       f'for pie chart')
                logger.error(msg)
                raise ValueError(msg)

        case QAbstractSeries.SeriesType.SeriesTypeBar:
            if not isinstance(model, BarChartModel):
                msg = (f'Invalid model, {model.__class__.__name__}, '
                       f'for bar chart')
                logger.error(msg)
                raise ValueError(msg)

        case QAbstractSeries.SeriesType.SeriesTypeArea:
            if not isinstance(model, AreaChartModel):
                msg = (f'Invalid model, {model.__class__.__name__}, '
                       f'for area chart')
                logger.error(msg)
                raise ValueError(msg)
        case QAbstractSeries.SeriesType.SeriesTypeLine | (
        QAbstractSeries.SeriesType.SeriesTypeScatter) | (
             QAbstractSeries.SeriesType.SeriesTypeSpline):
            if not isinstance(model, XYChartModel):
                msg = (f'Invalid model, {model.__class__.__name__}, '
                       f'for XY charts')
                logger.error(msg)
                raise ValueError(msg)
        case _:  # Default case
            raise ValueError(f'Model {model.__class__.__name__} not supported')

import math
from PyQt6.QtCore import QDateTime, QTimeZone


def timestamp_to_qdatetime(ts: float) -> QDateTime:
    """
    Convert a Unix timestamp (float, seconds since epoch) to a QDateTime.

    Precision contract:
        QDateTime stores time as whole milliseconds internally.
        Sub-millisecond precision in `ts` is therefore truncated — not rounded —
        so that round-tripping never drifts forward in time.
        The returned QDateTime is always in UTC.

    Args:
        ts: Unix timestamp in seconds (e.g. 1_700_000_000.123456).
            May be negative (pre-1970) or larger than 2^31 (post-2038).

    Returns:
        QDateTime set to UTC, accurate to the millisecond.

    Raises:
        ValueError: if ts is NaN or ±Inf (not representable as a point in time).
        OverflowError: if ts cannot fit in a 64-bit millisecond counter
                       (~±2.9 × 10^11 years from epoch).
    """
    if not math.isfinite(ts):
        raise ValueError(f"timestamp must be finite, got {ts!r}")

    # Truncate (floor toward -∞) to avoid rounding into the future.
    ms = math.floor(ts * 1_000)

    # QDateTime.fromMSecsSinceEpoch accepts a 64-bit signed integer,
    # covering roughly ±292 million years — more than enough for any real use.
    MAX_MS =  9_223_372_036_854_775_807   # INT64_MAX
    MIN_MS = -9_223_372_036_854_775_808   # INT64_MIN
    if not (MIN_MS <= ms <= MAX_MS):
        raise OverflowError(f"timestamp {ts} cannot be represented as 64-bit ms")

    return QDateTime.fromMSecsSinceEpoch(ms, QTimeZone.utc())


def qdatetime_to_timestamp(dt: QDateTime) -> float:
    """
    Convert a QDateTime to a Unix timestamp (float, seconds since epoch).

    Precision contract:
        QDateTime stores time as whole milliseconds.  This method exposes
        that full millisecond resolution as a float without further loss,
        because every integer up to 2^53 is exactly representable in a
        64-bit IEEE-754 double — and realistic ms-since-epoch values are
        well below that ceiling (~1.7 × 10^12 ms as of 2024).

    Args:
        dt: Any valid QDateTime (local, UTC, or offset-based).
            Invalid QDateTimes (QDateTime()) are rejected.

    Returns:
        Unix timestamp in seconds as a float, e.g. 1_700_000_000.123.

    Raises:
        ValueError: if `dt` is not valid (i.e. QDateTime() default-constructed
                    or otherwise null).
    """
    if not dt.isValid():
        raise ValueError("Cannot convert an invalid (null) QDateTime to a timestamp")

    # toMSecsSinceEpoch normalises any timezone to UTC internally,
    # so local / offset-based QDateTimes convert correctly.
    ms: int = dt.toMSecsSinceEpoch()

    # Exact integer division preserves the full millisecond precision.
    return ms / 1_000.0

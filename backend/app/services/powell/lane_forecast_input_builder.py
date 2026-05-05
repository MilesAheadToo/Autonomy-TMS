"""LaneForecastInputBuilder — assembles `LaneForecastInput` per lane
from observed shipment history. Closes the §3.40 production gap:
``TacticalForecastService.publish_forecast`` had no production caller
because nothing built its input list.

For each lane that had shipment activity in the lookback window,
this builder:

  1. Aggregates ``outbound_order_line.shipped_quantity`` per lane ×
     period (weekly buckets by default).
  2. Computes the Syntetos-Boylan classification inputs that
     ``LaneVolumeForecastState`` consumes (mean / std / ADI / CV² /
     trend / nonzero-period fraction).
  3. Builds a proposed forecast as a simple trailing-window mean.
     The L1 ``LaneVolumeForecastTRM`` re-classifies and re-routes to
     Holt-Winters / LightGBM / Croston / TSB / AutoETS based on the
     state, so a naïve proposed forecast here is fine — it's the
     baseline the TRM sees, not the final published forecast.
  4. Returns a ``[LaneForecastInput]`` ready to thread into
     ``TacticalForecastService.publish_forecast``.

The builder is intentionally minimal — it produces the *minimum
viable* input to make the cascade run end-to-end. Richer feature
engineering (rate-card covariates, market-tightness signals,
explicit seasonal-decomposition) is a follow-up workstream.

Per CLAUDE.md: this is decision-policy plumbing (transforms
canonical state into a form the L1 TRM consumes), so it lives in
TMS, not Core.
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.powell.tactical_forecast_service import LaneForecastInput
from autonomy_tms_heuristics.library import LaneVolumeForecastState

logger = logging.getLogger(__name__)


_TRAILING_WINDOW_PERIODS = 4
"""Number of recent periods averaged to produce the proposed forecast.
Small window — the L1 TRM owns the "real" forecast method choice;
this is just the baseline state the TRM sees."""

_DEFAULT_HISTORY_WEEKS = 26
"""How far back to read shipment history. Half a year is enough to
classify Syntetos-Boylan demand patterns reliably; less risks
mis-classifying a smooth lane as NEW (cold-start)."""

_PERIOD_DAYS_DEFAULT = 7


class LaneForecastInputBuilder:
    """Service that produces ``LaneForecastInput`` rows from
    historical shipments.

    Stateless beyond the ``Session`` parameter on each call. Designed
    to run synchronously inside the L3 cascade as Stage 0, before the
    TRM publishes ``LaneVolumePlan`` rows.
    """

    def __init__(
        self,
        *,
        period_days: int = _PERIOD_DAYS_DEFAULT,
        history_weeks: int = _DEFAULT_HISTORY_WEEKS,
    ) -> None:
        if period_days <= 0 or period_days > 365:
            raise ValueError("period_days must be in (0, 365]")
        if history_weeks <= 0 or history_weeks > 260:
            raise ValueError("history_weeks must be in (0, 260]")
        self.period_days = period_days
        self.history_weeks = history_weeks

    def build_inputs(
        self,
        db: Session,
        *,
        tenant_id: int,
        config_id: int,
        period_start: date,
    ) -> List[LaneForecastInput]:
        """Build ``LaneForecastInput`` rows for every lane with
        shipment activity in the lookback window.

        :param tenant_id: tenant scope (used at write time; doesn't
            filter the query because outbound_order_line is
            config-scoped, not tenant-scoped, in the AWS SC DM).
        :param config_id: config_id scope for the shipment + lane reads.
        :param period_start: the *target* forecast period (inclusive
            start). The lookback window ends just before this date.

        Returns ``[]`` when no lane has activity — the cascade will
        skip publish gracefully.
        """
        # Lazy imports — keep module-load cost low. Same pattern as
        # other powell services.
        from azirella_data_model.master import (
            OutboundOrderLine,
            TransportationLane,
        )

        lookback_start = period_start - timedelta(days=self.period_days * self.history_weeks)
        lookback_end = period_start  # exclusive

        # Pull per (lane, period_start) shipment totals over the
        # lookback window. Period-start truncation uses
        # ``date_trunc('week', ...)`` semantics — close enough for
        # weekly buckets, and SQLite tolerates it via DATE arithmetic.
        rows = self._fetch_period_aggregates(
            db,
            config_id=config_id,
            lookback_start=lookback_start,
            lookback_end=lookback_end,
        )
        if not rows:
            logger.info(
                "LaneForecastInputBuilder: no shipment history for "
                "config=%s window=%s..%s — no inputs built",
                config_id, lookback_start, lookback_end,
            )
            return []

        # Bucket per lane.
        by_lane: dict = {}
        for row in rows:
            by_lane.setdefault(row.lane_id, []).append(
                _PeriodObservation(
                    period_start=row.period_start,
                    units=float(row.units),
                )
            )

        inputs: List[LaneForecastInput] = []
        for lane_id, observations in by_lane.items():
            # Sort observations chronologically.
            observations.sort(key=lambda o: o.period_start)
            state = self._build_state(
                lane_id=lane_id,
                period_start=period_start,
                observations=observations,
            )
            inputs.append(LaneForecastInput(lane_id=lane_id, state=state))
        return inputs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_period_aggregates(
        self,
        db: Session,
        *,
        config_id: int,
        lookback_start: date,
        lookback_end: date,
    ) -> list:
        """Per `(lane_id, period_start)` shipment-quantity sum across
        the lookback window. Period bucketing rounds ship dates DOWN
        to the nearest period_days boundary anchored on
        ``lookback_start``.
        """
        from azirella_data_model.master import (
            OutboundOrderLine,
            TransportationLane,
        )

        # We bucket by (first_ship_date - lookback_start) // period_days
        # in Python after the query — keeps the SQL portable across
        # SQLite and Postgres without dialect-specific date_trunc.
        stmt = (
            select(
                TransportationLane.id.label("lane_id"),
                OutboundOrderLine.first_ship_date.label("ship_date"),
                func.sum(OutboundOrderLine.shipped_quantity).label("units"),
            )
            .join(
                TransportationLane,
                (TransportationLane.from_site_id == OutboundOrderLine.site_id)
                & (
                    TransportationLane.to_site_id
                    == OutboundOrderLine.market_demand_site_id
                ),
            )
            .where(
                OutboundOrderLine.config_id == config_id,
                TransportationLane.config_id == config_id,
                OutboundOrderLine.first_ship_date >= lookback_start,
                OutboundOrderLine.first_ship_date < lookback_end,
                OutboundOrderLine.shipped_quantity > 0,
            )
            .group_by(TransportationLane.id, OutboundOrderLine.first_ship_date)
        )
        raw = db.execute(stmt).all()

        # Re-bucket per (lane, period_start) where period_start is the
        # period boundary that contains ship_date.
        bucketed: dict = {}
        for row in raw:
            offset_days = (row.ship_date - lookback_start).days
            bucket_index = offset_days // self.period_days
            bucket_start = lookback_start + timedelta(
                days=bucket_index * self.period_days,
            )
            key = (row.lane_id, bucket_start)
            bucketed[key] = bucketed.get(key, 0.0) + float(row.units)

        # Materialise into row-like objects — match the shape the
        # caller expects (lane_id / period_start / units attrs).
        result = [
            _AggregateRow(
                lane_id=lane_id,
                period_start=period_start,
                units=units,
            )
            for (lane_id, period_start), units in bucketed.items()
        ]
        return result

    def _build_state(
        self,
        *,
        lane_id: int,
        period_start: date,
        observations: Sequence["_PeriodObservation"],
    ) -> LaneVolumeForecastState:
        """Compute the Syntetos-Boylan classification + state fields
        from a chronologically-sorted observation list."""
        # Fill gaps: weeks with zero shipments are missing from the
        # query (no rows to sum). Reconstruct a dense series so ADI
        # / CV² are computed correctly.
        if not observations:
            return self._empty_state(lane_id=lane_id, period_start=period_start)

        first_period = observations[0].period_start
        last_period = observations[-1].period_start
        n_periods = max(
            1,
            ((last_period - first_period).days // self.period_days) + 1,
        )
        dense = [0.0] * n_periods
        for obs in observations:
            idx = (obs.period_start - first_period).days // self.period_days
            if 0 <= idx < n_periods:
                dense[idx] = obs.units

        weeks_of_history = n_periods
        nonzero = [v for v in dense if v > 0]
        nonzero_count = len(nonzero)
        nonzero_period_pct = nonzero_count / n_periods if n_periods else 0.0

        if nonzero:
            mean_demand = statistics.fmean(nonzero)
            demand_std = (
                statistics.pstdev(nonzero) if nonzero_count >= 2 else 0.0
            )
        else:
            mean_demand = 0.0
            demand_std = 0.0

        # ADI: average periods between non-zero demand events.
        if nonzero_count >= 2:
            indices = [i for i, v in enumerate(dense) if v > 0]
            gaps = [indices[i + 1] - indices[i] for i in range(len(indices) - 1)]
            avg_demand_interval = (
                statistics.fmean(gaps) if gaps else 1.0
            )
        else:
            avg_demand_interval = 1.0

        # CV² of non-zero demand.
        if nonzero_count >= 2 and mean_demand > 0:
            squared_cv = (demand_std / mean_demand) ** 2
        else:
            squared_cv = 0.0

        # Linear trend slope on the dense series — naive least-squares
        # (slope-only). Sufficient for SMOOTH/DECLINING classification.
        trend_slope = _linear_slope(dense)

        # Last observed period (most recent non-zero).
        last_period_actual = nonzero[-1] if nonzero else 0.0

        # Proposed forecast: trailing-window mean of the last
        # ``_TRAILING_WINDOW_PERIODS``. The L1 TRM re-routes to its
        # chosen method based on the Syntetos-Boylan class; this is
        # only the baseline state.
        tail = dense[-_TRAILING_WINDOW_PERIODS:] if dense else []
        proposed_forecast_p50 = (
            statistics.fmean(tail) if tail else 0.0
        )
        # Naive bands: P50 ± stdev. Real bands come from the L1 TRM.
        if tail and len(tail) >= 2:
            tail_std = statistics.pstdev(tail)
        else:
            tail_std = 0.0
        proposed_forecast_p10 = max(0.0, proposed_forecast_p50 - tail_std)
        proposed_forecast_p90 = proposed_forecast_p50 + tail_std

        return LaneVolumeForecastState(
            lane_id=lane_id,
            period_start=period_start,
            period_days=self.period_days,
            weeks_of_history=weeks_of_history,
            mean_demand=mean_demand,
            demand_std=demand_std,
            avg_demand_interval=avg_demand_interval,
            squared_cv=squared_cv,
            nonzero_period_pct=nonzero_period_pct,
            trend_slope=trend_slope,
            seasonal_strength=0.0,  # Not computed; L1 TRM owns
            is_peak_season=False,
            proposed_forecast_p10=proposed_forecast_p10,
            proposed_forecast_p50=proposed_forecast_p50,
            proposed_forecast_p90=proposed_forecast_p90,
            proposed_method="HoltWinters",
            last_period_actual=last_period_actual,
            forecast_interval_width_pct=(
                (proposed_forecast_p90 - proposed_forecast_p10) / proposed_forecast_p50
                if proposed_forecast_p50 > 0 else 0.0
            ),
        )

    def _empty_state(
        self, *, lane_id: int, period_start: date,
    ) -> LaneVolumeForecastState:
        """State for a lane with no observations — the L1 TRM will
        classify as NEW and DEFER."""
        return LaneVolumeForecastState(
            lane_id=lane_id,
            period_start=period_start,
            period_days=self.period_days,
            weeks_of_history=0,
        )


# ---------------------------------------------------------------------------
# Internal dataclasses + helpers
# ---------------------------------------------------------------------------


@dataclass
class _PeriodObservation:
    period_start: date
    units: float


@dataclass
class _AggregateRow:
    lane_id: int
    period_start: date
    units: float


def _linear_slope(values: Sequence[float]) -> float:
    """Slope of a least-squares line fit to ``values`` indexed by
    [0, 1, 2, ...]. Returns 0 for fewer than 2 points or perfectly
    horizontal series.

    Centered formulation — numerator is Σ(x - x̄)(y - ȳ), denominator
    Σ(x - x̄)². Avoids overflow on long series and matches what
    statsmodels' OLS slope produces for a single regressor.
    """
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = 0.0
    den = 0.0
    for i, v in enumerate(values):
        dx = i - x_mean
        num += dx * (v - y_mean)
        den += dx * dx
    if den == 0:
        return 0.0
    return num / den


__all__ = [
    "LaneForecastInputBuilder",
]

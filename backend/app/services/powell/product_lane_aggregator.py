"""ProductLaneAggregator — populates Core's ``ProductLane`` from
historical ``outbound_order_line`` shipments.

Companion to §3.45 ``LaneVolumeLifecycleReactor``. The reactor consumes
``ProductLane.volume_share`` rows; this service is the writer that
populates them from observed shipment history. Without this aggregator
running, the reactor returns no overlay and the L1 forecaster runs
without lifecycle reactivity (the no-fallbacks design choice in
§3.40 Phase 3b).

Algorithm
---------
For one ``(tenant_id, config_id, period_start, period_end)`` window:

  1. JOIN ``outbound_order_line`` × ``transportation_lane`` on
     ``oo.site_id = lane.from_site_id`` AND
     ``oo.market_demand_site_id = lane.to_site_id``.
  2. Filter to lines with ``first_ship_date`` in
     ``[period_start, period_end)`` (left-inclusive, right-exclusive
     to align with weekly bucket conventions).
  3. ``GROUP BY (lane_id, product_id)``, sum
     ``shipped_quantity`` → ``volume_units``.
  4. Compute lane totals (``SUM(volume_units) GROUP BY lane_id``).
  5. ``volume_share = product_units / lane_total`` per row.
  6. Upsert ``ProductLane`` rows with
     ``source=ProductLaneSource.OBSERVED_HISTORY``. Existing rows for
     the same logical key are updated; older sources
     (``FORECAST_AGGREGATED``) are *not* touched (different `source`
     means different unique-key tuple per the §3.43 design).

Why upsert instead of insert: nightly schedules re-run the same
periods until the shipment history stabilises, and each re-run should
produce a single OBSERVED_HISTORY row per ``(tenant, lane, product,
period_start, period_end)`` tuple — not a growing pile.

Cadence
-------
Designed to run nightly (e.g., 2am after the day's shipment data
settles), aggregating the *previous N weeks* to capture late-arriving
or corrected order data. Default ``weeks_back=4`` is conservative —
shipment data is usually settled within 7 days but late corrections
(returns, billing adjustments) can arrive over weeks.

Per-tenant invocation: each tenant aggregated separately, scoped by
``config_id``. The caller (typically a celery beat task or APScheduler
job) iterates tenants.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AggregationResult:
    """What ``aggregate_period`` returns."""

    tenant_id: int
    config_id: int
    period_start: date
    period_end: date
    rows_written: int
    """Number of ProductLane rows upserted (one per
    (lane, product) tuple with non-zero shipped_quantity in the window)."""
    lanes_affected: int
    """Distinct lanes touched by the upsert."""
    total_volume: float
    """Sum of all volume_units written. Surfaceable in monitoring as a
    sanity-check signal — a sudden order-of-magnitude swing usually
    means a data-pipeline upstream regression."""


class ProductLaneAggregator:
    """Service that aggregates historical shipments into per-lane
    product-mix rows.

    Stateless beyond the ``Session`` parameter on each call; safe to
    instantiate once per process and reuse, or instantiate per
    invocation — both work.
    """

    def __init__(self, *, period_days: int = 7) -> None:
        if period_days <= 0 or period_days > 365:
            raise ValueError("period_days must be in (0, 365]")
        self.period_days = period_days

    @staticmethod
    def _compute_shares(
        per_pair: list[tuple[int, str, float]],
    ) -> list[tuple[int, str, float, float]]:
        """Pure helper. Given ``[(lane_id, product_id, units), ...]``,
        return ``[(lane_id, product_id, units, share), ...]`` where
        ``share = units / lane_total``. Lanes with zero or negative
        total are dropped silently (caller should already filter, but
        the helper handles it defensively).

        Extracted as a pure function so the share-arithmetic can be
        tested standalone without the SQL JOIN setup.
        """
        lane_totals: dict[int, float] = {}
        for lane_id, _, units in per_pair:
            lane_totals[lane_id] = lane_totals.get(lane_id, 0.0) + float(units)
        out: list[tuple[int, str, float, float]] = []
        for lane_id, product_id, units in per_pair:
            total = lane_totals.get(lane_id, 0.0)
            if total <= 0:
                continue
            out.append((lane_id, product_id, float(units), float(units) / total))
        return out

    def aggregate_period(
        self,
        db: Session,
        *,
        tenant_id: int,
        config_id: int,
        period_start: date,
        period_end: date,
    ) -> AggregationResult:
        """Aggregate one period and write ``ProductLane`` rows."""
        if period_start >= period_end:
            raise ValueError(
                f"period_start ({period_start}) must be < "
                f"period_end ({period_end})"
            )

        # Lazy import — keep the module-load cost low, especially
        # important for celery workers that don't otherwise need the
        # Core ORM at import time.
        # TransportationLane lives in master.supply_chain_config, not
        # entities (entities.py has a comment pointing at this).
        from azirella_data_model.master import (
            OutboundOrderLine,
            TransportationLane,
        )
        from azirella_data_model.transport_plan import (
            ProductLane,
            ProductLaneSource,
        )

        # Step 1+2+3: per (lane, product) aggregation for the window.
        per_pair_stmt = (
            select(
                TransportationLane.id.label("lane_id"),
                OutboundOrderLine.product_id.label("product_id"),
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
                OutboundOrderLine.first_ship_date >= period_start,
                OutboundOrderLine.first_ship_date < period_end,
                OutboundOrderLine.shipped_quantity > 0,
            )
            .group_by(TransportationLane.id, OutboundOrderLine.product_id)
        )
        per_pair_rows: list[tuple[int, str, float]] = [
            (row.lane_id, row.product_id, float(row.units))
            for row in db.execute(per_pair_stmt).all()
        ]

        if not per_pair_rows:
            logger.info(
                "ProductLaneAggregator: no shipments to aggregate for "
                "tenant=%s config=%s period=%s → %s",
                tenant_id, config_id, period_start, period_end,
            )
            return AggregationResult(
                tenant_id=tenant_id,
                config_id=config_id,
                period_start=period_start,
                period_end=period_end,
                rows_written=0,
                lanes_affected=0,
                total_volume=0.0,
            )

        # Step 4+5: compute shares via the pure helper (tested
        # standalone in test_product_lane_aggregator.py).
        share_rows = self._compute_shares(per_pair_rows)

        # Step 6: upsert.
        rows_written = 0
        total_volume = 0.0
        lanes_affected: set[int] = set()
        now = datetime.utcnow()
        for lane_id, product_id, volume_units, volume_share in share_rows:
            lanes_affected.add(lane_id)

            # Upsert: look up existing row by the unique key, update
            # in place if found, otherwise insert.
            existing_stmt = select(ProductLane).where(
                ProductLane.tenant_id == tenant_id,
                ProductLane.lane_id == lane_id,
                ProductLane.product_id == product_id,
                ProductLane.period_start == period_start,
                ProductLane.period_end == period_end,
                ProductLane.source == ProductLaneSource.OBSERVED_HISTORY,
            )
            existing = db.execute(existing_stmt).scalar_one_or_none()
            if existing is not None:
                existing.volume_units = volume_units
                existing.volume_share = volume_share
                existing.last_updated_at = now
            else:
                db.add(
                    ProductLane(
                        tenant_id=tenant_id,
                        lane_id=lane_id,
                        product_id=product_id,
                        period_start=period_start,
                        period_end=period_end,
                        volume_units=volume_units,
                        volume_share=volume_share,
                        source=ProductLaneSource.OBSERVED_HISTORY,
                        confidence=1.0,
                    )
                )
            rows_written += 1
            total_volume += volume_units

        db.flush()

        return AggregationResult(
            tenant_id=tenant_id,
            config_id=config_id,
            period_start=period_start,
            period_end=period_end,
            rows_written=rows_written,
            lanes_affected=len(lanes_affected),
            total_volume=total_volume,
        )

    def aggregate_recent(
        self,
        db: Session,
        *,
        tenant_id: int,
        config_id: int,
        weeks_back: int = 4,
        as_of: Optional[date] = None,
    ) -> list[AggregationResult]:
        """Aggregate the previous ``weeks_back`` periods ending at
        ``as_of`` (defaults to today UTC).

        Period boundaries align to ``self.period_days``; for the
        default ``period_days=7`` and ``as_of=today``, the periods are
        ``[today - 7N, today - 7(N-1))`` for ``N = weeks_back .. 1``.

        Returns one ``AggregationResult`` per period, in chronological
        order.
        """
        if weeks_back <= 0 or weeks_back > 52:
            raise ValueError("weeks_back must be in (0, 52]")
        if as_of is None:
            as_of = datetime.utcnow().date()

        results: list[AggregationResult] = []
        for offset in range(weeks_back, 0, -1):
            ps = as_of - timedelta(days=self.period_days * offset)
            pe = as_of - timedelta(days=self.period_days * (offset - 1))
            results.append(
                self.aggregate_period(
                    db,
                    tenant_id=tenant_id,
                    config_id=config_id,
                    period_start=ps,
                    period_end=pe,
                )
            )
        return results


__all__ = ["AggregationResult", "ProductLaneAggregator"]

"""Phase-2 tenant calibration for ``Phase1ShipmentGenerator``.

PR-6 of [docs/TWIN_REWRITE_PLAN.md](../../../../docs/TWIN_REWRITE_PLAN.md).
Mirrors the SCP Phase-2 pattern (see Autonomy-SCP's
``backend/app/services/seasonal_envelope_service.py`` and Core's
``MIGRATION_REGISTER.md §3.20``): walk the tenant's realised history,
fit per-channel and per-lane parameters from it, and return a
``Phase1ShipmentGenerator`` configured with those fitted values
instead of bootstrap defaults.

The module is split into three layers:

1. **Pure fitter** (``fit_phase2_shipment_generator``) — runs against
   any ``Iterable[HistoricalShipment]``. Pure function, deterministic
   given the same input. Tested against synthetic data.
2. **DB loaders** (``load_history_for_config`` async,
   ``load_history_for_config_sync`` sync) — thin adapters that read
   ``TransferOrder`` + ``TransferOrderLineItem`` rows for a config
   and map them to ``HistoricalShipment``. Two flavours so both
   async and sync provisioning paths can use the same fitter.
3. **Provisioning helpers** (``fit_phase2_for_config``,
   ``load_phase2_generator_for_config``) — sync functions for the
   ``_step_training_corpus`` provisioning hook. ``fit_phase2_for_config``
   fits + persists the seasonal envelopes via
   ``SeasonalEnvelopeRecord``; ``load_phase2_generator_for_config``
   reverses that — re-walks history, reads persisted envelopes, and
   returns a fully-configured generator (mirrors SCP's
   ``load_envelopes_for_simulator``).

The fit pulls four parameter classes out of the same history walk:

1. **Channel discovery.** ``candidate_lanes``,
   ``candidate_products``, and ``candidate_units`` come from the
   distinct values seen in the history. The generator's
   ``cartesian-product channel iteration`` is now cabined to channels
   that actually trade.
2. **Per-channel base volume.** ``base_volumes[(origin, dest, product)]``
   is the mean line-item quantity per occupied bucket. Sparse channels
   (fewer than ``min_buckets_for_volume_fit`` occupied buckets) fall
   back to ``default_base_volume`` rather than absorbing one outlier.
3. **Per-lane seasonal envelope.** For each lane (origin, dest) with
   ``>= min_full_periods_for_seasonal`` full annual cycles of data, fit
   a ``SeasonalEnvelope`` via Core's ``fit_seasonal_envelope`` on the
   weekly aggregated lane volume. Lanes with insufficient history fall
   through to the parametric ``envelope_spread`` (the bootstrap path
   stays available for the long tail of low-traffic lanes).
4. **Per-product unit override.** When a product is consistently
   shipped in the same UOM across history, that UOM becomes the
   ``product_unit_overrides`` value. Mixed-UOM products keep the
   default rotation.

Outputs are stamped with ``PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE``
so consumers can route between Phase-1 and Phase-2 envelopes (via
``TransferOrderEnvelope.produced_by`` / ``phase_indicator``) for
calibration-drift monitoring.

This module is the **pure, in-memory fitter**. The DB-side loader that
reads ``TransferOrder`` + ``TransferOrderLineItem`` rows and maps them
into ``HistoricalShipment`` is at the bottom of this file as
``load_history_for_config``; it's a thin async wrapper over the pure
fit. Provisioning-step integration (calling ``load_history_for_config``
+ ``fit_phase2_shipment_generator`` from a tenant-bootstrap step) is a
follow-up — the substrate ships here.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Mapping, Sequence

from azirella_data_model.stochastic.seasonal import (
    SeasonalEnvelope,
    SeriesKind,
    fit_seasonal_envelope,
)
from azirella_demand_planning_contract import Tier

from .shipment_generator import (
    Phase1ShipmentGenerator,
    lane_series_key,
)


logger = logging.getLogger(__name__)


PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE = "tms:to_arrival_phase2:v0.1.0"
"""Producer signature stamped on ``TransferOrderEnvelope.produced_by`` by
generators built from this fitter. Bump the version suffix when the
fit logic changes meaningfully so consumers can invalidate caches.

The matching ``TransferOrderEnvelope.phase_indicator`` is derived
automatically by ``Phase1ShipmentGenerator._phase_indicator`` — it
reads the ``tms:to_arrival_phase2`` prefix and emits
``PhaseIndicator.TENANT_CALIBRATED``.
"""


# ── Inputs ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HistoricalShipment:
    """One TO-line-item-shaped fact, normalised for fitting.

    Independent of TMS's SQLAlchemy ORM so the fitter can be exercised
    against synthetic data in unit tests, against ERP-extracted
    ``TransferOrderLineItem`` rows in provisioning, or against any
    other history source.

    Site IDs are the canonical AWS SC ``site.id`` String form (e.g.
    ``"site:42"``). The DB-side loader handles Integer → String
    conversion before reaching the fitter.
    """

    origin_site_id: str
    destination_site_id: str
    product_id: str
    shipment_date: date
    quantity: float
    unit: str = "each"

    def __post_init__(self) -> None:
        if not self.origin_site_id:
            raise ValueError("origin_site_id must be non-empty")
        if not self.destination_site_id:
            raise ValueError("destination_site_id must be non-empty")
        if not self.product_id:
            raise ValueError("product_id must be non-empty")
        if self.quantity < 0:
            raise ValueError(
                f"quantity must be >= 0; got {self.quantity}"
            )


# ── Tunable fit parameters ───────────────────────────────────────────


@dataclass
class FitParameters:
    """Tunable knobs for the Phase-2 fit.

    Defaults are chosen so a year of weekly TO history fits cleanly. The
    knobs surface so a follow-up provisioning step can scale them per
    tenant if needed (e.g. Food Dist's ``3y`` history → larger
    ``min_full_periods_for_seasonal`` for tighter seasonal fits).
    """

    tier: Tier = Tier.TACTICAL
    """Implies the bucket size used to aggregate the history. TACTICAL
    means weekly buckets — matches the seasonal envelope's default
    ``period=52``."""

    seasonal_period: int = 52
    """Seasonal envelope period. 52 = weekly × annual (default).
    Match-paired with ``tier=TACTICAL``."""

    min_full_periods_for_seasonal: float = 1.5
    """A lane needs at least this many full annual cycles of weekly
    aggregated history before its envelope is fitted. Below the
    threshold, the lane falls through to ``envelope_spread`` and the
    fit logs a fallback warning."""

    min_buckets_for_volume_fit: int = 4
    """A channel needs this many occupied buckets before ``base_volumes``
    is set; otherwise the channel inherits ``default_base_volume``."""

    fallback_envelope_spread: tuple[float, float] = (0.7, 1.3)
    """Used by lanes that lack enough history to fit a seasonal
    envelope. Same shape Phase-1 uses by default."""

    default_base_volume: float = 10.0
    """Used by sparse channels."""

    seed: int = 42
    """Forwarded to the ``Phase1ShipmentGenerator`` jitter so different
    tenant fits remain deterministic per-tenant."""


# ── Fitter ───────────────────────────────────────────────────────────


def fit_phase2_shipment_generator(
    history: Iterable[HistoricalShipment],
    *,
    params: FitParameters | None = None,
) -> Phase1ShipmentGenerator:
    """Fit a Phase-2 ``Phase1ShipmentGenerator`` from realised history.

    Returns a ``Phase1ShipmentGenerator`` whose parameters come from the
    history walk and whose ``producer_signature`` is the Phase-2
    constant — so envelopes it emits self-identify as tenant-calibrated.

    Empty or single-record histories return a Phase-1-shaped fallback
    (no candidates, no base_volumes, no envelopes — same as the bootstrap
    stub) so callers don't have to special-case the empty path.
    """
    p = params or FitParameters()
    rows = list(history)

    if not rows:
        logger.info(
            "fit_phase2_shipment_generator: empty history, returning empty generator"
        )
        return Phase1ShipmentGenerator(
            producer_signature=PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE,
            seed=p.seed,
        )

    candidate_lanes = _discover_lanes(rows)
    candidate_products = _discover_products(rows)
    candidate_units = _discover_units(rows)
    product_unit_overrides = _fit_unit_overrides(rows)

    base_volumes = _fit_base_volumes(
        rows,
        min_buckets=p.min_buckets_for_volume_fit,
    )

    seasonal_envelopes = _fit_seasonal_envelopes(
        rows,
        seasonal_period=p.seasonal_period,
        min_full_periods=p.min_full_periods_for_seasonal,
    )

    return Phase1ShipmentGenerator(
        candidate_lanes=candidate_lanes,
        candidate_products=candidate_products,
        candidate_units=candidate_units,
        base_volumes=base_volumes,
        default_base_volume=p.default_base_volume,
        seasonal_envelopes=seasonal_envelopes,
        envelope_spread=p.fallback_envelope_spread,
        seed=p.seed,
        product_unit_overrides=product_unit_overrides,
        producer_signature=PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE,
    )


# ── Internal helpers ─────────────────────────────────────────────────


def _discover_lanes(rows: Sequence[HistoricalShipment]) -> list[tuple[str, str]]:
    """Distinct ``(origin, destination)`` pairs in stable sort order."""
    lanes = {(r.origin_site_id, r.destination_site_id) for r in rows}
    return sorted(lanes)


def _discover_products(rows: Sequence[HistoricalShipment]) -> list[str]:
    return sorted({r.product_id for r in rows})


def _discover_units(rows: Sequence[HistoricalShipment]) -> list[str]:
    units = sorted({r.unit for r in rows})
    return units or ["each"]


def _fit_unit_overrides(rows: Sequence[HistoricalShipment]) -> dict[str, str]:
    """Map products that ship in a single UOM to that UOM. Mixed-UOM
    products are absent from the override dict (caller falls through
    to the candidate_units rotation).
    """
    by_product: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        by_product[r.product_id].add(r.unit)
    return {
        product: next(iter(units))
        for product, units in by_product.items()
        if len(units) == 1
    }


def _fit_base_volumes(
    rows: Sequence[HistoricalShipment],
    *,
    min_buckets: int,
) -> dict[tuple[str, str, str], float]:
    """Per-channel mean qty per occupied weekly bucket.

    ``occupied`` means the bucket has at least one shipment. Channels
    with fewer than ``min_buckets`` occupied buckets are dropped from
    the result (they inherit ``default_base_volume`` at generator-time).
    """
    # Channel → ISO-week-key → summed qty.
    per_channel_per_bucket: dict[tuple[str, str, str], dict[tuple[int, int], float]] = (
        defaultdict(lambda: defaultdict(float))
    )
    for r in rows:
        channel = (r.origin_site_id, r.destination_site_id, r.product_id)
        bucket = _iso_week_key(r.shipment_date)
        per_channel_per_bucket[channel][bucket] += r.quantity

    out: dict[tuple[str, str, str], float] = {}
    for channel, buckets in per_channel_per_bucket.items():
        if len(buckets) < min_buckets:
            continue
        out[channel] = sum(buckets.values()) / len(buckets)
    return out


def _fit_seasonal_envelopes(
    rows: Sequence[HistoricalShipment],
    *,
    seasonal_period: int,
    min_full_periods: float,
) -> dict[str, SeasonalEnvelope]:
    """Per-lane SeasonalEnvelope fitted via Core's ``fit_seasonal_envelope``.

    Aggregates per-(lane × ISO-week) qty over the full history window,
    flattens to (date, value) pairs, then delegates to Core.

    Lanes with too little history (less than ``min_full_periods`` annual
    cycles) are skipped — the fitter returns an envelope-less map for
    them so the generator falls through to ``envelope_spread`` instead.
    """
    # Lane → (year, iso_week) → summed qty.
    per_lane_per_bucket: dict[tuple[str, str], dict[tuple[int, int], float]] = (
        defaultdict(lambda: defaultdict(float))
    )
    for r in rows:
        lane = (r.origin_site_id, r.destination_site_id)
        per_lane_per_bucket[lane][_iso_week_key(r.shipment_date)] += r.quantity

    out: dict[str, SeasonalEnvelope] = {}
    for lane, buckets in per_lane_per_bucket.items():
        n_buckets = len(buckets)
        if n_buckets / seasonal_period < min_full_periods:
            logger.info(
                "fit_phase2_shipment_generator: lane %s -> %s has %d buckets "
                "(< %.1f periods of %d); skipping seasonal fit, will use spread",
                lane[0], lane[1], n_buckets, min_full_periods, seasonal_period,
            )
            continue

        # Replay (date, value) pairs for fit_seasonal_envelope. Use the
        # Monday-of-iso-week as the canonical date.
        dates: list[date] = []
        values: list[float] = []
        for (year, iso_week), qty in sorted(buckets.items()):
            dates.append(_monday_of_iso_week(year, iso_week))
            values.append(qty)

        series_key = lane_series_key(lane[0], lane[1])
        envelope = fit_seasonal_envelope(
            history_dates=dates,
            history_values=values,
            series_kind=SeriesKind.OTHER.value,
            series_key=series_key,
            period=seasonal_period,
        )
        out[series_key] = envelope
    return out


def _iso_week_key(d: date) -> tuple[int, int]:
    """``(iso_year, iso_week)`` — stable monthly-rollover-resistant key."""
    iso = d.isocalendar()
    return iso[0], iso[1]


def _monday_of_iso_week(iso_year: int, iso_week: int) -> date:
    """Calendar date of the Monday of the given ISO week."""
    # Jan 4 is always in ISO week 1.
    jan4 = date(iso_year, 1, 4)
    week1_monday = jan4 - timedelta(days=jan4.weekday())
    return week1_monday + timedelta(weeks=iso_week - 1)


# ── DB-side loader (thin async wrapper) ──────────────────────────────


async def load_history_for_config(
    config_id: int,
    db,  # AsyncSession — typed as Any to avoid a hard SQLAlchemy import here
    *,
    history_window_days: int = 365 * 2,
    end_date: date | None = None,
) -> list[HistoricalShipment]:
    """Load TransferOrder + TransferOrderLineItem rows for a config and
    map them into ``HistoricalShipment`` dataclasses for the fitter.

    Returns an empty list if no history is found — callers pass that
    straight to ``fit_phase2_shipment_generator`` which returns an
    empty Phase-2 generator (same shape as the Phase-1 stub).

    Site IDs are mapped from the DB Integer form to the canonical
    String form ``"site:<id>"`` so the resulting envelopes are
    interoperable with Phase-1 stub output.

    Lazily imports SQLAlchemy + the TMS ORM models so the pure fitter
    above stays importable in environments without a full backend
    install (unit tests, Phase-3 standalone consumers).
    """
    # Lazy imports keep the pure fitter dep-free.
    from sqlalchemy import select
    from app.models.transfer_order import TransferOrder, TransferOrderLineItem

    cutoff = (end_date or date.today()) - timedelta(days=history_window_days)

    stmt = (
        select(
            TransferOrder.source_site_id,
            TransferOrder.destination_site_id,
            TransferOrder.shipment_date,
            TransferOrderLineItem.product_id,
            TransferOrderLineItem.quantity,
        )
        .join(
            TransferOrderLineItem,
            TransferOrderLineItem.to_id == TransferOrder.id,
        )
        .where(TransferOrder.config_id == config_id)
        .where(TransferOrder.shipment_date >= cutoff)
    )

    result = await db.execute(stmt)
    out: list[HistoricalShipment] = []
    for row in result.all():
        origin_id, dest_id, ship_date, product_id, qty = row
        if ship_date is None:
            continue
        out.append(
            HistoricalShipment(
                origin_site_id=f"site:{int(origin_id)}",
                destination_site_id=f"site:{int(dest_id)}",
                product_id=str(product_id),
                shipment_date=ship_date,
                quantity=float(qty or 0.0),
                # The TMS line-item table doesn't carry a UOM column
                # today (PR-6 follow-up: resolve product UOM from the
                # ``product`` master). For now, all rows tag as
                # ``each`` and the fitter's unit-override pass collapses
                # to that.
                unit="each",
            )
        )
    return out


def load_history_for_config_sync(
    db,  # SQLAlchemy Session — typed as Any so this module imports clean
    config_id: int,
    *,
    history_window_days: int = 365 * 2,
    end_date: date | None = None,
) -> list[HistoricalShipment]:
    """Sync version of ``load_history_for_config``.

    Provisioning steps run inside async handlers but open *sync*
    SQLAlchemy sessions for SQL-heavy work (see SCP's
    ``_step_training_corpus`` and TMS's ``_step_warm_start``,
    ``_step_lgbm_forecast``). This sync loader is the entry point those
    sync sessions use.
    """
    from sqlalchemy import select
    from app.models.transfer_order import TransferOrder, TransferOrderLineItem

    cutoff = (end_date or date.today()) - timedelta(days=history_window_days)

    stmt = (
        select(
            TransferOrder.source_site_id,
            TransferOrder.destination_site_id,
            TransferOrder.shipment_date,
            TransferOrderLineItem.product_id,
            TransferOrderLineItem.quantity,
        )
        .join(
            TransferOrderLineItem,
            TransferOrderLineItem.to_id == TransferOrder.id,
        )
        .where(TransferOrder.config_id == config_id)
        .where(TransferOrder.shipment_date >= cutoff)
    )
    rows = db.execute(stmt).all()

    out: list[HistoricalShipment] = []
    for row in rows:
        origin_id, dest_id, ship_date, product_id, qty = row
        if ship_date is None:
            continue
        out.append(
            HistoricalShipment(
                origin_site_id=f"site:{int(origin_id)}",
                destination_site_id=f"site:{int(dest_id)}",
                product_id=str(product_id),
                shipment_date=ship_date,
                quantity=float(qty or 0.0),
                unit="each",
            )
        )
    return out


# ── Provisioning helpers (SCP-mirror pattern) ────────────────────────


def fit_phase2_for_config(
    *,
    db,  # SQLAlchemy Session
    config_id: int,
    tenant_id: int,
    history_window_days: int = 365 * 2,
    params: FitParameters | None = None,
    history_loader=None,
) -> dict[str, int]:
    """Fit + persist Phase-2 seasonal envelopes for ``config_id``.

    Mirrors SCP's ``fit_seasonal_envelopes_for_config`` pattern. Walks
    tenant TO history, runs the pure fitter, and **persists** the
    fitted seasonal envelopes via Core's ``SeasonalEnvelopeRecord``
    upsert. The lighter parameters (``base_volumes``,
    ``candidate_lanes``, etc.) are deliberately **not** persisted —
    SCP's pattern stores only the heavy artefact and recomputes the
    rest at simulator-instantiation time. ``load_phase2_generator_for_config``
    is the matching reader.

    Idempotent: upsert-on-``(tenant_id, series_kind, series_key)``.
    Best-effort: callers should wrap in try/except so a fit failure
    doesn't fail the whole provisioning step (mirrors SCP).

    Args:
        db: sync SQLAlchemy session.
        config_id: which supply-chain config to process.
        tenant_id: tenant scope. Required for RLS-policy compliance and
            for the unique key on ``stochastic_seasonal_envelope``.
        history_window_days: how far back to look for shipments.
        params: optional fit-knob overrides.
        history_loader: optional injection point for tests. Defaults to
            ``load_history_for_config_sync``.

    Returns:
        Summary dict ``{n_lanes, n_envelopes_fitted, n_lanes_skipped,
        n_channels_with_base_volume, n_products}`` for the
        provisioning-step result payload.
    """
    from azirella_data_model.stochastic.orm import SeasonalEnvelopeRecord

    loader = history_loader or load_history_for_config_sync
    history = loader(db, config_id, history_window_days=history_window_days)

    if not history:
        logger.info(
            "fit_phase2_for_config: no shipment history for config=%d", config_id
        )
        return {
            "n_lanes": 0,
            "n_envelopes_fitted": 0,
            "n_lanes_skipped": 0,
            "n_channels_with_base_volume": 0,
            "n_products": 0,
        }

    generator = fit_phase2_shipment_generator(history, params=params)

    # Persist seasonal envelopes via the same ORM table SCP uses.
    n_fitted = 0
    for series_key, envelope in generator.seasonal_envelopes.items():
        existing = (
            db.query(SeasonalEnvelopeRecord)
            .filter(
                SeasonalEnvelopeRecord.tenant_id == tenant_id,
                SeasonalEnvelopeRecord.series_kind == envelope.series_kind,
                SeasonalEnvelopeRecord.series_key == series_key,
            )
            .one_or_none()
        )
        if existing is None:
            record = SeasonalEnvelopeRecord(
                tenant_id=tenant_id,
                config_id=config_id,
                series_kind=envelope.series_kind,
                series_key=series_key,
                period=envelope.period,
                period_indices=envelope.period_indices,
                p_low=envelope.p_low,
                p_mid=envelope.p_mid,
                p_high=envelope.p_high,
                residual_distribution=envelope.residual_distribution.to_dict(),
                fit_metadata=envelope.fit_metadata.to_dict(),
            )
            db.add(record)
        else:
            existing.config_id = config_id
            existing.period = envelope.period
            existing.period_indices = envelope.period_indices
            existing.p_low = envelope.p_low
            existing.p_mid = envelope.p_mid
            existing.p_high = envelope.p_high
            existing.residual_distribution = envelope.residual_distribution.to_dict()
            existing.fit_metadata = envelope.fit_metadata.to_dict()
        n_fitted += 1

    # Caller commits — keeps fit_phase2_for_config composable inside a
    # broader provisioning transaction (mirrors
    # SeasonalEnvelopeFitPipeline.fit_for_tenant).

    summary = {
        "n_lanes": len(generator.candidate_lanes),
        "n_envelopes_fitted": n_fitted,
        "n_lanes_skipped": len(generator.candidate_lanes) - n_fitted,
        "n_channels_with_base_volume": len(generator.base_volumes),
        "n_products": len(generator.candidate_products),
    }
    logger.info(
        "fit_phase2_for_config(config=%d, tenant=%d): %s",
        config_id, tenant_id, summary,
    )
    return summary


def load_phase2_generator_for_config(
    *,
    db,  # SQLAlchemy Session
    tenant_id: int,
    config_id: int,
    history_window_days: int = 365 * 2,
    params: FitParameters | None = None,
    history_loader=None,
) -> Phase1ShipmentGenerator:
    """Load tenant-calibrated Phase-2 generator for ``config_id``.

    Mirrors SCP's ``load_envelopes_for_simulator`` pattern: re-walks
    history to discover channels and recompute base_volumes /
    product_unit_overrides (cheap), then **overrides** the fitter's
    ad-hoc envelopes with persisted ones from
    ``stochastic_seasonal_envelope`` (the canonical fitted artefact).

    When no envelopes are persisted yet (Phase-2 fit hasn't run), the
    fitter's own envelope output is preserved — equivalent to running
    ``fit_phase2_shipment_generator`` directly.

    Returns an empty Phase-2 generator if there's no history.
    """
    from azirella_data_model.stochastic import (
        SeasonalEnvelopeRepository,
        SeriesKind,
    )

    loader = history_loader or load_history_for_config_sync
    history = loader(db, config_id, history_window_days=history_window_days)

    if not history:
        return Phase1ShipmentGenerator(
            producer_signature=PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE,
            seed=(params or FitParameters()).seed,
        )

    fresh = fit_phase2_shipment_generator(history, params=params)

    repo = SeasonalEnvelopeRepository(db)
    persisted_envs = repo.list_for_tenant(
        tenant_id=tenant_id, series_kind=SeriesKind.OTHER.value,
    )
    persisted = {
        env.series_key: env
        for env in persisted_envs
        if env.series_key.startswith("lane:")
    }

    merged_envelopes = dict(fresh.seasonal_envelopes)
    merged_envelopes.update(persisted)

    return Phase1ShipmentGenerator(
        candidate_lanes=fresh.candidate_lanes,
        candidate_products=fresh.candidate_products,
        candidate_units=fresh.candidate_units,
        base_volumes=fresh.base_volumes,
        default_base_volume=(params or FitParameters()).default_base_volume,
        seasonal_envelopes=merged_envelopes,
        envelope_spread=fresh.envelope_spread,
        seed=fresh.seed,
        product_unit_overrides=fresh.product_unit_overrides,
        producer_signature=PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE,
    )


__all__ = [
    "FitParameters",
    "HistoricalShipment",
    "PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE",
    "fit_phase2_for_config",
    "fit_phase2_shipment_generator",
    "load_history_for_config",
    "load_history_for_config_sync",
    "load_phase2_generator_for_config",
]

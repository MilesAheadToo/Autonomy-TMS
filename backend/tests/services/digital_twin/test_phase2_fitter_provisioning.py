"""Phase-2 fitter sync provisioning helpers (PR-6 follow-up).

Tests the SCP-mirror provisioning surface added on top of PR-6's pure
fitter:

  - fit_phase2_for_config: walks history, runs the pure fitter,
    upserts SeasonalEnvelopeRecord rows via the same ORM SCP uses.
  - load_phase2_generator_for_config: re-walks history (for cheap
    params) and reads persisted envelopes (heavy artefact) — mirrors
    SCP's load_envelopes_for_simulator.

Tests use an in-memory SQLite session so the upsert path runs against
a real ORM. The history loader is injected as a callable so we don't
have to seed TransferOrder + TransferOrderLineItem rows.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

# SQLAlchemy stack — sync session for these tests.
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.digital_twin import (
    FitParameters,
    HistoricalShipment,
    PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE,
    fit_phase2_for_config,
    fit_phase2_shipment_generator,
    lane_series_key,
    load_phase2_generator_for_config,
)
from azirella_data_model.stochastic.orm import (
    Base as StochasticBase,
    SeasonalEnvelopeRecord,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Fresh in-memory SQLite session with stochastic_seasonal_envelope created.

    ``StochasticBase.metadata`` is the *global* Core ORM metadata (the
    same Base used across all of ``azirella_data_model``), so a naive
    ``create_all`` would also try to create ``plane_registration``,
    ``forecast``, etc. — too many tables, with FKs to product / site
    masters that aren't worth standing up for these unit tests.

    Two-step workaround:
      1. Add stub ``tenants`` + ``supply_chain_configs`` tables to the
         same metadata so ``SeasonalEnvelopeRecord``'s FKs resolve.
      2. Create only ``SeasonalEnvelopeRecord.__table__`` rather than
         the full metadata — sidesteps the unrelated tables and their
         FK targets.

    SQLite doesn't enforce FKs by default, so the stubs are
    name-only; row content is irrelevant.
    """
    from sqlalchemy import Column, Integer, Table

    engine = create_engine("sqlite:///:memory:")

    # Stub the two FK targets SeasonalEnvelopeRecord points at, in the
    # same metadata so SQLAlchemy's FK-resolution succeeds.
    Table(
        "tenants",
        StochasticBase.metadata,
        Column("id", Integer, primary_key=True),
        extend_existing=True,
    )
    Table(
        "supply_chain_configs",
        StochasticBase.metadata,
        Column("id", Integer, primary_key=True),
        extend_existing=True,
    )
    # Create stubs first, then the real envelope table.
    StochasticBase.metadata.tables["tenants"].create(engine, checkfirst=True)
    StochasticBase.metadata.tables["supply_chain_configs"].create(
        engine, checkfirst=True
    )
    try:
        SeasonalEnvelopeRecord.__table__.create(engine, checkfirst=True)
    except Exception as exc:  # pragma: no cover — env-dependent
        pytest.skip(
            f"stochastic_seasonal_envelope table can't be created on this "
            f"SQLite build: {exc}"
        )
    session = Session(engine)
    yield session
    session.close()


def _seasonal_history(*, weeks: int) -> list[HistoricalShipment]:
    """Cosine-shaped weekly history sufficient for a seasonal fit."""
    start = date(2026, 1, 5)
    out: list[HistoricalShipment] = []
    for i in range(weeks):
        qty = max(0.0, 100.0 + 30.0 * math.cos(2 * math.pi * i / 52))
        out.append(
            HistoricalShipment(
                origin_site_id="site:1",
                destination_site_id="site:2",
                product_id="sku:A",
                shipment_date=start + timedelta(weeks=i),
                quantity=qty,
            )
        )
    return out


def _stub_loader(history):
    """Return an injectable history-loader closure that ignores DB args."""
    def _load(db, config_id, *, history_window_days=730, end_date=None):  # noqa: ARG001
        return history
    return _load


# ── fit_phase2_for_config ────────────────────────────────────────────


def test_fit_phase2_for_config_persists_seasonal_envelopes(db):
    """A successful fit upserts one SeasonalEnvelopeRecord per fitted lane."""
    history = _seasonal_history(weeks=80)
    summary = fit_phase2_for_config(
        db=db,
        config_id=10,
        tenant_id=1,
        history_loader=_stub_loader(history),
    )
    db.commit()

    assert summary["n_envelopes_fitted"] == 1
    assert summary["n_lanes"] == 1
    assert summary["n_lanes_skipped"] == 0
    assert summary["n_channels_with_base_volume"] == 1
    assert summary["n_products"] == 1

    rows = db.query(SeasonalEnvelopeRecord).all()
    assert len(rows) == 1
    record = rows[0]
    assert record.tenant_id == 1
    assert record.config_id == 10
    assert record.series_key == lane_series_key("site:1", "site:2")
    assert record.period == 52


def test_fit_phase2_for_config_empty_history_persists_nothing(db):
    summary = fit_phase2_for_config(
        db=db,
        config_id=10,
        tenant_id=1,
        history_loader=_stub_loader([]),
    )
    db.commit()
    assert summary["n_envelopes_fitted"] == 0
    assert summary["n_lanes"] == 0
    assert db.query(SeasonalEnvelopeRecord).count() == 0


def test_fit_phase2_for_config_idempotent(db):
    """Re-running the fit upserts in-place rather than duplicating rows."""
    history = _seasonal_history(weeks=80)
    fit_phase2_for_config(
        db=db, config_id=10, tenant_id=1,
        history_loader=_stub_loader(history),
    )
    db.commit()
    first_count = db.query(SeasonalEnvelopeRecord).count()
    assert first_count == 1

    # Run again; should still be exactly one row.
    fit_phase2_for_config(
        db=db, config_id=10, tenant_id=1,
        history_loader=_stub_loader(history),
    )
    db.commit()
    second_count = db.query(SeasonalEnvelopeRecord).count()
    assert second_count == 1


def test_fit_phase2_for_config_skips_thin_lanes(db):
    """A lane with too little history doesn't get an envelope row."""
    history = [
        HistoricalShipment(
            origin_site_id="site:1",
            destination_site_id="site:2",
            product_id="sku:A",
            shipment_date=date(2026, 1, 5) + timedelta(weeks=i),
            quantity=100.0,
        )
        for i in range(10)  # 10 weeks << 1.5 annual cycles
    ]
    summary = fit_phase2_for_config(
        db=db, config_id=10, tenant_id=1,
        history_loader=_stub_loader(history),
    )
    db.commit()
    assert summary["n_envelopes_fitted"] == 0
    assert summary["n_lanes"] == 1
    assert summary["n_lanes_skipped"] == 1
    assert db.query(SeasonalEnvelopeRecord).count() == 0


# ── load_phase2_generator_for_config ─────────────────────────────────


def test_load_phase2_generator_uses_persisted_envelopes(db):
    """After a fit + commit, loader returns a generator with persisted envelopes."""
    history = _seasonal_history(weeks=80)
    fit_phase2_for_config(
        db=db, config_id=10, tenant_id=1,
        history_loader=_stub_loader(history),
    )
    db.commit()

    gen = load_phase2_generator_for_config(
        db=db, tenant_id=1, config_id=10,
        history_loader=_stub_loader(history),
    )
    assert gen.producer_signature == PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE
    lane_key = lane_series_key("site:1", "site:2")
    assert lane_key in gen.seasonal_envelopes
    # Persisted envelope's p_mid should match the fitter's output exactly.
    fresh_gen = fit_phase2_shipment_generator(history)
    assert gen.seasonal_envelopes[lane_key].p_mid == \
        fresh_gen.seasonal_envelopes[lane_key].p_mid


def test_load_phase2_generator_falls_back_to_fresh_fit_when_no_persistence(db):
    """When no envelopes are persisted, loader returns the fitter's
    output directly — equivalent to calling fit_phase2_shipment_generator.
    """
    history = _seasonal_history(weeks=80)
    # Note: no fit_phase2_for_config call → no rows in DB.

    gen = load_phase2_generator_for_config(
        db=db, tenant_id=1, config_id=10,
        history_loader=_stub_loader(history),
    )
    lane_key = lane_series_key("site:1", "site:2")
    # The fitter still ran in load_phase2_generator_for_config, so its
    # ad-hoc seasonal_envelopes is what we get back.
    assert lane_key in gen.seasonal_envelopes
    assert gen.producer_signature == PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE


def test_load_phase2_generator_empty_history_returns_empty_generator(db):
    gen = load_phase2_generator_for_config(
        db=db, tenant_id=1, config_id=10,
        history_loader=_stub_loader([]),
    )
    assert gen.seasonal_envelopes == {}
    assert gen.candidate_lanes == []
    assert gen.producer_signature == PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE


def test_load_phase2_generator_persisted_envelopes_override_fresh(db):
    """Persisted envelopes take priority over the fitter's ad-hoc output."""
    # First fit + persist with one history.
    history_v1 = _seasonal_history(weeks=80)
    fit_phase2_for_config(
        db=db, config_id=10, tenant_id=1,
        history_loader=_stub_loader(history_v1),
    )
    db.commit()
    persisted_p_mid = db.query(SeasonalEnvelopeRecord).one().p_mid

    # Now load with a *different* history (e.g. a week of zeros). The
    # fitter would skip the seasonal fit (too thin), but the persisted
    # envelope from the prior run should still come back.
    history_v2 = [
        HistoricalShipment(
            origin_site_id="site:1",
            destination_site_id="site:2",
            product_id="sku:A",
            shipment_date=date(2026, 1, 5) + timedelta(weeks=i),
            quantity=100.0,
        )
        for i in range(5)
    ]
    gen = load_phase2_generator_for_config(
        db=db, tenant_id=1, config_id=10,
        history_loader=_stub_loader(history_v2),
    )
    lane_key = lane_series_key("site:1", "site:2")
    assert lane_key in gen.seasonal_envelopes
    assert gen.seasonal_envelopes[lane_key].p_mid == persisted_p_mid


# ── End-to-end: fit + load + emit envelope ───────────────────────────


def test_end_to_end_fit_then_load_then_emit_phase2_envelope(db):
    """Full provisioning-mirror loop: fit + persist, reload, emit envelope.

    Verifies the artefact survives the round-trip and tags envelopes
    with the Phase-2 signature.
    """
    from azirella_demand_planning_contract import Tier
    from azirella_transfer_order_envelope_contract import PhaseIndicator

    history = _seasonal_history(weeks=80)
    fit_phase2_for_config(
        db=db, config_id=10, tenant_id=1,
        history_loader=_stub_loader(history),
    )
    db.commit()

    gen = load_phase2_generator_for_config(
        db=db, tenant_id=1, config_id=10,
        history_loader=_stub_loader(history),
    )
    envelope = gen.generate_envelope(
        tenant_id=1, config_id=10, tier=Tier.TACTICAL,
        anchor_date=date(2026, 1, 5), horizon_buckets=4,
    )
    assert envelope.produced_by == PHASE2_TENANT_CALIBRATED_PRODUCER_SIGNATURE
    assert envelope.phase_indicator is PhaseIndicator.TENANT_CALIBRATED
    assert envelope.rows  # non-empty; fitted channels emit rows


# ── Custom params propagation ────────────────────────────────────────


def test_fit_phase2_for_config_passes_params_through_to_fitter(db):
    """``params`` overrides reach the underlying fitter."""
    history = _seasonal_history(weeks=20)  # too thin for default min_full_periods
    # Default would skip the fit — but lowering the threshold lets it through.
    summary = fit_phase2_for_config(
        db=db, config_id=10, tenant_id=1,
        history_loader=_stub_loader(history),
        params=FitParameters(min_full_periods_for_seasonal=0.3),
    )
    db.commit()
    assert summary["n_envelopes_fitted"] == 1

"""§3.46 Phase 1 — L3CascadeRunner tests.

Verifies orchestration of MovementPlannerService → IntegratedBalancerService:
- happy-path end-to-end (both stages OK, shared cascade_run_id);
- idempotency skip (second run on same period is a no-op);
- force=True bypasses the idempotency skip;
- stage-2 failure preserves stage-1 (per-stage transaction boundary);
- stage-1 failure short-circuits stage-2;
- cascade_run_id format and prefix.

The fixture mirrors ``test_l3_planning_services.py`` (in-memory SQLite,
stubs for FK targets that are out-of-scope for the orchestration layer).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import pytest
from sqlalchemy import Column, Integer, create_engine
from sqlalchemy.orm import Session, sessionmaker

from azirella_data_model.base import Base
from azirella_data_model.transport_plan import (
    LaneVolumePlan,
    TransportationPlan,
    TransportationPlanItem,
)

from app.services.powell.l3_cascade_runner import (
    CascadeRunResult,
    L3CascadeRunner,
    StageResult,
)


# ---------------------------------------------------------------------------
# Local fixture (in-memory SQLite) — same pattern as test_l3_planning_services
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> Session:
    """In-memory SQLite session with FK-target stubs."""
    for tbl, cls_name in (
        ("supply_chain_configs", "_Cfg"),
        ("scenarios", "_Sc"),
        ("transportation_lane", "_Ln"),
        ("site", "_Si"),
        ("tenants", "_Tn"),
        ("users", "_Us"),
        ("carrier", "_Ca"),
        ("freight_rate", "_Fr"),
        ("load", "_Ld"),
    ):
        if tbl not in Base.metadata.tables:
            type(cls_name, (Base,), {
                "__tablename__": tbl,
                "id": Column(Integer, primary_key=True),
            })

    engine = create_engine("sqlite:///:memory:")
    tables = [
        Base.metadata.tables[t] for t in [
            "supply_chain_configs", "scenarios", "transportation_lane",
            "site", "tenants", "users", "carrier", "freight_rate", "load",
        ]
    ] + [
        LaneVolumePlan.__table__,
        TransportationPlan.__table__,
        TransportationPlanItem.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    Sess = sessionmaker(bind=engine)
    s = Sess()
    try:
        yield s
    finally:
        s.close()


def _seed_lane_volume_plan(
    db: Session,
    *,
    lane_id: int = 10,
    period_start: date = date(2026, 5, 4),
    forecast_loads_p50: float = 3.0,
    config_id: int = 1,
    tenant_id: int = 1,
) -> LaneVolumePlan:
    row = LaneVolumePlan(
        tenant_id=tenant_id, config_id=config_id,
        scenario_id=None, lane_id=lane_id,
        period_start=period_start, period_days=7,
        mode="FTL", equipment_type="DRY_VAN",
        forecast_loads_p10=forecast_loads_p50 - 1,
        forecast_loads_p50=forecast_loads_p50,
        forecast_loads_p90=forecast_loads_p50 + 1,
        plan_version="unconstrained_reference",
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_runs_both_stages(db) -> None:
    """End-to-end: forecast row → unconstrained plan → constrained plan.
    Both stages succeed; shared cascade_run_id; OK status."""
    _seed_lane_volume_plan(db, forecast_loads_p50=3)

    runner = L3CascadeRunner(db)
    result = runner.run(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        # No rate cards seeded → MovementPlanner falls back to NULL
        # carriers; that's fine for the orchestration test.
        resolve_capacity_from_db=False,  # Phase 1 clone-only
    )

    assert result.status == "OK", result
    assert result.cascade_run_id.startswith("l3_1_")
    assert len(result.stages) == 2
    assert result.stages[0].stage == "movement"
    assert result.stages[0].status == "OK"
    assert result.stages[0].plan_id is not None
    assert result.stages[1].stage == "balancer"
    assert result.stages[1].status == "OK"
    assert result.stages[1].plan_id is not None
    assert result.stages[1].plan_id != result.stages[0].plan_id

    # Both plans tagged with the same cascade_run_id.
    plans = db.query(TransportationPlan).order_by(TransportationPlan.id).all()
    assert len(plans) == 2
    assert plans[0].cascade_run_id == result.cascade_run_id
    assert plans[1].cascade_run_id == result.cascade_run_id
    assert plans[0].plan_version == "unconstrained_reference"
    assert plans[1].plan_version == "constrained_live"


def test_idempotency_second_run_skips(db) -> None:
    """A second run for the same (tenant, period) returns SKIPPED
    without running either stage. Subsequent reruns are safe (the
    cron in Phase 2 fires daily; we don't want duplicate plans)."""
    _seed_lane_volume_plan(db, forecast_loads_p50=2)
    runner = L3CascadeRunner(db)
    first = runner.run(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        resolve_capacity_from_db=False,
    )
    assert first.status == "OK"

    second = runner.run(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        resolve_capacity_from_db=False,
    )
    assert second.status == "SKIPPED"
    assert second.stages == []
    assert second.cascade_run_id != first.cascade_run_id  # New id even on skip
    # No additional plans written.
    assert db.query(TransportationPlan).count() == 2


def test_force_bypasses_idempotency(db) -> None:
    """force=True re-runs the cascade even when a prior plan exists
    (replan-after-data-fix path)."""
    _seed_lane_volume_plan(db, forecast_loads_p50=2)
    runner = L3CascadeRunner(db)
    first = runner.run(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        resolve_capacity_from_db=False,
    )
    assert first.status == "OK"

    second = runner.run(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        resolve_capacity_from_db=False, force=True,
    )
    assert second.status == "OK"
    assert second.cascade_run_id != first.cascade_run_id
    # 2 cascades × 2 plans each = 4 plans total.
    assert db.query(TransportationPlan).count() == 4


def test_stage_2_failure_preserves_stage_1(db, monkeypatch) -> None:
    """Per-stage transactions: if Balancer raises, the unconstrained
    plan stays in the DB (operators can fix capacity data + re-run
    just the Balancer)."""
    _seed_lane_volume_plan(db, forecast_loads_p50=2)

    from app.services.powell import integrated_balancer_service as ibs

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated balancer failure")

    monkeypatch.setattr(
        ibs.IntegratedBalancerService, "balance_plan", _raise,
    )

    runner = L3CascadeRunner(db)
    result = runner.run(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        resolve_capacity_from_db=False,
    )

    assert result.status == "FAILED"
    assert len(result.stages) == 2
    assert result.stages[0].status == "OK"
    assert result.stages[1].status == "FAILED"
    assert "simulated balancer failure" in result.stages[1].error

    # Unconstrained plan still in DB.
    plans = db.query(TransportationPlan).all()
    assert len(plans) == 1
    assert plans[0].plan_version == "unconstrained_reference"
    assert plans[0].cascade_run_id == result.cascade_run_id


def test_stage_1_failure_short_circuits(db, monkeypatch) -> None:
    """If Movement Planner raises, the cascade stops there — Balancer
    is not called."""
    _seed_lane_volume_plan(db, forecast_loads_p50=2)

    from app.services.powell import movement_planner_service as mps

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated movement failure")

    monkeypatch.setattr(
        mps.MovementPlannerService, "plan_movement", _raise,
    )

    runner = L3CascadeRunner(db)
    result = runner.run(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        resolve_capacity_from_db=False,
    )

    assert result.status == "FAILED"
    assert len(result.stages) == 1
    assert result.stages[0].stage == "movement"
    assert result.stages[0].status == "FAILED"
    assert "simulated movement failure" in result.stages[0].error
    assert db.query(TransportationPlan).count() == 0


def test_cascade_run_id_format(db) -> None:
    """``l3_{tenant_id}_{utc_iso}_{uuid8}`` — verifies prefix, tenant
    id slot, and uniqueness across same-period calls."""
    _seed_lane_volume_plan(db, forecast_loads_p50=1)
    runner = L3CascadeRunner(db)
    a = runner.run(
        tenant_id=42, config_id=1, period_start=date(2026, 5, 4),
        resolve_capacity_from_db=False,
    )
    b = runner.run(
        tenant_id=42, config_id=1, period_start=date(2026, 5, 4),
        resolve_capacity_from_db=False, force=True,
    )

    parts = a.cascade_run_id.split("_")
    assert parts[0] == "l3"
    assert parts[1] == "42"
    assert len(parts[2]) == 16  # YYYYMMDDTHHMMSSZ
    assert len(parts[3]) == 8   # uuid hex slice
    assert a.cascade_run_id != b.cascade_run_id

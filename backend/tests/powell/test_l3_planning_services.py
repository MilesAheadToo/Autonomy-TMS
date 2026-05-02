"""§3.38 Phase 1 — MovementPlannerService + IntegratedBalancerService tests.

L3 planning pipeline (per ``docs/TMS_DECISION_HIERARCHY.md`` §4.2 + §4.3):

  LaneVolumePlan (§3.37)
       ↓ MovementPlannerService
  TransportationPlan(plan_version='unconstrained_reference')
       ↓ IntegratedBalancerService
  TransportationPlan(plan_version='constrained_live')

Phase 1 ships heuristic scaffolds (no GraphSAGE / no LP-projection).
Tests verify:
- MovementPlanner: fan-out math (n_items = round(forecast_loads_p50)),
  leaf-row filter (FTL parent skipped when equipment children exist),
  pickup-date distribution, secondary weight/cube derivation, summary
  metrics.
- IntegratedBalancer: clone-only behaviour, source-version validation,
  metric mirroring, error on missing source plan.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import Column, ForeignKey, Integer, create_engine
from sqlalchemy.orm import Session, sessionmaker

from azirella_data_model.base import Base
from azirella_data_model.transport_plan import (
    DEFAULT_PLAN_VERSION,
    LaneVolumePlan,
)

from azirella_data_model.transport_plan import (
    PlanItemStatus,
    PlanStatus,
    TransportationPlan,
    TransportationPlanItem,
)

from app.services.powell.integrated_balancer_service import (
    BalanceResult,
    IntegratedBalancerService,
)
from app.services.powell.movement_planner_service import (
    MovementPlannerService,
    PlanResult,
)


# ---------------------------------------------------------------------------
# Local fixtures (in-memory SQLite per-test, independent of TMS app config)
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> Session:
    """In-memory SQLite session with all FK target stubs.

    Bypasses the TMS conftest's full DB setup so this file can run as
    a pure-orchestration smoke test. Production tests run against the
    real PostgreSQL fixture in CI.
    """
    # Stub FK target tables not present in Base.metadata.
    if "supply_chain_configs" not in Base.metadata.tables:
        class _Cfg(Base):  # type: ignore
            __tablename__ = "supply_chain_configs"
            id = Column(Integer, primary_key=True)
    if "scenarios" not in Base.metadata.tables:
        class _Sc(Base):  # type: ignore
            __tablename__ = "scenarios"
            id = Column(Integer, primary_key=True)
    if "transportation_lane" not in Base.metadata.tables:
        class _Ln(Base):  # type: ignore
            __tablename__ = "transportation_lane"
            id = Column(Integer, primary_key=True)
    if "site" not in Base.metadata.tables:
        class _Si(Base):  # type: ignore
            __tablename__ = "site"
            id = Column(Integer, primary_key=True)
    if "tenants" not in Base.metadata.tables:
        class _Tn(Base):  # type: ignore
            __tablename__ = "tenants"
            id = Column(Integer, primary_key=True)
    if "users" not in Base.metadata.tables:
        class _Us(Base):  # type: ignore
            __tablename__ = "users"
            id = Column(Integer, primary_key=True)
    if "carrier" not in Base.metadata.tables:
        class _Ca(Base):  # type: ignore
            __tablename__ = "carrier"
            id = Column(Integer, primary_key=True)
    if "freight_rate" not in Base.metadata.tables:
        class _Fr(Base):  # type: ignore
            __tablename__ = "freight_rate"
            id = Column(Integer, primary_key=True)
    if "load" not in Base.metadata.tables:
        class _Ld(Base):  # type: ignore
            __tablename__ = "load"
            id = Column(Integer, primary_key=True)

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
    mode: str = "FTL",
    equipment_type: str = None,
    forecast_loads_p50: float = 100.0,
    weight_p50: float = None,
    volume_p50: float = None,
    config_id: int = 1,
    tenant_id: int = 1,
    scenario_id: int = None,
) -> LaneVolumePlan:
    row = LaneVolumePlan(
        tenant_id=tenant_id,
        config_id=config_id,
        scenario_id=scenario_id,
        lane_id=lane_id,
        period_start=period_start,
        period_days=7,
        mode=mode,
        equipment_type=equipment_type,
        forecast_loads_p10=forecast_loads_p50 * 0.8,
        forecast_loads_p50=forecast_loads_p50,
        forecast_loads_p90=forecast_loads_p50 * 1.2,
        forecast_weight_kg_p50=weight_p50,
        forecast_volume_m3_p50=volume_p50,
        plan_version=DEFAULT_PLAN_VERSION,
        produced_by="TacticalForecastService",
    )
    db.add(row)
    db.flush()
    return row


# ===========================================================================
# MovementPlannerService tests
# ===========================================================================


def test_movement_plan_creates_header_and_items(db) -> None:
    _seed_lane_volume_plan(db, mode="LTL", forecast_loads_p50=5)

    svc = MovementPlannerService(db)
    result = svc.plan_movement(
        tenant_id=1, config_id=1,
        period_start=date(2026, 5, 4),
    )
    assert result.plan_id is not None
    assert result.items_written == 5
    assert result.items_per_lane == {10: 5}
    assert result.skipped_zero_loads == 0

    plan = db.query(TransportationPlan).one()
    assert plan.plan_version == "unconstrained_reference"
    assert plan.status == PlanStatus.DRAFT
    # §3.38 Phase 2A is now default; Phase 1 (no rate cards seeded) → graceful
    # NULL carrier fallback, but optimization_method reflects the Phase 2A path.
    assert plan.optimization_method == "HEURISTIC_PHASE_2A"
    assert plan.generated_by == "AGENT"
    assert plan.total_planned_loads == 5

    items = db.query(TransportationPlanItem).all()
    assert len(items) == 5
    assert all(item.plan_id == plan.id for item in items)
    assert all(item.mode == "LTL" for item in items)
    assert all(item.equipment_type is None for item in items)
    assert all(item.status == PlanItemStatus.PLANNED for item in items)
    # No rate cards seeded → carrier_id stays None (graceful Phase 2A fallback)
    assert all(item.carrier_id is None for item in items)


def test_movement_plan_fans_out_per_load(db) -> None:
    _seed_lane_volume_plan(db, lane_id=1, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=12)
    _seed_lane_volume_plan(db, lane_id=2, mode="LTL", forecast_loads_p50=3)

    svc = MovementPlannerService(db)
    result = svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    assert result.items_written == 15
    assert result.items_per_lane == {1: 12, 2: 3}


def test_movement_plan_skips_ftl_parent_when_equipment_children_exist(db) -> None:
    """FTL mode-level row is the parent; equipment-level FTL rows are
    children. Only children become plan items (so we don't double-count
    the FTL volume)."""
    _seed_lane_volume_plan(db, lane_id=1, mode="FTL", equipment_type=None, forecast_loads_p50=10)  # parent
    _seed_lane_volume_plan(db, lane_id=1, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=8)
    _seed_lane_volume_plan(db, lane_id=1, mode="FTL", equipment_type="REEFER", forecast_loads_p50=2)

    svc = MovementPlannerService(db)
    result = svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    # 8 + 2 = 10 (children only); parent's 10 NOT added (would have been 20)
    assert result.items_written == 10
    items = db.query(TransportationPlanItem).all()
    by_eq = {item.equipment_type: 0 for item in items if item.equipment_type}
    for item in items:
        if item.equipment_type:
            by_eq[item.equipment_type] += 1
    assert by_eq == {"DRY_VAN": 8, "REEFER": 2}


def test_movement_plan_keeps_ftl_parent_when_no_equipment_children(db) -> None:
    """If no equipment-level rows exist for an FTL lane, the parent IS
    a leaf and produces plan items."""
    _seed_lane_volume_plan(db, lane_id=1, mode="FTL", equipment_type=None, forecast_loads_p50=5)

    svc = MovementPlannerService(db)
    result = svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    assert result.items_written == 5
    items = db.query(TransportationPlanItem).all()
    assert all(item.mode == "FTL" for item in items)
    assert all(item.equipment_type is None for item in items)


def test_movement_plan_skips_zero_load_rows(db) -> None:
    """Forecast loads_p50 < 0.5 rounds to 0 → skipped."""
    _seed_lane_volume_plan(db, lane_id=1, forecast_loads_p50=10)
    _seed_lane_volume_plan(db, lane_id=2, forecast_loads_p50=0.3)

    svc = MovementPlannerService(db)
    result = svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    assert result.items_written == 10
    assert result.skipped_zero_loads == 1


def test_movement_plan_distributes_pickup_dates_across_period(db) -> None:
    _seed_lane_volume_plan(db, lane_id=1, mode="LTL", forecast_loads_p50=7, period_start=date(2026, 5, 4))

    svc = MovementPlannerService(db)
    svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4), period_days=7)

    items = db.query(TransportationPlanItem).order_by(TransportationPlanItem.planned_pickup_date).all()
    period_start_dt = datetime(2026, 5, 4)
    period_end_dt = period_start_dt + timedelta(days=7)
    # All pickups within period
    for item in items:
        assert period_start_dt <= item.planned_pickup_date < period_end_dt
    # Strictly increasing — even distribution
    pickups = [item.planned_pickup_date for item in items]
    assert pickups == sorted(pickups)
    # First and last have meaningful spacing
    span = pickups[-1] - pickups[0]
    assert span > timedelta(days=4)


def test_movement_plan_derives_weight_per_item(db) -> None:
    """Total weight/cube on the LaneVolumePlan row is split across items."""
    _seed_lane_volume_plan(
        db, lane_id=1, mode="LTL", forecast_loads_p50=10,
        weight_p50=180000.0, volume_p50=700.0,
    )
    svc = MovementPlannerService(db)
    svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    items = db.query(TransportationPlanItem).all()
    assert len(items) == 10
    # 180000 / 10 = 18000 per item
    assert all(item.total_weight == 18000.0 for item in items)
    # 700 / 10 = 70 per item
    assert all(item.total_volume == 70.0 for item in items)


def test_movement_plan_no_volume_data_yields_null_weight(db) -> None:
    _seed_lane_volume_plan(db, lane_id=1, mode="LTL", forecast_loads_p50=3)
    svc = MovementPlannerService(db)
    svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    items = db.query(TransportationPlanItem).all()
    assert all(item.total_weight is None for item in items)
    assert all(item.total_volume is None for item in items)


def test_movement_plan_filters_by_period_start(db) -> None:
    """Only LaneVolumePlan rows for the given period_start become items."""
    _seed_lane_volume_plan(db, lane_id=1, forecast_loads_p50=5, period_start=date(2026, 5, 4))
    _seed_lane_volume_plan(db, lane_id=1, forecast_loads_p50=99, period_start=date(2026, 5, 11))  # next week

    svc = MovementPlannerService(db)
    result = svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    assert result.items_written == 5  # only the May 4 row


def test_movement_plan_filters_by_plan_version(db) -> None:
    """Default forecast_plan_version filter is 'unconstrained_reference'."""
    row = _seed_lane_volume_plan(db, lane_id=1, forecast_loads_p50=5)
    # Add a row with a different plan_version that should be ignored
    other = _seed_lane_volume_plan(db, lane_id=1, forecast_loads_p50=999)
    other.plan_version = "decision_action"
    db.flush()

    svc = MovementPlannerService(db)
    result = svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    assert result.items_written == 5


# ===========================================================================
# IntegratedBalancerService tests
# ===========================================================================


def _seed_unconstrained_plan(db: Session, n_items: int = 5) -> int:
    _seed_lane_volume_plan(db, lane_id=1, mode="LTL", forecast_loads_p50=n_items)
    movement = MovementPlannerService(db)
    result = movement.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))
    return result.plan_id


def test_balancer_clones_unconstrained_plan(db) -> None:
    plan_id = _seed_unconstrained_plan(db, n_items=7)

    svc = IntegratedBalancerService(db)
    result = svc.balance_plan(unconstrained_plan_id=plan_id)

    assert result.constrained_plan_id != plan_id
    assert result.items_cloned == 7

    constrained_plan = db.query(TransportationPlan).filter_by(
        id=result.constrained_plan_id,
    ).one()
    assert constrained_plan.plan_version == "constrained_live"
    assert constrained_plan.optimization_method == "CLONE_PHASE_1"
    assert constrained_plan.total_planned_loads == 7


def test_balancer_phase_1_applies_no_constraints(db) -> None:
    """Phase 1 stub never repairs / escalates — those are Phase 2."""
    plan_id = _seed_unconstrained_plan(db, n_items=5)

    svc = IntegratedBalancerService(db)
    result = svc.balance_plan(unconstrained_plan_id=plan_id)

    assert result.constraints_applied == 0
    assert result.items_escalated == 0


def test_balancer_clone_preserves_item_fields(db) -> None:
    """Every field on each item is preserved in the clone."""
    plan_id = _seed_unconstrained_plan(db, n_items=3)

    svc = IntegratedBalancerService(db)
    result = svc.balance_plan(unconstrained_plan_id=plan_id)

    src_items = db.query(TransportationPlanItem).filter_by(plan_id=plan_id).order_by(TransportationPlanItem.planned_pickup_date).all()
    clone_items = db.query(TransportationPlanItem).filter_by(plan_id=result.constrained_plan_id).order_by(TransportationPlanItem.planned_pickup_date).all()

    for src, clone in zip(src_items, clone_items):
        assert src.lane_id == clone.lane_id
        assert src.mode == clone.mode
        assert src.equipment_type == clone.equipment_type
        assert src.planned_pickup_date == clone.planned_pickup_date
        assert src.planned_delivery_date == clone.planned_delivery_date
        assert src.shipment_count == clone.shipment_count
        assert src.status == clone.status


def test_balancer_rejects_non_unconstrained_source(db) -> None:
    plan_id = _seed_unconstrained_plan(db, n_items=3)
    plan = db.query(TransportationPlan).filter_by(id=plan_id).one()
    plan.plan_version = "constrained_live"  # mutate to a non-unconstrained
    db.flush()

    svc = IntegratedBalancerService(db)
    with pytest.raises(ValueError, match="expected 'unconstrained_reference'"):
        svc.balance_plan(unconstrained_plan_id=plan_id)


def test_balancer_rejects_missing_source_plan(db) -> None:
    svc = IntegratedBalancerService(db)
    with pytest.raises(ValueError, match="not found"):
        svc.balance_plan(unconstrained_plan_id=99999)


def test_balancer_renames_plan_for_constrained_version(db) -> None:
    plan_id = _seed_unconstrained_plan(db, n_items=2)

    svc = IntegratedBalancerService(db)
    result = svc.balance_plan(unconstrained_plan_id=plan_id)

    constrained_plan = db.query(TransportationPlan).filter_by(
        id=result.constrained_plan_id,
    ).one()
    assert "Constrained" in constrained_plan.plan_name
    assert "Unconstrained" not in constrained_plan.plan_name


def test_balancer_mirrors_summary_metrics(db) -> None:
    plan_id = _seed_unconstrained_plan(db, n_items=5)
    src = db.query(TransportationPlan).filter_by(id=plan_id).one()
    src.total_estimated_cost = 12345.0
    src.total_estimated_miles = 2500.0
    src.avg_cost_per_mile = 4.94
    db.flush()

    svc = IntegratedBalancerService(db)
    result = svc.balance_plan(unconstrained_plan_id=plan_id)

    constrained_plan = db.query(TransportationPlan).filter_by(
        id=result.constrained_plan_id,
    ).one()
    assert constrained_plan.total_planned_loads == 5
    assert constrained_plan.total_estimated_cost == 12345.0
    assert constrained_plan.total_estimated_miles == 2500.0
    assert constrained_plan.avg_cost_per_mile == pytest.approx(4.94)


# ===========================================================================
# End-to-end: L1 → L3 Demand Potential → L3 Movement → L3 Constrained
# ===========================================================================


def test_full_l3_pipeline_end_to_end(db) -> None:
    """Smoke test the full L3 cascade: LaneVolumePlan → unconstrained
    movement plan → constrained-live plan."""
    # 1. L3 Demand Potential — seed two lane forecasts.
    _seed_lane_volume_plan(db, lane_id=1, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=8)
    _seed_lane_volume_plan(db, lane_id=2, mode="LTL", forecast_loads_p50=3)

    # 2. L3 Movement Plan (unconstrained_reference)
    movement_svc = MovementPlannerService(db)
    movement_result = movement_svc.plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
    )
    assert movement_result.items_written == 11

    # 3. L3 Constrained Balanced Plan
    balancer = IntegratedBalancerService(db)
    balance_result = balancer.balance_plan(
        unconstrained_plan_id=movement_result.plan_id,
    )
    assert balance_result.items_cloned == 11

    # 4. Verify both plans exist with distinct plan_versions
    plans = db.query(TransportationPlan).all()
    assert len(plans) == 2
    versions = {p.plan_version for p in plans}
    assert versions == {"unconstrained_reference", "constrained_live"}

    # 5. Verify total item count = 11 per plan = 22 total
    total_items = db.query(TransportationPlanItem).count()
    assert total_items == 22


# ===========================================================================
# §3.38 Phase 2A — Carrier assignment via rate-card lookup
# ===========================================================================


def _seed_carrier_and_rate_cards(db: Session) -> None:
    """Seed minimal Carrier + Contract + RateCard data for Phase 2A tests.

    Two rate cards on the same lane (DRY_VAN, PER_MILE @ $2.50 and $3.00).
    Phase 2A should pick the cheaper one ($2.50).
    """
    from datetime import datetime
    from azirella_data_model.settlement.entities import Carrier, Contract, RateCard

    db.add(Carrier(
        id=1, tenant_id=1, scac="ABCD", display_name="Acme",
        carrier_type="TRUCKLOAD",
    ))
    db.add(Carrier(
        id=2, tenant_id=1, scac="WXYZ", display_name="Beta",
        carrier_type="TRUCKLOAD",
    ))
    db.flush()
    db.add(Contract(
        id=1, tenant_id=1, carrier_id=1, contract_number="C-001",
        contract_type="PRIMARY",
        effective_from=datetime(2026, 1, 1), currency="USD",
    ))
    db.add(Contract(
        id=2, tenant_id=1, carrier_id=2, contract_number="C-002",
        contract_type="BACKUP",
        effective_from=datetime(2026, 1, 1), currency="USD",
    ))
    db.flush()
    # Cheapest: $2.50/mile from carrier 1
    db.add(RateCard(
        id=1, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1),
    ))
    # More expensive: $3.00/mile from carrier 2
    db.add(RateCard(
        id=2, tenant_id=1, contract_id=2, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=3.00,
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()


def _seed_lane_profile(db: Session, lane_id: int = 10, distance: float = 750.0) -> None:
    """Seed a TMS-side LaneProfile with a known distance."""
    from app.models.transportation_config import LaneProfile

    db.add(LaneProfile(
        lane_id=lane_id, config_id=1,
        distance_miles=distance, primary_mode="FTL",
    ))
    db.flush()


def test_phase_2a_assigns_cheapest_carrier(db) -> None:
    """Phase 2A picks the cheapest matching rate card (carrier 1, $2.50/mi)
    over the more expensive option ($3.00/mi)."""
    _seed_carrier_and_rate_cards(db)
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=5)

    svc = MovementPlannerService(db)
    result = svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    assert result.items_written == 5
    assert result.items_with_carrier == 5
    assert result.items_without_carrier == 0

    plan = db.query(TransportationPlan).one()
    assert plan.optimization_method == "HEURISTIC_PHASE_2A"
    assert plan.total_estimated_cost == 9375.0  # 5 items × 2.50 × 750
    assert plan.total_estimated_miles == 3750.0  # 5 items × 750
    assert plan.avg_cost_per_mile == 2.5
    assert plan.carrier_count == 1  # All items assigned to carrier 1

    items = db.query(TransportationPlanItem).all()
    assert all(item.carrier_id == 1 for item in items)
    assert all(item.rate_id == 1 for item in items)  # Cheaper rate
    assert all(item.estimated_cost == 1875.0 for item in items)
    assert all(item.distance_miles == 750.0 for item in items)
    assert all(item.estimated_cost_per_mile == 2.5 for item in items)


def test_phase_2a_graceful_null_when_no_rate_card_matches(db) -> None:
    """No matching rate card (e.g., no REEFER rate cards) → carrier_id
    and cost stay NULL. Plan still produced with HEURISTIC_PHASE_2A."""
    _seed_carrier_and_rate_cards(db)  # Only DRY_VAN rate cards
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="REEFER", forecast_loads_p50=3)

    svc = MovementPlannerService(db)
    result = svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    assert result.items_written == 3
    assert result.items_with_carrier == 0
    assert result.items_without_carrier == 3

    plan = db.query(TransportationPlan).one()
    assert plan.optimization_method == "HEURISTIC_PHASE_2A"
    items = db.query(TransportationPlanItem).all()
    assert all(item.carrier_id is None for item in items)
    assert all(item.estimated_cost is None for item in items)


def test_phase_2a_explicit_phase_1_fallback(db) -> None:
    """Caller can opt out of Phase 2A — sets optimization_method back to
    HEURISTIC_PHASE_1 even when rate cards exist."""
    _seed_carrier_and_rate_cards(db)
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)

    svc = MovementPlannerService(db)
    result = svc.plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        carrier_assignment_enabled=False,
    )

    plan = db.query(TransportationPlan).one()
    assert plan.optimization_method == "HEURISTIC_PHASE_1"
    items = db.query(TransportationPlanItem).all()
    assert all(item.carrier_id is None for item in items)
    assert all(item.estimated_cost is None for item in items)
    assert all(item.distance_miles is None for item in items)


def test_phase_2a_filters_by_equipment_type(db) -> None:
    """Rate card with equipment_type=NULL ('any equipment') is also a
    match. equipment_type='REEFER' rate card NOT matched by DRY_VAN item."""
    from datetime import datetime
    from azirella_data_model.settlement.entities import Carrier, Contract, RateCard

    db.add(Carrier(
        id=1, tenant_id=1, scac="ABCD", display_name="Acme",
        carrier_type="TRUCKLOAD",
    ))
    db.flush()
    db.add(Contract(
        id=1, tenant_id=1, carrier_id=1, contract_number="C-001",
        contract_type="PRIMARY",
        effective_from=datetime(2026, 1, 1), currency="USD",
    ))
    db.flush()
    # REEFER-only rate card — should NOT match a DRY_VAN item
    db.add(RateCard(
        id=10, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="REEFER", rate_basis="PER_MILE", base_rate=2.00,
        effective_from=datetime(2026, 1, 1),
    ))
    # Equipment-agnostic rate card — should match DRY_VAN item
    db.add(RateCard(
        id=11, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type=None, rate_basis="PER_MILE", base_rate=3.00,
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)

    svc = MovementPlannerService(db)
    svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    items = db.query(TransportationPlanItem).all()
    # Should pick rate card 11 (equipment-agnostic at $3); rate card 10 (REEFER)
    # rejected even though it's cheaper.
    assert all(item.rate_id == 11 for item in items)
    assert all(item.estimated_cost == 2250.0 for item in items)  # 3.00 × 750


def test_phase_2a_filters_by_effective_window(db) -> None:
    """Rate card outside the effective window (expired or not-yet-active)
    is not matched."""
    from datetime import datetime
    from azirella_data_model.settlement.entities import Carrier, Contract, RateCard

    db.add(Carrier(
        id=1, tenant_id=1, scac="ABCD", display_name="Acme",
        carrier_type="TRUCKLOAD",
    ))
    db.flush()
    db.add(Contract(
        id=1, tenant_id=1, carrier_id=1, contract_number="C-001",
        contract_type="PRIMARY",
        effective_from=datetime(2025, 1, 1), currency="USD",
    ))
    db.flush()
    # Expired rate card (effective_to before period_start)
    db.add(RateCard(
        id=20, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=1.00,
        effective_from=datetime(2025, 1, 1),
        effective_to=datetime(2025, 12, 31),  # expired before May 2026
    ))
    # Active rate card
    db.add(RateCard(
        id=21, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)

    svc = MovementPlannerService(db)
    svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    items = db.query(TransportationPlanItem).all()
    # Should pick rate card 21 (active); not 20 (expired) even though cheaper
    assert all(item.rate_id == 21 for item in items)
    assert all(item.estimated_cost == 1875.0 for item in items)


def test_phase_2a_filters_by_lane_id(db) -> None:
    """Rate card with explicit lane_id filter only matches that lane."""
    from datetime import datetime
    from azirella_data_model.settlement.entities import Carrier, Contract, RateCard

    db.add(Carrier(
        id=1, tenant_id=1, scac="ABCD", display_name="Acme",
        carrier_type="TRUCKLOAD",
    ))
    db.flush()
    db.add(Contract(
        id=1, tenant_id=1, carrier_id=1, contract_number="C-001",
        contract_type="PRIMARY",
        effective_from=datetime(2026, 1, 1), currency="USD",
    ))
    db.flush()
    # Cheap rate scoped to lane 99 ONLY (item is on lane 10 — should not match)
    db.add(RateCard(
        id=30, tenant_id=1, contract_id=1, lane_filter={"lane_id": 99},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=1.00,
        effective_from=datetime(2026, 1, 1),
    ))
    # Catch-all (matches lane 10)
    db.add(RateCard(
        id=31, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)

    svc = MovementPlannerService(db)
    svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    items = db.query(TransportationPlanItem).all()
    # Should pick rate card 31 (catch-all); not 30 (lane 99 mismatch)
    assert all(item.rate_id == 31 for item in items)


def test_phase_2a_lane_distance_fallback_when_lane_profile_missing(db) -> None:
    """When LaneProfile has no row for the lane, distance falls back to
    the Phase 2A default (500 miles)."""
    _seed_carrier_and_rate_cards(db)
    # NO _seed_lane_profile call — distance falls back
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)

    svc = MovementPlannerService(db)
    svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    items = db.query(TransportationPlanItem).all()
    assert all(item.distance_miles == 500.0 for item in items)
    # Cost: 500 mi × $2.50/mi = $1250
    assert all(item.estimated_cost == 1250.0 for item in items)


def test_phase_2a_supports_flat_rate_basis(db) -> None:
    """FLAT rate-basis: cost = base_rate (independent of distance)."""
    from datetime import datetime
    from azirella_data_model.settlement.entities import Carrier, Contract, RateCard

    db.add(Carrier(
        id=1, tenant_id=1, scac="ABCD", display_name="Acme",
        carrier_type="TRUCKLOAD",
    ))
    db.flush()
    db.add(Contract(
        id=1, tenant_id=1, carrier_id=1, contract_number="C-001",
        contract_type="PRIMARY",
        effective_from=datetime(2026, 1, 1), currency="USD",
    ))
    db.flush()
    db.add(RateCard(
        id=40, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="FLAT", base_rate=1500.00,
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)

    svc = MovementPlannerService(db)
    svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    items = db.query(TransportationPlanItem).all()
    assert all(item.estimated_cost == 1500.0 for item in items)


def test_phase_2a_picks_distinct_carriers_when_lanes_differ(db) -> None:
    """Phase 2A reports `carrier_count` correctly when items span
    multiple carriers."""
    from datetime import datetime
    from azirella_data_model.settlement.entities import Carrier, Contract, RateCard

    db.add(Carrier(id=1, tenant_id=1, scac="C1", display_name="C1", carrier_type="TRUCKLOAD"))
    db.add(Carrier(id=2, tenant_id=1, scac="C2", display_name="C2", carrier_type="TRUCKLOAD"))
    db.flush()
    db.add(Contract(id=1, tenant_id=1, carrier_id=1, contract_number="C-001",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.add(Contract(id=2, tenant_id=1, carrier_id=2, contract_number="C-002",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.flush()
    # Carrier 1 cheap on lane 10; Carrier 2 cheap on lane 20
    db.add(RateCard(id=1, tenant_id=1, contract_id=1, lane_filter={"lane_id": 10},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.00,
        effective_from=datetime(2026, 1, 1)))
    db.add(RateCard(id=2, tenant_id=1, contract_id=2, lane_filter={"lane_id": 20},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.00,
        effective_from=datetime(2026, 1, 1)))
    db.flush()
    _seed_lane_profile(db, lane_id=10, distance=500)
    _seed_lane_profile(db, lane_id=20, distance=500)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)
    _seed_lane_volume_plan(db, lane_id=20, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=3)

    svc = MovementPlannerService(db)
    result = svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    plan = db.query(TransportationPlan).filter_by(id=result.plan_id).one()
    assert plan.carrier_count == 2  # distinct carriers across lanes

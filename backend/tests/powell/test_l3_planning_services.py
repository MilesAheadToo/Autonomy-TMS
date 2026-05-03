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


# ===========================================================================
# §3.38 Phase 2B — Integrated Balancer LP-projection (item 1)
# ===========================================================================


def _seed_two_carriers_with_capacity_test_setup(db: Session) -> int:
    """Helper: seed 2 carriers + rate cards + 10-load forecast, run
    Phase 2A, return the unconstrained_reference plan id."""
    from datetime import datetime
    from azirella_data_model.settlement.entities import Carrier, Contract, RateCard

    db.add(Carrier(id=1, tenant_id=1, scac="C1", display_name="C1", carrier_type="TRUCKLOAD"))
    db.add(Carrier(id=2, tenant_id=1, scac="C2", display_name="C2", carrier_type="TRUCKLOAD"))
    db.flush()
    db.add(Contract(id=1, tenant_id=1, carrier_id=1, contract_number="C-1",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.add(Contract(id=2, tenant_id=1, carrier_id=2, contract_number="C-2",
        contract_type="BACKUP", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.flush()
    db.add(RateCard(id=1, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1)))
    db.add(RateCard(id=2, tenant_id=1, contract_id=2, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=3.00,
        effective_from=datetime(2026, 1, 1)))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=10)
    mp = MovementPlannerService(db)
    return mp.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4)).plan_id


def test_phase_2b_lp_projection_redistributes_when_capacity_exceeded(db) -> None:
    """Phase 2A puts all 10 items on the cheaper carrier 1. Phase 2B
    LP enforces capacity {1: 4, 2: 100} → 6 items reassign to carrier 2."""
    plan_id = _seed_two_carriers_with_capacity_test_setup(db)

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(
        unconstrained_plan_id=plan_id,
        carrier_capacity={1: 4, 2: 100},
    )

    assert result.optimization_method == "LP_PROJECTION_PHASE_2B"
    assert result.items_cloned == 10
    assert result.constraints_applied == 6
    assert result.items_escalated == 0

    items = db.query(TransportationPlanItem).filter_by(
        plan_id=result.constrained_plan_id,
    ).all()
    counts = {}
    for it in items:
        counts[it.carrier_id] = counts.get(it.carrier_id, 0) + 1
    assert counts.get(1) == 4
    assert counts.get(2) == 6


def test_phase_2b_escalates_when_total_capacity_exhausted(db) -> None:
    """Capacity {1: 3, 2: 5} = 8 total; 10 items → 2 escalated to CANCELLED."""
    plan_id = _seed_two_carriers_with_capacity_test_setup(db)

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(
        unconstrained_plan_id=plan_id,
        carrier_capacity={1: 3, 2: 5},
    )

    assert result.items_escalated == 2
    cancelled = db.query(TransportationPlanItem).filter_by(
        plan_id=result.constrained_plan_id,
        status=PlanItemStatus.CANCELLED,
    ).count()
    assert cancelled == 2


def test_phase_2b_capacity_utilization_reported(db) -> None:
    """LP utilisation per carrier post-solve is reported in BalanceResult."""
    plan_id = _seed_two_carriers_with_capacity_test_setup(db)

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(
        unconstrained_plan_id=plan_id,
        carrier_capacity={1: 4, 2: 100},
    )

    util = result.capacity_utilization_per_carrier
    assert util[1] == 1.0  # 4/4 — fully utilised
    assert util[2] == pytest.approx(0.06, abs=0.001)  # 6/100


def test_phase_2b_falls_back_to_clone_when_capacity_omitted(db) -> None:
    """No `carrier_capacity` arg → CLONE_PHASE_1 (backward compat)."""
    plan_id = _seed_two_carriers_with_capacity_test_setup(db)

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(unconstrained_plan_id=plan_id)

    assert result.optimization_method == "CLONE_PHASE_1"
    assert result.items_cloned == 10
    assert result.constraints_applied == 0


# ===========================================================================
# §3.42 — DB-resolved carrier capacity from CarrierCapacityCommitment
# ===========================================================================


def _seed_capacity_commitments(db: Session, period_start, period_end) -> None:
    """Seed CarrierCapacityCommitment rows for §3.42 DB-resolution tests.

    Carrier 1 (the cheap one from `_seed_two_carriers_with_capacity_test_setup`)
    gets a 4-load commitment; carrier 2 gets 100. Same shape as the dict
    that Phase 2B tests pass directly.
    """
    from datetime import datetime
    from azirella_data_model.settlement import CarrierCapacityCommitment

    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=1, lane_filter={}, equipment_type="DRY_VAN",
        period_start=period_start, period_end=period_end,
        period_granularity="WEEKLY",
        commit_volume=4, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=2, lane_filter={}, equipment_type="DRY_VAN",
        period_start=period_start, period_end=period_end,
        period_granularity="WEEKLY",
        commit_volume=100, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()


def test_phase_3_42_db_resolved_capacity_drives_lp_projection(db) -> None:
    """resolve_capacity_from_db=True queries CarrierCapacityCommitment
    rows and feeds the LP. Same outcome as the dict path."""
    plan_id = _seed_two_carriers_with_capacity_test_setup(db)
    plan = db.query(TransportationPlan).filter_by(id=plan_id).one()
    _seed_capacity_commitments(db, plan.plan_start_date, plan.plan_end_date)

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(
        unconstrained_plan_id=plan_id,
        resolve_capacity_from_db=True,
    )

    assert result.optimization_method == "LP_PROJECTION_PHASE_2B"
    assert result.constraints_applied == 6  # 6 items reassigned to carrier 2
    items = db.query(TransportationPlanItem).filter_by(
        plan_id=result.constrained_plan_id,
    ).all()
    counts = {}
    for it in items:
        counts[it.carrier_id] = counts.get(it.carrier_id, 0) + 1
    assert counts.get(1) == 4
    assert counts.get(2) == 6


def test_phase_3_42_dict_override_takes_precedence(db) -> None:
    """When both `carrier_capacity` and `resolve_capacity_from_db=True`
    are passed, the dict overrides per-carrier values from the DB."""
    plan_id = _seed_two_carriers_with_capacity_test_setup(db)
    plan = db.query(TransportationPlan).filter_by(id=plan_id).one()
    _seed_capacity_commitments(db, plan.plan_start_date, plan.plan_end_date)

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(
        unconstrained_plan_id=plan_id,
        resolve_capacity_from_db=True,
        carrier_capacity={1: 2},  # override carrier 1 to 2 (was 4 in DB)
    )

    items = db.query(TransportationPlanItem).filter_by(
        plan_id=result.constrained_plan_id,
    ).all()
    counts = {}
    for it in items:
        counts[it.carrier_id] = counts.get(it.carrier_id, 0) + 1
    assert counts.get(1) == 2  # capped by override
    assert counts.get(2) == 8  # rest reassigned


def test_phase_3_42_empty_db_resolution_falls_through_to_clone(db) -> None:
    """No CarrierCapacityCommitment rows + flag set + no dict →
    fall through to CLONE_PHASE_1."""
    plan_id = _seed_two_carriers_with_capacity_test_setup(db)
    # No _seed_capacity_commitments call

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(
        unconstrained_plan_id=plan_id,
        resolve_capacity_from_db=True,
    )

    assert result.optimization_method == "CLONE_PHASE_1"
    assert result.constraints_applied == 0


def test_phase_3_42_p2_irrelevant_equipment_commitments_filtered_out(db) -> None:
    """§3.42 Phase 2: a REEFER commitment under the same carrier does
    not boost the carrier's pool when plan items are all DRY_VAN."""
    from datetime import datetime
    from azirella_data_model.settlement import (
        Carrier, Contract, RateCard, CarrierCapacityCommitment,
    )

    # 1 carrier with 2 contracts: one DRY_VAN with low cap, one REEFER with high cap
    db.add(Carrier(id=1, tenant_id=1, scac="C1", display_name="C1", carrier_type="TRUCKLOAD"))
    db.flush()
    db.add(Contract(id=1, tenant_id=1, carrier_id=1, contract_number="C1-A",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.add(Contract(id=2, tenant_id=1, carrier_id=1, contract_number="C1-B",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.flush()
    db.add(RateCard(id=1, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1)))
    db.flush()
    # Relevant DRY_VAN commitment (low cap)
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=1, lane_filter={}, equipment_type="DRY_VAN",
        period_start=date(2026, 5, 4), period_end=date(2026, 5, 10),
        period_granularity="WEEKLY", commit_volume=4, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    # Irrelevant REEFER commitment (high cap; should NOT count)
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=2, lane_filter={}, equipment_type="REEFER",
        period_start=date(2026, 5, 4), period_end=date(2026, 5, 10),
        period_granularity="WEEKLY", commit_volume=100, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=10)

    mp = MovementPlannerService(db)
    plan_id = mp.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4)).plan_id

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(unconstrained_plan_id=plan_id, resolve_capacity_from_db=True)

    # Only DRY_VAN cap of 4 counts; 6 of 10 items escalated.
    # Phase 1 (no filter) would have summed 4 + 100 = 104, escalating 0.
    assert result.items_escalated == 6
    items = db.query(TransportationPlanItem).filter_by(
        plan_id=result.constrained_plan_id,
    ).all()
    on_c1 = sum(1 for it in items if it.carrier_id == 1)
    cancelled = sum(1 for it in items if it.status == PlanItemStatus.CANCELLED)
    assert on_c1 == 4
    assert cancelled == 6


def test_phase_3_42_p2_lane_filter_mismatch_filtered_out(db) -> None:
    """§3.42 Phase 2: a commitment scoped to lane_id 99 doesn't count
    when plan items are all on lane 10."""
    from datetime import datetime
    from azirella_data_model.settlement import (
        Carrier, Contract, RateCard, CarrierCapacityCommitment,
    )

    db.add(Carrier(id=1, tenant_id=1, scac="C1", display_name="C1", carrier_type="TRUCKLOAD"))
    db.flush()
    db.add(Contract(id=1, tenant_id=1, carrier_id=1, contract_number="C1",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.flush()
    db.add(RateCard(id=1, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1)))
    # Commitment scoped to lane 99 only — items are on lane 10
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=1, lane_filter={"lane_id": 99},
        equipment_type="DRY_VAN",
        period_start=date(2026, 5, 4), period_end=date(2026, 5, 10),
        period_granularity="WEEKLY", commit_volume=100, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=3)

    plan_id = MovementPlannerService(db).plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4)).plan_id

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(unconstrained_plan_id=plan_id, resolve_capacity_from_db=True)

    # Lane 99 commitment doesn't match lane 10 items → no capacity →
    # falls through to CLONE_PHASE_1.
    assert result.optimization_method == "CLONE_PHASE_1"


def test_phase_3_42_p2_geographic_filter_fails_closed(db) -> None:
    """§3.42 Phase 2: geographic lane_filter shapes (origin_state etc.)
    are not yet supported — the matcher fails closed (commitment
    excluded). Phase 3 will resolve them via _resolve_lane_geography."""
    from datetime import datetime
    from azirella_data_model.settlement import (
        Carrier, Contract, RateCard, CarrierCapacityCommitment,
    )

    db.add(Carrier(id=1, tenant_id=1, scac="C1", display_name="C1", carrier_type="TRUCKLOAD"))
    db.flush()
    db.add(Contract(id=1, tenant_id=1, carrier_id=1, contract_number="C1",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.flush()
    db.add(RateCard(id=1, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1)))
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=1, lane_filter={"origin_state": "TX"},
        equipment_type="DRY_VAN",
        period_start=date(2026, 5, 4), period_end=date(2026, 5, 10),
        period_granularity="WEEKLY", commit_volume=100, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=3)

    plan_id = MovementPlannerService(db).plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4)).plan_id

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(unconstrained_plan_id=plan_id, resolve_capacity_from_db=True)

    # Geographic filter not supported in Phase 2 → commitment excluded
    # → no capacity → CLONE_PHASE_1.
    assert result.optimization_method == "CLONE_PHASE_1"


def test_phase_3_42_p2_mode_filter(db) -> None:
    """§3.42 Phase 2: a commitment with explicit mode='LTL' doesn't
    count for FTL plan items."""
    from datetime import datetime
    from azirella_data_model.settlement import (
        Carrier, Contract, RateCard, CarrierCapacityCommitment,
    )

    db.add(Carrier(id=1, tenant_id=1, scac="C1", display_name="C1", carrier_type="TRUCKLOAD"))
    db.flush()
    db.add(Contract(id=1, tenant_id=1, carrier_id=1, contract_number="C1",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.flush()
    db.add(RateCard(id=1, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1)))
    # Commitment scoped to LTL — items are FTL
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=1, lane_filter={}, mode="LTL",
        equipment_type="DRY_VAN",
        period_start=date(2026, 5, 4), period_end=date(2026, 5, 10),
        period_granularity="WEEKLY", commit_volume=100, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=3)

    plan_id = MovementPlannerService(db).plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4)).plan_id

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(unconstrained_plan_id=plan_id, resolve_capacity_from_db=True)

    # LTL commitment doesn't match FTL items → CLONE_PHASE_1.
    assert result.optimization_method == "CLONE_PHASE_1"


# ===========================================================================
# §3.42 Phase 3 — per-(carrier × equipment) LP + prorating + geographic filters
# ===========================================================================


def test_phase_3_42_p3_per_equipment_lp_constraint(db) -> None:
    """§3.42 Phase 3.2: per-(carrier × equipment) LP constraints.
    Single carrier with DRY_VAN cap=4 + REEFER cap=10. Plan: 6 DRY_VAN
    + 2 REEFER. Phase 3.2 fits 4 DRY_VAN + 2 REEFER, escalates 2
    DRY_VAN. Phase 1 would have summed 14 and fitted all 8."""
    from datetime import datetime
    from azirella_data_model.settlement import (
        Carrier, Contract, RateCard, CarrierCapacityCommitment,
    )

    db.add(Carrier(id=1, tenant_id=1, scac="C1", display_name="C1", carrier_type="TRUCKLOAD"))
    db.flush()
    db.add(Contract(id=1, tenant_id=1, carrier_id=1, contract_number="C1",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.flush()
    db.add(RateCard(id=1, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1)))
    db.add(RateCard(id=2, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="REEFER", rate_basis="PER_MILE", base_rate=3.00,
        effective_from=datetime(2026, 1, 1)))
    db.flush()
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=1, lane_filter={}, equipment_type="DRY_VAN",
        period_start=date(2026, 5, 4), period_end=date(2026, 5, 10),
        period_granularity="WEEKLY", commit_volume=4, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=1, lane_filter={}, equipment_type="REEFER",
        period_start=date(2026, 5, 4), period_end=date(2026, 5, 10),
        period_granularity="WEEKLY", commit_volume=10, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=6)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="REEFER", forecast_loads_p50=2)

    plan_id = MovementPlannerService(db).plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4)).plan_id
    result = IntegratedBalancerService(db).balance_plan(
        unconstrained_plan_id=plan_id, resolve_capacity_from_db=True)

    items = db.query(TransportationPlanItem).filter_by(plan_id=result.constrained_plan_id).all()
    cancelled = [i for i in items if i.status == PlanItemStatus.CANCELLED]
    assert result.items_escalated == 2
    # All escalated items should be DRY_VAN (DRY_VAN cap was tight)
    cancelled_dv = sum(1 for i in cancelled if i.equipment_type == "DRY_VAN")
    assert cancelled_dv == 2


def test_phase_3_42_p3_prorating_weekly_over_quarter(db) -> None:
    """§3.42 Phase 3.3: a WEEKLY commitment over Q2 (91 days) prorates
    to ~7/91 of its volume for a 7-day plan. 100 commit_volume → ~7.69
    effective cap → only 7 of 10 items fit."""
    from datetime import datetime
    from azirella_data_model.settlement import (
        Carrier, Contract, RateCard, CarrierCapacityCommitment,
    )

    db.add(Carrier(id=1, tenant_id=1, scac="C1", display_name="C1", carrier_type="TRUCKLOAD"))
    db.flush()
    db.add(Contract(id=1, tenant_id=1, carrier_id=1, contract_number="C1",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.flush()
    db.add(RateCard(id=1, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1)))
    db.flush()
    # Q2 commitment: Apr 1 — Jun 30 (91 days), commit_volume=100 WEEKLY
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=1, lane_filter={}, equipment_type="DRY_VAN",
        period_start=date(2026, 4, 1), period_end=date(2026, 6, 30),
        period_granularity="WEEKLY", commit_volume=100, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=10)

    plan_id = MovementPlannerService(db).plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4)).plan_id
    result = IntegratedBalancerService(db).balance_plan(
        unconstrained_plan_id=plan_id, resolve_capacity_from_db=True)

    items = db.query(TransportationPlanItem).filter_by(plan_id=result.constrained_plan_id).all()
    on_carrier = sum(1 for i in items if i.carrier_id == 1)
    # 100 × 7/91 = 7.69 → floor to 7 fittable items, 3 escalated.
    assert on_carrier == 7
    assert result.items_escalated == 3


def test_phase_3_42_p3_prorating_flat_no_proration(db) -> None:
    """§3.42 Phase 3.3: FLAT granularity commitment is NOT prorated —
    full commit_volume applies regardless of period overlap."""
    from datetime import datetime
    from azirella_data_model.settlement import (
        Carrier, Contract, RateCard, CarrierCapacityCommitment,
    )

    db.add(Carrier(id=1, tenant_id=1, scac="C1", display_name="C1", carrier_type="TRUCKLOAD"))
    db.flush()
    db.add(Contract(id=1, tenant_id=1, carrier_id=1, contract_number="C1",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.flush()
    db.add(RateCard(id=1, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1)))
    db.flush()
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=1, lane_filter={}, equipment_type="DRY_VAN",
        period_start=date(2026, 4, 1), period_end=date(2026, 6, 30),
        period_granularity="FLAT", commit_volume=20, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=10)

    plan_id = MovementPlannerService(db).plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4)).plan_id
    result = IntegratedBalancerService(db).balance_plan(
        unconstrained_plan_id=plan_id, resolve_capacity_from_db=True)

    # FLAT 20-load cap > 10 plan items → all fit
    items = db.query(TransportationPlanItem).filter_by(plan_id=result.constrained_plan_id).all()
    assert sum(1 for i in items if i.carrier_id == 1) == 10
    assert result.items_escalated == 0


# ===========================================================================
# §3.41 Phase 3 — GraphSAGE training pipeline scaffold
# ===========================================================================


def test_phase_3_41_torch_graphsage_untrained_returns_low_confidence(db) -> None:
    """§3.41 Phase 3.2: an untrained TorchGraphSAGEMovementPlanner
    returns predictions with carrier_id=None and confidence=0.0 so
    consumers can detect the untrained state and fall back to Phase 2A."""
    from app.services.powell.graphsage_movement_planner import (
        TorchGraphSAGEMovementPlanner,
        GraphSAGEPredictionInput,
    )

    model = TorchGraphSAGEMovementPlanner()
    inp = GraphSAGEPredictionInput(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4), period_days=7,
        lane_volume_forecasts=[
            {"item_id": 1, "lane_id": 10, "mode": "FTL", "equipment_type": "DRY_VAN",
             "forecast_loads_p50": 5.0},
        ],
        available_carriers=[
            {"carrier_id": 1, "rate_card_id": 1, "base_rate": 2.5, "capacity_remaining": 50},
        ],
    )
    outputs = model.predict(inp)
    assert len(outputs) == 1
    assert outputs[0].carrier_id is None
    assert outputs[0].confidence == 0.0
    assert "model_not_trained" in outputs[0].rationale.get("reason", "")


def test_phase_3_41_training_extractor_returns_iterator(db) -> None:
    """§3.41 Phase 3.1: extractor returns an iterator of training
    examples. Empty when no plan items exist."""
    from app.services.powell.movement_planner_training_data import (
        MovementPlannerTrainingDataExtractor,
    )

    extractor = MovementPlannerTrainingDataExtractor(db)
    examples = list(extractor.extract(tenant_id=1))
    assert examples == []  # No plans seeded


def test_phase_3_41_trainer_returns_run_result_with_zero_examples(db) -> None:
    """§3.41 Phase 3.3: trainer produces a TrainingRunResult even when
    no examples are extracted. Model version reflects the no-data
    state."""
    from app.services.powell.movement_planner_trainer import (
        MovementPlannerTrainer,
    )

    trainer = MovementPlannerTrainer(db)
    result = trainer.train(tenant_id=1)
    assert result.examples_extracted == 0
    assert result.examples_used == 0
    assert "no_training_data" in result.model_version
    assert result.training_run_id.startswith("trainer_1_")


# ===========================================================================
# §3.41 Phase 3.4 — GraphSAGE inference-service wiring + A/B counters
# ===========================================================================


def _stub_model(*, carrier_id_per_item, rate_id_per_item=None, confidence=1.0,
                model_version="stub_v1"):
    """Return a stand-in :class:`GraphSAGEMovementPlannerModel` whose
    ``predict`` emits the given carrier per draft item-id with the
    given confidence. ``carrier_id_per_item`` is a dict ``{item_id:
    carrier_id_or_None}``; missing keys → no prediction (heuristic
    fallback). ``confidence`` may be a scalar (applied to every
    prediction) or a dict ``{item_id: float}`` for mixed-confidence
    tests."""
    from app.services.powell.graphsage_movement_planner import (
        GraphSAGEMovementPlannerModel,
        GraphSAGEPredictionOutput,
    )

    class _Stub(GraphSAGEMovementPlannerModel):
        def fit(self, training_data):
            pass

        def predict(self, inputs):
            outputs = []
            for fc in inputs.lane_volume_forecasts:
                item_id = fc["item_id"]
                if item_id not in carrier_id_per_item:
                    continue
                carrier = carrier_id_per_item[item_id]
                conf = (
                    confidence[item_id] if isinstance(confidence, dict)
                    else confidence
                )
                outputs.append(GraphSAGEPredictionOutput(
                    item_id=item_id,
                    carrier_id=carrier,
                    rate_id=(rate_id_per_item or {}).get(item_id),
                    estimated_cost=None,
                    confidence=conf,
                    rationale={"source": "stub"},
                ))
            return outputs

        def model_version(self):
            return model_version

    return _Stub()


def test_phase_3_4_no_model_keeps_phase_2a(db) -> None:
    """§3.41 Phase 3.4 baseline: when no model is passed, the planner
    behaves exactly like Phase 2A — optimization_method stays
    HEURISTIC_PHASE_2A and the new model counters are all 0."""
    _seed_carrier_and_rate_cards(db)
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=3)

    svc = MovementPlannerService(db)
    result = svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    assert result.items_with_carrier == 3
    assert result.items_via_model == 0
    assert result.model_heuristic_agreements == 0
    assert result.model_heuristic_overrides == 0
    assert result.items_via_heuristic_fallback == 3
    assert result.model_version is None

    plan = db.query(TransportationPlan).one()
    assert plan.optimization_method == "HEURISTIC_PHASE_2A"
    assert plan.optimization_metadata is None


def test_phase_3_4_untrained_model_abstains_falls_back_to_heuristic(db) -> None:
    """§3.41 Phase 3.4: an untrained TorchGraphSAGEMovementPlanner
    emits confidence=0.0, so every item falls back to the heuristic.
    optimization_method stays HEURISTIC_PHASE_2A; model_version is
    captured for audit."""
    from app.services.powell.graphsage_movement_planner import (
        TorchGraphSAGEMovementPlanner,
    )

    _seed_carrier_and_rate_cards(db)
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=3)

    svc = MovementPlannerService(db)
    model = TorchGraphSAGEMovementPlanner()
    result = svc.plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        model=model,
    )

    assert result.items_with_carrier == 3
    assert result.items_via_model == 0
    assert result.items_via_heuristic_fallback == 3
    assert result.model_version == "graphsage_untrained"

    plan = db.query(TransportationPlan).one()
    assert plan.optimization_method == "HEURISTIC_PHASE_2A"
    assert plan.optimization_metadata is not None
    assert plan.optimization_metadata["graphsage_model_version"] == "graphsage_untrained"
    assert plan.optimization_metadata["items_via_model"] == 0


def test_phase_3_4_high_confidence_model_overrides_heuristic(db) -> None:
    """§3.41 Phase 3.4: when the model returns a different carrier with
    confidence ≥ threshold, the heuristic's choice is overridden;
    optimization_method becomes GRAPHSAGE_PHASE_3_4 (every assignable
    item went through the model)."""
    _seed_carrier_and_rate_cards(db)
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=3)

    # Heuristic picks carrier 1 (cheaper); stub overrides to carrier 2.
    model = _stub_model(
        carrier_id_per_item={0: 2, 1: 2, 2: 2},
        rate_id_per_item={0: 2, 1: 2, 2: 2},
        confidence=0.9,
        model_version="stub_override_v1",
    )

    svc = MovementPlannerService(db)
    result = svc.plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        model=model, model_confidence_threshold=0.5,
    )

    assert result.items_with_carrier == 3
    assert result.items_via_model == 3
    assert result.model_heuristic_overrides == 3
    assert result.model_heuristic_agreements == 0
    assert result.items_via_heuristic_fallback == 0
    assert result.model_version == "stub_override_v1"

    plan = db.query(TransportationPlan).one()
    assert plan.optimization_method == "GRAPHSAGE_PHASE_3_4"
    items = db.query(TransportationPlanItem).all()
    assert all(item.carrier_id == 2 for item in items)
    assert all(item.rate_id == 2 for item in items)


def test_phase_3_4_high_confidence_agreement_does_not_count_as_override(db) -> None:
    """§3.41 Phase 3.4: when the model picks the same carrier as the
    heuristic with confidence ≥ threshold, the assignment is counted as
    an *agreement*, not an override; the heuristic's already-correct
    rate id and cost are preserved."""
    _seed_carrier_and_rate_cards(db)
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)

    # Heuristic picks carrier 1; stub agrees on carrier 1.
    model = _stub_model(
        carrier_id_per_item={0: 1, 1: 1},
        confidence=0.95,
    )

    svc = MovementPlannerService(db)
    result = svc.plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        model=model,
    )

    assert result.items_via_model == 2
    assert result.model_heuristic_agreements == 2
    assert result.model_heuristic_overrides == 0

    plan = db.query(TransportationPlan).one()
    assert plan.optimization_method == "GRAPHSAGE_PHASE_3_4"
    items = db.query(TransportationPlanItem).all()
    assert all(item.carrier_id == 1 for item in items)
    assert all(item.estimated_cost == 1875.0 for item in items)  # heuristic cost preserved


def test_phase_3_4_partial_confidence_produces_hybrid(db) -> None:
    """§3.41 Phase 3.4: when the model is high-confidence on some items
    and low-confidence on others, the result is GRAPHSAGE_HEURISTIC_HYBRID
    and per-item splits land in items_via_model / items_via_heuristic_fallback."""
    _seed_carrier_and_rate_cards(db)
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=4)

    # Items 0, 1: high-confidence override to carrier 2.
    # Items 2, 3: low-confidence — heuristic stays.
    model = _stub_model(
        carrier_id_per_item={0: 2, 1: 2, 2: 2, 3: 2},
        rate_id_per_item={0: 2, 1: 2, 2: 2, 3: 2},
        confidence={0: 0.9, 1: 0.9, 2: 0.1, 3: 0.1},
    )

    svc = MovementPlannerService(db)
    result = svc.plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        model=model, model_confidence_threshold=0.5,
    )

    assert result.items_with_carrier == 4
    assert result.items_via_model == 2
    assert result.model_heuristic_overrides == 2
    assert result.items_via_heuristic_fallback == 2

    plan = db.query(TransportationPlan).one()
    assert plan.optimization_method == "GRAPHSAGE_HEURISTIC_HYBRID"
    # First two items overridden to carrier 2; last two stayed at carrier 1.
    items = sorted(
        db.query(TransportationPlanItem).all(),
        key=lambda i: i.planned_pickup_date,
    )
    assert items[0].carrier_id == 2 and items[1].carrier_id == 2
    assert items[2].carrier_id == 1 and items[3].carrier_id == 1


def test_phase_3_4_model_predict_exception_falls_back_safely(db) -> None:
    """§3.41 Phase 3.4: if ``model.predict`` throws, the planner returns
    the heuristic result with optimization_method=HEURISTIC_PHASE_2A
    (no items_via_model). This is Phase 3.5's robustness contract:
    inference failures must never break planning."""
    from app.services.powell.graphsage_movement_planner import (
        GraphSAGEMovementPlannerModel,
    )

    class _ThrowingModel(GraphSAGEMovementPlannerModel):
        def fit(self, training_data): pass
        def predict(self, inputs):
            raise RuntimeError("simulated inference failure")
        def model_version(self):
            return "throw_v1"

    _seed_carrier_and_rate_cards(db)
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)

    svc = MovementPlannerService(db)
    result = svc.plan_movement(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4),
        model=_ThrowingModel(),
    )

    assert result.items_with_carrier == 2
    assert result.items_via_model == 0
    assert result.items_via_heuristic_fallback == 2
    plan = db.query(TransportationPlan).one()
    assert plan.optimization_method == "HEURISTIC_PHASE_2A"


def test_phase_3_42_expired_commitments_not_used(db) -> None:
    """A CarrierCapacityCommitment with effective_to in the past is
    excluded from the resolved capacity."""
    from datetime import datetime
    from azirella_data_model.settlement import CarrierCapacityCommitment

    plan_id = _seed_two_carriers_with_capacity_test_setup(db)
    plan = db.query(TransportationPlan).filter_by(id=plan_id).one()

    # Carrier 1: expired commitment (effective_to before now)
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=1, lane_filter={}, equipment_type="DRY_VAN",
        period_start=plan.plan_start_date, period_end=plan.plan_end_date,
        period_granularity="WEEKLY",
        commit_volume=4, currency="USD",
        effective_from=datetime(2025, 1, 1),
        effective_to=datetime(2025, 12, 31),  # expired
    ))
    # Carrier 2: active commitment
    db.add(CarrierCapacityCommitment(
        tenant_id=1, contract_id=2, lane_filter={}, equipment_type="DRY_VAN",
        period_start=plan.plan_start_date, period_end=plan.plan_end_date,
        period_granularity="WEEKLY",
        commit_volume=100, currency="USD",
        effective_from=datetime(2026, 1, 1),
    ))
    db.flush()

    ib = IntegratedBalancerService(db)
    result = ib.balance_plan(
        unconstrained_plan_id=plan_id,
        resolve_capacity_from_db=True,
    )

    items = db.query(TransportationPlanItem).filter_by(
        plan_id=result.constrained_plan_id,
    ).all()
    counts = {}
    for it in items:
        counts[it.carrier_id] = counts.get(it.carrier_id, 0) + 1
    # Carrier 1 has no active capacity → ALL 10 items go to carrier 2
    assert counts.get(2) == 10
    assert 1 not in counts


# ===========================================================================
# §3.38 Phase 2A.1 — ChargeCalculator integration (item 2)
# ===========================================================================


def test_phase_2a_1_charge_calculator_path_used_when_available(db) -> None:
    """ChargeCalculator path produces the same linehaul result as inline
    math when accessorials/fuel are absent (Phase 2A.1)."""
    _seed_carrier_and_rate_cards(db)
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)

    svc = MovementPlannerService(db)
    svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    items = db.query(TransportationPlanItem).all()
    # ChargeCalculator linehaul = base_rate × distance = 2.50 × 750 = 1875
    # Same as the inline Phase 2A math.
    assert all(item.estimated_cost == 1875.0 for item in items)


# ===========================================================================
# §3.38 Phase 2A.2 — Geographic lane filters (item 3)
# ===========================================================================


def test_phase_2a_2_lane_filter_supports_origin_state_geography(db) -> None:
    """Rate card with `{origin_state: 'TX'}` matches only when the
    lane's from-Site is in TX."""
    from datetime import datetime
    from azirella_data_model.settlement.entities import Carrier, Contract, RateCard

    # Set up two contracts: one with TX-origin filter, one catch-all.
    db.add(Carrier(id=1, tenant_id=1, scac="C1", display_name="C1", carrier_type="TRUCKLOAD"))
    db.flush()
    db.add(Contract(id=1, tenant_id=1, carrier_id=1, contract_number="C-1",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.flush()

    # TX-only rate card (cheap)
    db.add(RateCard(id=10, tenant_id=1, contract_id=1, lane_filter={"origin_state": "TX"},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=1.00,
        effective_from=datetime(2026, 1, 1)))
    # Catch-all rate card (more expensive)
    db.add(RateCard(id=11, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1)))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)

    # Test fixture: no Site / Geography rows seeded → geography lookup
    # fails → TX-filter rate card rejected. Catch-all selected.
    svc = MovementPlannerService(db)
    svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    items = db.query(TransportationPlanItem).all()
    assert all(item.rate_id == 11 for item in items)


def test_phase_2a_2_lane_filter_with_lane_id_still_works(db) -> None:
    """The Phase 2A `lane_id` shape continues to work after geographic
    filter support is added."""
    from datetime import datetime
    from azirella_data_model.settlement.entities import Carrier, Contract, RateCard

    db.add(Carrier(id=1, tenant_id=1, scac="C1", display_name="C1", carrier_type="TRUCKLOAD"))
    db.flush()
    db.add(Contract(id=1, tenant_id=1, carrier_id=1, contract_number="C-1",
        contract_type="PRIMARY", effective_from=datetime(2026, 1, 1), currency="USD"))
    db.flush()
    # Lane-10-only cheap rate
    db.add(RateCard(id=20, tenant_id=1, contract_id=1, lane_filter={"lane_id": 10},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=1.50,
        effective_from=datetime(2026, 1, 1)))
    # Catch-all expensive
    db.add(RateCard(id=21, tenant_id=1, contract_id=1, lane_filter={},
        equipment_type="DRY_VAN", rate_basis="PER_MILE", base_rate=2.50,
        effective_from=datetime(2026, 1, 1)))
    db.flush()
    _seed_lane_profile(db)
    _seed_lane_volume_plan(db, lane_id=10, mode="FTL", equipment_type="DRY_VAN", forecast_loads_p50=2)

    svc = MovementPlannerService(db)
    svc.plan_movement(tenant_id=1, config_id=1, period_start=date(2026, 5, 4))

    items = db.query(TransportationPlanItem).all()
    # Lane-10 filter matches → cheaper rate (20) wins
    assert all(item.rate_id == 20 for item in items)


# ===========================================================================
# §3.38 Phase 3 — GraphSAGE scaffold (item 4)
# ===========================================================================


def test_graphsage_scaffold_raises_not_implemented() -> None:
    """The Phase 3 scaffold raises NotImplementedError so the contract
    is exercised but no model is trained at scaffold time."""
    from app.services.powell.graphsage_movement_planner import (
        NotYetImplementedModel,
        GraphSAGEPredictionInput,
    )

    model = NotYetImplementedModel()
    assert model.model_version() == "graphsage_not_yet_implemented"

    with pytest.raises(NotImplementedError, match="training not yet implemented"):
        model.fit(training_data=[])

    inp = GraphSAGEPredictionInput(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4), period_days=7,
        lane_volume_forecasts=[], available_carriers=[],
    )
    with pytest.raises(NotImplementedError, match="inference not yet implemented"):
        model.predict(inp)


def test_graphsage_scaffold_interface_dataclasses_construct() -> None:
    """The Phase 3 input/output dataclasses construct cleanly so consumers
    can build them against the contract before the model is trained."""
    from app.services.powell.graphsage_movement_planner import (
        GraphSAGEPredictionInput,
        GraphSAGEPredictionOutput,
    )

    inp = GraphSAGEPredictionInput(
        tenant_id=1, config_id=1, period_start=date(2026, 5, 4), period_days=7,
        lane_volume_forecasts=[
            {"lane_id": 10, "mode": "FTL", "equipment_type": "DRY_VAN",
             "forecast_loads_p50": 5.0},
        ],
        available_carriers=[
            {"carrier_id": 1, "contract_id": 1, "rate_card_id": 1,
             "base_rate": 2.5, "capacity_remaining": 50},
        ],
    )
    assert inp.tenant_id == 1
    assert len(inp.lane_volume_forecasts) == 1

    out = GraphSAGEPredictionOutput(
        item_id=42, carrier_id=1, rate_id=1, estimated_cost=1875.0,
        confidence=0.92,
    )
    assert out.confidence == 0.92


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

"""§3.37 Phase 1 — TacticalForecastService tests.

Pure-orchestration service that consumes L1 ``LaneVolumeForecastTRM``
outputs (with §3.36 mode + equipment segmentation) and writes
``ShippingForecast`` rows. Tests verify the fan-out math:

- 1 lane × ewma_share_history with N modes + M equipment-within-FTL →
  N + M rows (mode-level + equipment-level coexist per §3.37 schema).
- 1 lane × no_segmentation → 1 row with mode='ALL' (unhappy path; the
  aggregate is still preserved).
- 1 lane × single_mode_passthrough → 1 row with the dominant mode.
- DEFER actions skip persistence.
- Bands (P10/P50/P90) split proportionally by share — mode-level FTL
  P50 = aggregate P50 × FTL share; equipment-level DRY_VAN P50 =
  aggregate P50 × FTL share × DRY_VAN share.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from azirella_data_model.base import Base
from azirella_data_model.transport_plan import (
    DEFAULT_PLAN_VERSION,
    ShippingForecast,
)

from app.services.powell.tactical_forecast_service import (
    LaneForecastInput,
    TacticalForecastService,
    _NO_SEG_MODE,
)
from app.services.powell.tms_heuristic_library import LaneVolumeForecastState


# ---------------------------------------------------------------------------
# Local fixtures (in-memory SQLite per-test, independent of TMS app config)
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> Session:
    """In-memory SQLite session — ShippingForecast plus FK-target stubs.

    Avoids the TMS conftest's full DB setup. Stubs the FK targets
    (``supply_chain_configs``, ``scenarios``) the same way Core's conftest
    stubs ``users`` / ``tenants``: minimal columns just sufficient for FK
    resolution, mapped against ``Base.metadata`` so ``create_all`` builds
    them alongside ``shipping_forecast``.
    """
    from sqlalchemy import Column, Integer
    from azirella_data_model.base import Base

    # Stub FK targets if they're not already in Base.metadata.
    if "supply_chain_configs" not in Base.metadata.tables:
        class _StubConfig(Base):  # type: ignore
            __tablename__ = "supply_chain_configs"
            id = Column(Integer, primary_key=True)
    if "scenarios" not in Base.metadata.tables:
        class _StubScenario(Base):  # type: ignore
            __tablename__ = "scenarios"
            id = Column(Integer, primary_key=True)
    if "transportation_lane" not in Base.metadata.tables:
        class _StubLane(Base):  # type: ignore
            __tablename__ = "transportation_lane"
            id = Column(Integer, primary_key=True)

    engine = create_engine("sqlite:///:memory:")
    # Build only the four tables we need; ignore everything else in
    # Base.metadata that may have been registered by other test modules.
    tables = [
        Base.metadata.tables["supply_chain_configs"],
        Base.metadata.tables["scenarios"],
        Base.metadata.tables["transportation_lane"],
        ShippingForecast.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    Sess = sessionmaker(bind=engine)
    s = Sess()
    try:
        yield s
    finally:
        s.close()


def _state(**overrides) -> LaneVolumeForecastState:
    """SMOOTH-class state with 26w history → ACCEPT path."""
    base = dict(
        weeks_of_history=26,
        mean_demand=100.0,
        demand_std=10.0,
        avg_demand_interval=1.0,
        squared_cv=0.04,
        nonzero_period_pct=1.0,
        trailing_mape=0.10,
        conformal_coverage_p80=0.82,
        forecast_interval_width_pct=0.30,
        proposed_forecast_p10=80.0,
        proposed_forecast_p50=100.0,
        proposed_forecast_p90=120.0,
        last_period_actual=110.0,
    )
    base.update(overrides)
    return LaneVolumeForecastState(**base)


def _input(lane_id: int = 1, **state_kwargs) -> LaneForecastInput:
    return LaneForecastInput(
        lane_id=lane_id,
        period_start=date(2026, 5, 4),
        state=_state(**state_kwargs),
    )


# ---------------------------------------------------------------------------
# no_segmentation — falls back to one mode='ALL' row
# ---------------------------------------------------------------------------


def test_no_segmentation_writes_one_mode_all_row(db) -> None:
    svc = TacticalForecastService(db)
    result = svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(lane_id=1)],  # no mode_history → no_segmentation
    )
    assert result.rows_written == 1
    row = db.query(ShippingForecast).one()
    assert row.mode == _NO_SEG_MODE
    assert row.equipment_type is None
    assert row.segmentation_method == "no_segmentation"
    assert row.forecast_loads_p10 == 80.0
    assert row.forecast_loads_p50 == 100.0
    assert row.forecast_loads_p90 == 120.0


# ---------------------------------------------------------------------------
# single_mode_passthrough — one row at the dominant mode
# ---------------------------------------------------------------------------


def test_single_mode_passthrough_writes_one_row_at_dominant_mode(db) -> None:
    svc = TacticalForecastService(db)
    result = svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(lane_id=1, mode_history={"FTL": 0.97, "LTL": 0.03})],
    )
    assert result.rows_written == 1
    row = db.query(ShippingForecast).one()
    assert row.mode == "FTL"
    assert row.equipment_type is None
    assert row.segmentation_method == "single_mode_passthrough"
    # Full aggregate carried at the dominant mode
    assert row.forecast_loads_p50 == 100.0


# ---------------------------------------------------------------------------
# ewma_share_history — multi-mode + equipment-within-FTL
# ---------------------------------------------------------------------------


def test_ewma_writes_per_mode_rows(db) -> None:
    svc = TacticalForecastService(db)
    result = svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(
            lane_id=1,
            mode_history={"FTL": 0.70, "LTL": 0.30},
        )],
    )
    # 2 mode-level rows; no equipment_history → 0 equipment rows
    assert result.rows_written == 2
    rows = db.query(ShippingForecast).order_by(ShippingForecast.mode).all()
    by_mode = {r.mode: r for r in rows}
    assert set(by_mode.keys()) == {"FTL", "LTL"}
    # FTL row: 100 × 0.7 = 70
    assert by_mode["FTL"].forecast_loads_p50 == pytest.approx(70.0)
    assert by_mode["FTL"].forecast_loads_p10 == pytest.approx(56.0)  # 80×0.7
    assert by_mode["FTL"].forecast_loads_p90 == pytest.approx(84.0)  # 120×0.7
    # LTL row: 100 × 0.3 = 30
    assert by_mode["LTL"].forecast_loads_p50 == pytest.approx(30.0)


def test_ewma_writes_per_equipment_rows_within_ftl(db) -> None:
    svc = TacticalForecastService(db)
    result = svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(
            lane_id=1,
            mode_history={"FTL": 0.70, "LTL": 0.30},
            equipment_history={"DRY_VAN": 0.80, "REEFER": 0.20},
        )],
    )
    # 2 mode-level (FTL, LTL) + 2 equipment-level (DRY_VAN, REEFER inside FTL) = 4 rows
    assert result.rows_written == 4

    # Mode-level rows have equipment_type IS NULL
    mode_rows = db.query(ShippingForecast).filter(
        ShippingForecast.equipment_type.is_(None),
    ).all()
    assert len(mode_rows) == 2
    assert {r.mode for r in mode_rows} == {"FTL", "LTL"}

    # Equipment-level rows have mode='FTL' AND equipment_type IS NOT NULL
    equip_rows = db.query(ShippingForecast).filter(
        ShippingForecast.equipment_type.isnot(None),
    ).all()
    assert len(equip_rows) == 2
    assert {r.equipment_type for r in equip_rows} == {"DRY_VAN", "REEFER"}
    assert {r.mode for r in equip_rows} == {"FTL"}

    # Bands proportional: DRY_VAN p50 = 100 × 0.7 × 0.8 = 56
    by_eq = {r.equipment_type: r for r in equip_rows}
    assert by_eq["DRY_VAN"].forecast_loads_p50 == pytest.approx(56.0)
    assert by_eq["REEFER"].forecast_loads_p50 == pytest.approx(14.0)
    # Equipment-level p50s sum to FTL mode-level p50
    assert (
        by_eq["DRY_VAN"].forecast_loads_p50
        + by_eq["REEFER"].forecast_loads_p50
        == pytest.approx(70.0)
    )


def test_equipment_rows_skipped_when_ltl_only(db) -> None:
    """LTL-only lane with FTL equipment_history → equipment rows skipped."""
    svc = TacticalForecastService(db)
    result = svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(
            lane_id=1,
            mode_history={"LTL": 1.0},
            equipment_history={"DRY_VAN": 1.0},  # set but irrelevant
        )],
    )
    # single_mode_passthrough on LTL → 1 row only
    assert result.rows_written == 1
    rows = db.query(ShippingForecast).all()
    assert rows[0].mode == "LTL"
    assert rows[0].equipment_type is None


# ---------------------------------------------------------------------------
# Action paths — DEFER skips persistence
# ---------------------------------------------------------------------------


def test_defer_skips_persistence(db) -> None:
    """L1 returns DEFER (insufficient history) → no rows written."""
    svc = TacticalForecastService(db)
    result = svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(lane_id=1, weeks_of_history=2)],  # < 4 → DEFER
    )
    assert result.rows_written == 0
    assert result.skipped_deferred == 1
    assert db.query(ShippingForecast).count() == 0


def test_escalate_still_persists(db) -> None:
    """ESCALATE (cold-start NEW class) still persists — segmentation rides
    through every action path that doesn't DEFER."""
    svc = TacticalForecastService(db)
    result = svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(
            lane_id=1, weeks_of_history=4,  # NEW class → ESCALATE
            mode_history={"FTL": 1.0},
        )],
    )
    assert result.rows_written == 1
    row = db.query(ShippingForecast).one()
    assert row.mode == "FTL"


# ---------------------------------------------------------------------------
# Multi-lane fan-out
# ---------------------------------------------------------------------------


def test_multi_lane_fan_out(db) -> None:
    svc = TacticalForecastService(db)
    result = svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[
            _input(lane_id=1, mode_history={"FTL": 0.70, "LTL": 0.30}),
            _input(lane_id=2, mode_history={"PARCEL": 1.0}),
            _input(lane_id=3, weeks_of_history=2),  # DEFER
        ],
    )
    # Lane 1: 2 rows; Lane 2: 1 row (single_mode_passthrough); Lane 3: 0 rows
    assert result.rows_written == 3
    assert result.skipped_deferred == 1
    assert result.rows_per_lane == {1: 2, 2: 1, 3: 0}


# ---------------------------------------------------------------------------
# Provenance fields
# ---------------------------------------------------------------------------


def test_default_plan_version_used(db) -> None:
    svc = TacticalForecastService(db)
    svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(lane_id=1, mode_history={"FTL": 1.0})],
    )
    row = db.query(ShippingForecast).one()
    assert row.plan_version == DEFAULT_PLAN_VERSION
    assert row.plan_version == "unconstrained_reference"


def test_produced_by_default(db) -> None:
    svc = TacticalForecastService(db)
    svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(lane_id=1, mode_history={"FTL": 1.0})],
    )
    row = db.query(ShippingForecast).one()
    assert row.produced_by == "TacticalForecastService"


def test_produced_by_override(db) -> None:
    svc = TacticalForecastService(db)
    svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(lane_id=1, mode_history={"FTL": 1.0})],
        produced_by="custom_l3_runner",
    )
    row = db.query(ShippingForecast).one()
    assert row.produced_by == "custom_l3_runner"


def test_segmentation_method_recorded_for_audit(db) -> None:
    """Every persisted row carries the segmentation_method flag from the
    L1 TRM so consumers can audit how the split was derived."""
    svc = TacticalForecastService(db)
    svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(lane_id=1, mode_history={"FTL": 0.70, "LTL": 0.30})],
    )
    rows = db.query(ShippingForecast).all()
    assert len(rows) == 2
    for r in rows:
        assert r.segmentation_method == "ewma_share_history"


def test_forecast_method_and_demand_class_persisted(db) -> None:
    svc = TacticalForecastService(db)
    svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(lane_id=1, mode_history={"FTL": 1.0})],
    )
    row = db.query(ShippingForecast).one()
    # SMOOTH class with covariates absent → HoltWinters
    assert row.forecast_method == "HoltWinters"
    assert row.syntetos_boylan_class == "SMOOTH"


# ---------------------------------------------------------------------------
# Secondary tonnage / cube
# ---------------------------------------------------------------------------


def test_weight_and_cube_split_by_share(db) -> None:
    """Weight + cube are P50 only (per industry norm); split by mode share."""
    svc = TacticalForecastService(db)
    svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(
            lane_id=1,
            mode_history={"FTL": 0.70, "LTL": 0.30},
            mean_weight_kg_per_load=18000.0,
            mean_volume_m3_per_load=70.0,
        )],
    )
    rows = db.query(ShippingForecast).filter(
        ShippingForecast.equipment_type.is_(None),
    ).all()
    by_mode = {r.mode: r for r in rows}
    # FTL gets 70% of total weight (18000 × 100 = 1.8M × 0.7 = 1.26M)
    assert by_mode["FTL"].forecast_weight_kg_p50 == pytest.approx(1260000.0, rel=1e-3)
    assert by_mode["LTL"].forecast_weight_kg_p50 == pytest.approx(540000.0, rel=1e-3)


def test_weight_and_cube_null_when_no_signal(db) -> None:
    svc = TacticalForecastService(db)
    svc.publish_forecast(
        tenant_id=1, config_id=1,
        inputs=[_input(lane_id=1, mode_history={"FTL": 1.0})],
        # no mean_weight_kg_per_load / mean_volume_m3_per_load
    )
    row = db.query(ShippingForecast).one()
    assert row.forecast_weight_kg_p50 is None
    assert row.forecast_volume_m3_p50 is None

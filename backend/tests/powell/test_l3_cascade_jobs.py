"""§3.46 Phase 2 — L3 cascade scheduler-registration tests.

The Phase 1 runner is end-to-end-tested in ``test_l3_cascade_runner.py``.
Phase 2 only adds the APScheduler wiring — these tests cover:

- ``register_l3_cascade_jobs`` calls ``add_job`` with the right id +
  trigger when given a SyncSchedulerService stub;
- ``register_l3_cascade_jobs`` is a no-op when the scheduler is None
  (the SyncSchedulerService starts disabled in some envs);
- ``_discover_active_tenants`` returns only PRODUCTION + ACTIVE tenants
  with an active BASELINE config, and pairs each tenant with its
  config_id.

We don't test the full ``_run_l3_cascade_for_all_tenants`` body — it
opens a real DB session via ``sync_session_factory()`` which isn't
available in the test fixture. The runner-level behaviour (per-stage
commits, idempotency, A/B counters) is already covered by the Phase 1
tests; this layer is just discovery + iteration glue.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column, Integer, create_engine
from sqlalchemy.orm import Session, sessionmaker

from azirella_data_model.base import Base


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> Session:
    """In-memory SQLite session with the tenant + config tables."""
    # Stub FK targets the discovery query doesn't need but the ORMs reference.
    for tbl, cls_name in (
        ("customers", "_Cu"),
        ("users", "_Us"),
    ):
        if tbl not in Base.metadata.tables:
            type(cls_name, (Base,), {
                "__tablename__": tbl,
                "id": Column(Integer, primary_key=True),
            })

    from azirella_data_model.master.config import SupplyChainConfig
    from azirella_data_model.tenant import Tenant

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Base.metadata.tables["customers"],
            Base.metadata.tables["users"],
            Tenant.__table__,
            SupplyChainConfig.__table__,
        ],
    )
    Sess = sessionmaker(bind=engine)
    s = Sess()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# register_l3_cascade_jobs
# ---------------------------------------------------------------------------


def test_register_calls_add_job_with_daily_cron() -> None:
    """register_l3_cascade_jobs adds one job at the daily 5:00 UTC cron
    with the canonical id, replace_existing=True, and a 1-hour misfire
    grace (so a delayed cron doesn't no-op the day's run)."""
    from app.services.powell.l3_cascade_jobs import (
        _L3_CASCADE_JOB_ID,
        register_l3_cascade_jobs,
    )

    fake_scheduler = MagicMock()
    fake_service = MagicMock(_scheduler=fake_scheduler)

    register_l3_cascade_jobs(fake_service)

    assert fake_scheduler.add_job.call_count == 1
    kwargs = fake_scheduler.add_job.call_args.kwargs
    assert kwargs["id"] == _L3_CASCADE_JOB_ID
    assert kwargs["replace_existing"] is True
    assert kwargs["misfire_grace_time"] == 3600
    # Trigger has hour=5, minute=0 — APScheduler stores them as fields.
    trigger = kwargs["trigger"]
    field_map = {f.name: str(f) for f in trigger.fields}
    assert field_map["hour"] == "5"
    assert field_map["minute"] == "0"


def test_register_no_op_when_scheduler_none() -> None:
    """If the SyncSchedulerService doesn't have an active scheduler
    (some envs / tests start it disabled), register is a logged no-op
    rather than an exception."""
    from app.services.powell.l3_cascade_jobs import register_l3_cascade_jobs

    fake_service = MagicMock(_scheduler=None)
    register_l3_cascade_jobs(fake_service)  # must not raise


# ---------------------------------------------------------------------------
# _discover_active_tenants
# ---------------------------------------------------------------------------


def _seed_tenant(
    db: Session, *, tid: int, mode: str, status: str,
) -> None:
    from azirella_data_model.tenant import Tenant
    db.add(Tenant(
        id=tid, customer_id=1, name=f"T{tid}", mode=mode, status=status,
    ))


def _seed_config(
    db: Session, *, cid: int, tid: int,
    is_active: bool = True, scenario_type: str = "BASELINE",
) -> None:
    from azirella_data_model.master.config import SupplyChainConfig
    db.add(SupplyChainConfig(
        id=cid, tenant_id=tid, name=f"Cfg{cid}",
        is_active=is_active, scenario_type=scenario_type,
    ))


def test_discover_includes_production_active_with_baseline_config(db) -> None:
    """Happy path: a PRODUCTION + ACTIVE tenant with an active BASELINE
    config is returned with its (tenant_id, config_id) pair."""
    from app.services.powell.l3_cascade_jobs import _discover_active_tenants

    _seed_tenant(db, tid=1, mode="PRODUCTION", status="ACTIVE")
    _seed_config(db, cid=10, tid=1)
    db.flush()

    rows = _discover_active_tenants(db)
    assert rows == [(1, 10)]


def test_discover_excludes_learning_mode(db) -> None:
    """LEARNING-mode tenants run the cascade on-demand, not on the
    daily cron."""
    from app.services.powell.l3_cascade_jobs import _discover_active_tenants

    _seed_tenant(db, tid=1, mode="LEARNING", status="ACTIVE")
    _seed_config(db, cid=10, tid=1)
    db.flush()

    assert _discover_active_tenants(db) == []


def test_discover_excludes_inactive_status(db) -> None:
    """Tenants with status != 'ACTIVE' (e.g., SUSPENDED, ARCHIVED)
    are excluded."""
    from app.services.powell.l3_cascade_jobs import _discover_active_tenants

    _seed_tenant(db, tid=1, mode="PRODUCTION", status="SUSPENDED")
    _seed_config(db, cid=10, tid=1)
    db.flush()

    assert _discover_active_tenants(db) == []


def test_discover_excludes_non_baseline_or_inactive_config(db) -> None:
    """SCENARIO configs and is_active=False configs are excluded — the
    daily cron runs the production-baseline cascade only. Scenarios
    are run on-demand via the manual trigger."""
    from app.services.powell.l3_cascade_jobs import _discover_active_tenants

    _seed_tenant(db, tid=1, mode="PRODUCTION", status="ACTIVE")
    _seed_config(db, cid=10, tid=1, scenario_type="SCENARIO")  # excluded
    _seed_config(db, cid=11, tid=1, is_active=False)            # excluded
    db.flush()

    assert _discover_active_tenants(db) == []


def test_discover_returns_one_pair_per_tenant_config_match(db) -> None:
    """Multiple eligible tenants → multiple rows."""
    from app.services.powell.l3_cascade_jobs import _discover_active_tenants

    _seed_tenant(db, tid=1, mode="PRODUCTION", status="ACTIVE")
    _seed_tenant(db, tid=2, mode="PRODUCTION", status="ACTIVE")
    _seed_tenant(db, tid=3, mode="LEARNING", status="ACTIVE")  # excluded
    _seed_config(db, cid=10, tid=1)
    _seed_config(db, cid=20, tid=2)
    _seed_config(db, cid=30, tid=3)
    db.flush()

    rows = sorted(_discover_active_tenants(db))
    assert rows == [(1, 10), (2, 20)]

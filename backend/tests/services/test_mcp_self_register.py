"""§3.8.2 — TMS MCP self-registration unit tests.

Mirror of the SCP-side test_mcp_self_register; pinned to
``Plane.TRANSPORT`` + ``TMS_MCP_PUBLIC_URL`` env var. Uses an
in-memory SQLite session pointed at a minimal
``PlaneRegistration``-only schema with stubbed FK targets.
"""
from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

import azirella_data_model.base as _dm_base
from azirella_data_model.planes.plane import Plane
from azirella_data_model.planes.tier import Tier
from azirella_data_model.planes.producer_tier import ProducerTier

for _stub in ("tenants", "supply_chain_configs"):
    if _stub not in _dm_base.Base.metadata.tables:
        Table(
            _stub,
            _dm_base.Base.metadata,
            Column("id", Integer, primary_key=True),
        )

from azirella_data_model.planes.registry import PlaneRegistration  # noqa: E402

from app.services.mcp_self_register import self_register_mcp_endpoint  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for k in ("TMS_MCP_PUBLIC_URL", "MCP_PUBLIC_URL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    PlaneRegistration.metadata.create_all(
        engine, tables=[PlaneRegistration.__table__]
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    yield SessionLocal
    engine.dispose()


def _insert(session_factory, tenant_id, plane, *, mcp_url=None, deregistered=False):
    db = session_factory()
    row = PlaneRegistration(
        tenant_id=tenant_id,
        applies_to_all_configs=True,
        plane=plane,
        tier=Tier.T1_EXECUTION,
        cross_plane_intersection=False,
        skills_narration=False,
        premium_narration=False,
        producer_tier=ProducerTier.AZIRELLA,
        mcp_endpoint_url=mcp_url,
    )
    if deregistered:
        from datetime import datetime
        row.deregistered_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.close()


def test_env_unset_is_no_op(session_factory, caplog):
    _insert(session_factory, 1, Plane.TRANSPORT)
    with caplog.at_level(logging.INFO):
        n = self_register_mcp_endpoint(session_factory)
    assert n == 0
    assert any("TMS_MCP_PUBLIC_URL is unset" in r.message for r in caplog.records)


def test_env_set_updates_active_transport_rows(session_factory, monkeypatch):
    monkeypatch.setenv("TMS_MCP_PUBLIC_URL", "http://tms.local/mcp")
    _insert(session_factory, 1, Plane.TRANSPORT)
    _insert(session_factory, 2, Plane.TRANSPORT)

    n = self_register_mcp_endpoint(session_factory)
    assert n == 2

    db = session_factory()
    rows = db.query(PlaneRegistration).filter_by(plane=Plane.TRANSPORT).all()
    assert {r.mcp_endpoint_url for r in rows} == {"http://tms.local/mcp"}
    db.close()


def test_already_up_to_date_rows_not_rewritten(session_factory, monkeypatch):
    monkeypatch.setenv("TMS_MCP_PUBLIC_URL", "http://tms.local/mcp")
    _insert(session_factory, 1, Plane.TRANSPORT, mcp_url="http://tms.local/mcp")
    _insert(session_factory, 2, Plane.TRANSPORT)  # needs update

    n = self_register_mcp_endpoint(session_factory)
    assert n == 1


def test_non_transport_plane_rows_untouched(session_factory, monkeypatch):
    monkeypatch.setenv("TMS_MCP_PUBLIC_URL", "http://tms.local/mcp")
    _insert(session_factory, 1, Plane.TRANSPORT)
    _insert(session_factory, 1, Plane.SUPPLY)
    _insert(session_factory, 1, Plane.DEMAND)

    n = self_register_mcp_endpoint(session_factory)
    assert n == 1  # only TRANSPORT row touched

    db = session_factory()
    scp_row = db.query(PlaneRegistration).filter_by(plane=Plane.SUPPLY).one()
    dp_row = db.query(PlaneRegistration).filter_by(plane=Plane.DEMAND).one()
    assert scp_row.mcp_endpoint_url is None
    assert dp_row.mcp_endpoint_url is None
    db.close()


def test_deregistered_rows_skipped(session_factory, monkeypatch):
    monkeypatch.setenv("TMS_MCP_PUBLIC_URL", "http://tms.local/mcp")
    _insert(session_factory, 1, Plane.TRANSPORT, deregistered=True)

    n = self_register_mcp_endpoint(session_factory)
    assert n == 0


def test_generic_mcp_public_url_fallback(session_factory, monkeypatch):
    monkeypatch.delenv("TMS_MCP_PUBLIC_URL", raising=False)
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://tms.local/mcp")
    _insert(session_factory, 1, Plane.TRANSPORT)

    n = self_register_mcp_endpoint(session_factory)
    assert n == 1


def test_db_exception_returns_zero_and_warns(session_factory, monkeypatch, caplog):
    monkeypatch.setenv("TMS_MCP_PUBLIC_URL", "http://tms.local/mcp")

    failing_factory = MagicMock()
    failing_db = MagicMock()
    failing_db.query.side_effect = RuntimeError("connection lost")
    failing_factory.return_value = failing_db

    with caplog.at_level(logging.WARNING):
        n = self_register_mcp_endpoint(failing_factory)
    assert n == 0
    assert failing_db.rollback.called
    assert failing_db.close.called
    assert any("self-registration failed" in r.message for r in caplog.records)

"""Tests for the live-backtest scaffold.

Closes TMS_TRM_TRAINING_DATA_SPECIFICATION.md Open Item #3 — locks
the schema + runner against the committed synthetic fixture.

The real-data ERP extract is not in scope here; this verifies that
the framework correctly hydrates state, replays through
``compute_tms_decision``, and computes a sensible agreement report.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest


_BACKEND = Path(__file__).resolve().parents[2]
for p in (
    str(_BACKEND),
    str(_BACKEND.parent / "packages" / "autonomy-tms-heuristics" / "src"),
    "/home/trevor/Autonomy-Core/packages/azirella-heuristics-common/src",
):
    if p not in sys.path:
        sys.path.insert(0, p)

# Bypass app.services.powell heavy __init__ when loading the runner.
for _name in ("app", "app.services", "app.services.powell"):
    sys.modules.setdefault(_name, ModuleType(_name))

# scripts.backtest.* is a clean Python package (no torch dep), so import normally.
from scripts.backtest.schema import (  # noqa: E402
    BacktestRow,
    PlannerDecision,
    hydrate_state,
    load_rows,
    read_jsonl,
    write_jsonl,
)
from scripts.backtest.run_tender_backtest import (  # noqa: E402
    BacktestReport,
    replay_row,
    run_backtest,
)


FIXTURE = _BACKEND / "tests" / "fixtures" / "tender_history_sample.jsonl"


# ─────────────────────────────────────────────────────────────────────
# Schema invariants
# ─────────────────────────────────────────────────────────────────────


def test_fixture_exists() -> None:
    assert FIXTURE.exists(), (
        "Synthetic fixture missing — re-run "
        "scripts/backtest/generate_synthetic_fixture.py to regenerate"
    )


def test_fixture_row_count() -> None:
    rows = load_rows(FIXTURE)
    assert len(rows) == 50  # 30 freight_procurement + 20 capacity_promise


def test_fixture_trm_distribution() -> None:
    rows = load_rows(FIXTURE)
    trms = {r.trm_type for r in rows}
    assert trms == {"freight_procurement", "capacity_promise"}


def test_fixture_row_shape() -> None:
    rows = load_rows(FIXTURE)
    r = rows[0]
    assert r.row_id
    assert r.trm_type
    assert r.tenant_id
    assert r.source_system == "synthetic"
    assert isinstance(r.state, dict)
    assert isinstance(r.planner_decision, PlannerDecision)
    assert r.planner_decision.action_code in range(11)
    assert r.planner_decision.action_name


def test_hydrate_state_unknown_trm_raises() -> None:
    with pytest.raises(ValueError, match="Unknown trm_type"):
        hydrate_state("nonexistent_trm", {"foo": 1})


def test_hydrate_state_drops_extra_keys() -> None:
    """Hydrator must ignore extras so metadata in rows doesn't break it."""
    flat = {
        "shipment_id": 42, "lane_id": 7, "requested_loads": 3,
        "_metadata_extra": "should be ignored",
    }
    state = hydrate_state("capacity_promise", flat)
    assert state.shipment_id == 42
    assert state.lane_id == 7
    assert state.requested_loads == 3
    assert not hasattr(state, "_metadata_extra")


def test_hydrate_state_parses_datetime_fields() -> None:
    """Datetime fields stored as ISO strings hydrate back to datetime."""
    flat = {
        "shipment_id": 1, "lane_id": 1, "requested_loads": 1,
        "requested_date": "2026-04-01T10:00:00+00:00",
    }
    state = hydrate_state("capacity_promise", flat)
    assert isinstance(state.requested_date, datetime)


def test_write_read_roundtrip(tmp_path: Path) -> None:
    rows = [
        BacktestRow(
            row_id="ROUNDTRIP-001",
            trm_type="capacity_promise",
            timestamp="2026-04-01T10:00:00+00:00",
            tenant_id=1,
            source_system="test",
            state={"shipment_id": 1, "lane_id": 1, "requested_loads": 1},
            planner_decision=PlannerDecision(
                action_code=0, action_name="ACCEPT", reasoning="rt",
                actor_kind="ai",
            ),
        ),
    ]
    path = tmp_path / "rt.jsonl"
    count = write_jsonl(rows, path)
    assert count == 1
    recovered = load_rows(path)
    assert len(recovered) == 1
    assert recovered[0].row_id == "ROUNDTRIP-001"
    assert recovered[0].planner_decision.action_code == 0
    assert recovered[0].planner_decision.actor_kind == "ai"


# ─────────────────────────────────────────────────────────────────────
# Runner behaviour
# ─────────────────────────────────────────────────────────────────────


def test_replay_row_returns_result() -> None:
    rows = load_rows(FIXTURE)
    r = replay_row(rows[0])
    assert r.row_id == rows[0].row_id
    assert r.trm_type == rows[0].trm_type
    assert r.teacher_action in range(11)
    assert isinstance(r.agreement, bool)


def test_run_backtest_against_fixture_high_agreement() -> None:
    """Fixture was generated with ~20% deliberate disagreement, so
    agreement should land in [70%, 95%]."""
    report = run_backtest(FIXTURE)
    assert report.rows_total == 50
    assert 70.0 <= report.agreement_pct <= 95.0
    assert set(report.per_trm_agreement) == {"freight_procurement", "capacity_promise"}


def test_run_backtest_trm_filter() -> None:
    """Filtering to one TRM only scores rows of that type."""
    report = run_backtest(FIXTURE, trm_filter="freight_procurement")
    assert report.rows_total == 30
    assert set(report.per_trm_agreement) == {"freight_procurement"}


def test_run_backtest_emits_confusion_matrix() -> None:
    report = run_backtest(FIXTURE)
    # Both TRMs should have a confusion matrix populated.
    assert "freight_procurement" in report.per_trm_confusion
    assert "capacity_promise" in report.per_trm_confusion
    # Diagonal cells (planner == teacher) should be non-zero for at
    # least one action in each TRM (we have agreement).
    for trm, matrix in report.per_trm_confusion.items():
        diag_total = sum(
            inner.get(planner, 0) for planner, inner in matrix.items()
        )
        assert diag_total > 0, f"{trm} has no agreement-diagonal cells"


def test_run_backtest_caps_disagreement_examples() -> None:
    report = run_backtest(FIXTURE, max_disagreement_examples=3)
    assert len(report.top_disagreements) <= 3


def test_report_to_dict_serialises() -> None:
    """``BacktestReport.to_dict`` must produce JSON-serialisable output."""
    report = run_backtest(FIXTURE, max_disagreement_examples=2)
    payload = report.to_dict()
    # Round-trip through JSON to confirm.
    encoded = json.dumps(payload, default=str)
    decoded = json.loads(encoded)
    assert decoded["rows_total"] == 50
    assert "per_trm" in decoded
    assert "per_trm_confusion" in decoded


def test_run_backtest_unknown_trm_filter_returns_empty() -> None:
    report = run_backtest(FIXTURE, trm_filter="nonexistent")
    assert report.rows_total == 0
    assert report.agreement_pct == 0.0


# ─────────────────────────────────────────────────────────────────────
# End-to-end — runner + fixture exercise full pipeline
# ─────────────────────────────────────────────────────────────────────


def test_end_to_end_agreement_above_threshold_per_trm() -> None:
    """Synthetic fixture should achieve ≥70 % agreement per TRM after
    factoring in the ~20% injected planner overrides."""
    report = run_backtest(FIXTURE)
    for trm, d in report.per_trm_agreement.items():
        agreement_pct = 100.0 * d["agreed"] / max(1, d["total"])
        assert agreement_pct >= 70.0, f"{trm} agreement {agreement_pct:.1f}% below 70%"

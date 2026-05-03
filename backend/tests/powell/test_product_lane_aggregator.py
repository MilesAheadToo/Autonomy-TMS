"""Tests for ProductLaneAggregator — companion to §3.45 (writer side).

The aggregator joins ``outbound_order_line`` × ``transportation_lane``
and writes per-(lane, product, period) ``ProductLane`` rows with
volume share. The SQL portion is straightforward; the
share-computation logic gets focused unit tests here.

Full DB-integration tests (mocking outbound_order_line + the join)
need Postgres + Core's transitive-FK stub machinery, which lives in
the integration suite. Pure logic + validators tested standalone.

Same module-load pattern as ``test_lane_volume_lifecycle_reactor.py``:
load the aggregator module directly via importlib so we don't trigger
``app.services.powell.__init__``'s heavy DB-bound side-effects.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date

import pytest


_AGGREGATOR_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "app", "services", "powell", "product_lane_aggregator.py",
)


def _load_aggregator_module():
    spec = importlib.util.spec_from_file_location(
        "product_lane_aggregator_test_loaded", _AGGREGATOR_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


aggregator_module = _load_aggregator_module()
ProductLaneAggregator = aggregator_module.ProductLaneAggregator
AggregationResult = aggregator_module.AggregationResult


# ---------------------------------------------------------------------------
# _compute_shares — the pure helper that computes volume_share from per-pair
# units. The hot-path math the SQL hands off to.
# ---------------------------------------------------------------------------


class TestComputeShares:
    """The math: given [(lane_id, product_id, units)], compute
    [(lane_id, product_id, units, share)] where share = units /
    lane_total."""

    def test_single_product_single_lane_share_is_one(self):
        out = ProductLaneAggregator._compute_shares([
            (10, "A", 100.0),
        ])
        assert out == [(10, "A", 100.0, 1.0)]

    def test_two_products_one_lane_shares_sum_to_one(self):
        out = ProductLaneAggregator._compute_shares([
            (10, "A", 70.0),
            (10, "B", 30.0),
        ])
        rows = {p: (u, s) for _, p, u, s in out}
        assert rows["A"] == (70.0, 0.7)
        assert rows["B"] == (30.0, 0.3)
        assert sum(s for _, _, _, s in out) == pytest.approx(1.0)

    def test_same_product_two_lanes_independent_shares(self):
        out = ProductLaneAggregator._compute_shares([
            (10, "A", 100.0),
            (20, "A", 50.0),
        ])
        # Each lane has A as its sole product → share = 1.0 in each.
        rows = {lid: s for lid, _, _, s in out}
        assert rows[10] == 1.0
        assert rows[20] == 1.0

    def test_zero_lane_total_drops_silently(self):
        # Edge case: lane with all zero-unit rows. Defensive — caller
        # should already filter, but the helper handles it.
        out = ProductLaneAggregator._compute_shares([
            (10, "A", 0.0),
            (10, "B", 0.0),
        ])
        assert out == []

    def test_mixed_lanes_with_zero_total(self):
        out = ProductLaneAggregator._compute_shares([
            (10, "A", 0.0),  # lane 10 has zero total
            (20, "B", 50.0),  # lane 20 still produces a row
        ])
        # Only lane 20 row survives.
        assert out == [(20, "B", 50.0, 1.0)]

    def test_empty_input(self):
        assert ProductLaneAggregator._compute_shares([]) == []

    def test_three_products_share_proportions(self):
        out = ProductLaneAggregator._compute_shares([
            (10, "A", 25.0),
            (10, "B", 50.0),
            (10, "C", 25.0),
        ])
        shares = {p: s for _, p, _, s in out}
        assert shares["A"] == pytest.approx(0.25)
        assert shares["B"] == pytest.approx(0.50)
        assert shares["C"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Validators — invalid input rejected before SQL fires
# ---------------------------------------------------------------------------


class TestAggregatorValidators:
    """Input validation paths run before any DB I/O, so they don't
    need a session fixture."""

    def test_invalid_period_days_raises(self):
        with pytest.raises(ValueError, match="period_days"):
            ProductLaneAggregator(period_days=0)
        with pytest.raises(ValueError, match="period_days"):
            ProductLaneAggregator(period_days=-1)
        with pytest.raises(ValueError, match="period_days"):
            ProductLaneAggregator(period_days=400)

    def test_default_period_days_seven(self):
        agg = ProductLaneAggregator()
        assert agg.period_days == 7

    def test_custom_period_days_accepted(self):
        agg = ProductLaneAggregator(period_days=30)
        assert agg.period_days == 30

    def test_invalid_period_window_raises(self):
        agg = ProductLaneAggregator()
        # period_start >= period_end → reject before SQL fires.
        with pytest.raises(ValueError, match="period_start"):
            agg.aggregate_period(
                db=None,  # type: ignore[arg-type]
                tenant_id=42,
                config_id=1,
                period_start=date(2026, 5, 11),
                period_end=date(2026, 5, 4),
            )
        with pytest.raises(ValueError, match="period_start"):
            agg.aggregate_period(
                db=None,  # type: ignore[arg-type]
                tenant_id=42,
                config_id=1,
                period_start=date(2026, 5, 11),
                period_end=date(2026, 5, 11),  # equal also rejected
            )

    def test_invalid_weeks_back_raises(self):
        agg = ProductLaneAggregator()
        with pytest.raises(ValueError, match="weeks_back"):
            agg.aggregate_recent(
                db=None,  # type: ignore[arg-type]
                tenant_id=42,
                config_id=1,
                weeks_back=0,
                as_of=date(2026, 5, 4),
            )
        with pytest.raises(ValueError, match="weeks_back"):
            agg.aggregate_recent(
                db=None,  # type: ignore[arg-type]
                tenant_id=42,
                config_id=1,
                weeks_back=53,
                as_of=date(2026, 5, 4),
            )


# ---------------------------------------------------------------------------
# AggregationResult dataclass shape
# ---------------------------------------------------------------------------


class TestAggregationResult:
    def test_result_dataclass_fields(self):
        r = AggregationResult(
            tenant_id=42,
            config_id=1,
            period_start=date(2026, 5, 4),
            period_end=date(2026, 5, 11),
            rows_written=3,
            lanes_affected=2,
            total_volume=350.0,
        )
        assert r.tenant_id == 42
        assert r.rows_written == 3
        assert r.lanes_affected == 2
        assert r.total_volume == 350.0

    def test_result_is_frozen(self):
        r = AggregationResult(
            tenant_id=42, config_id=1,
            period_start=date(2026, 5, 4),
            period_end=date(2026, 5, 11),
            rows_written=0, lanes_affected=0, total_volume=0.0,
        )
        with pytest.raises(Exception):  # FrozenInstanceError or similar
            r.rows_written = 99  # type: ignore[misc]

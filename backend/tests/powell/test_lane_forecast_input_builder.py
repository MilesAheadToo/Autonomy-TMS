"""Tests for LaneForecastInputBuilder — Stage 0 input shaping for the
L3 cascade.

Covers the pure logic: dense-series reconstruction, Syntetos-Boylan
classification inputs (mean / std / ADI / CV² / nonzero-fraction),
linear trend slope, proposed-forecast trailing window, and edge
cases (empty obs, single obs, all-zero history).

DB-integration tests of the SQL aggregation are gated on
TMS_RUN_INTEGRATION_TESTS=1 (same env-gate as the
ProductLaneAggregator integration suite) — they need the full
mapper graph. The pure-logic suite below validates the math
without a live DB.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date, timedelta

import pytest


_BUILDER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "app", "services", "powell", "lane_forecast_input_builder.py",
)


def _load_builder_module():
    # The builder module imports from
    # ``app.services.powell.tactical_forecast_service`` and
    # ``autonomy_tms_heuristics.library``, both of which transitively
    # pull in the heavy app.services.powell.__init__. We pre-load only
    # the essentials via stub modules in sys.modules so importlib can
    # exec the builder cleanly.
    import types

    # Stub LaneForecastInput dataclass (the builder imports this).
    if "app.services.powell.tactical_forecast_service" not in sys.modules:
        tfs_stub = types.ModuleType("app.services.powell.tactical_forecast_service")
        from dataclasses import dataclass
        from typing import Any

        @dataclass
        class LaneForecastInput:
            lane_id: int
            state: Any
        tfs_stub.LaneForecastInput = LaneForecastInput
        sys.modules["app.services.powell.tactical_forecast_service"] = tfs_stub

    # Stub LaneVolumeForecastState — simple dataclass with the fields
    # the builder writes to. We don't need full TRM behaviour.
    if "autonomy_tms_heuristics.library" not in sys.modules:
        thl_stub = types.ModuleType("autonomy_tms_heuristics.library")
        from dataclasses import dataclass, field
        from datetime import date as _date
        from typing import Optional

        @dataclass
        class LaneVolumeForecastState:
            lane_id: int = 0
            period_start: Optional[_date] = None
            period_days: int = 7
            weeks_of_history: int = 0
            mean_demand: float = 0.0
            demand_std: float = 0.0
            avg_demand_interval: float = 1.0
            squared_cv: float = 0.0
            nonzero_period_pct: float = 1.0
            trend_slope: float = 0.0
            seasonal_strength: float = 0.0
            is_peak_season: bool = False
            forecast_method_in_use: str = ""
            trailing_mape: float = 0.0
            trailing_wape: float = 0.0
            forecast_bias: float = 0.0
            conformal_coverage_p80: float = 0.80
            has_rate_covariate: bool = False
            has_market_signal: bool = False
            has_calendar_features: bool = True
            signal_type: str = ""
            signal_magnitude: float = 0.0
            signal_confidence: float = 0.0
            proposed_forecast_p50: float = 0.0
            proposed_forecast_p10: float = 0.0
            proposed_forecast_p90: float = 0.0
            proposed_method: str = "HoltWinters"
            last_period_actual: float = 0.0
            forecast_interval_width_pct: float = 0.0
        thl_stub.LaneVolumeForecastState = LaneVolumeForecastState
        sys.modules["autonomy_tms_heuristics.library"] = thl_stub

    # Pre-register parent packages.
    for parent in ("app", "app.services", "app.services.powell"):
        if parent not in sys.modules:
            pkg = types.ModuleType(parent)
            pkg.__path__ = []
            sys.modules[parent] = pkg

    spec = importlib.util.spec_from_file_location(
        "lane_forecast_input_builder_test_loaded", _BUILDER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


builder_module = _load_builder_module()
LaneForecastInputBuilder = builder_module.LaneForecastInputBuilder
_linear_slope = builder_module._linear_slope
_PeriodObservation = builder_module._PeriodObservation


# ---------------------------------------------------------------------------
# _linear_slope helper
# ---------------------------------------------------------------------------


class TestLinearSlope:
    def test_perfectly_increasing_series(self):
        # y = x → slope = 1.
        assert _linear_slope([0, 1, 2, 3, 4]) == pytest.approx(1.0)

    def test_perfectly_decreasing_series(self):
        assert _linear_slope([4, 3, 2, 1, 0]) == pytest.approx(-1.0)

    def test_flat_series_slope_zero(self):
        assert _linear_slope([5, 5, 5, 5]) == 0.0

    def test_short_series_slope_zero(self):
        assert _linear_slope([]) == 0.0
        assert _linear_slope([42.0]) == 0.0

    def test_noisy_increasing_series_positive_slope(self):
        # y = x + random noise, positive slope on average.
        slope = _linear_slope([1, 3, 2, 5, 4, 7, 6, 9])
        assert slope > 0.5


# ---------------------------------------------------------------------------
# _build_state — Syntetos-Boylan classification inputs
# ---------------------------------------------------------------------------


def _obs(period_start: date, units: float) -> _PeriodObservation:
    return _PeriodObservation(period_start=period_start, units=units)


@pytest.fixture
def builder():
    return LaneForecastInputBuilder()


class TestBuildStateSmoothLane:
    def test_smooth_dense_series(self, builder):
        # 12 weeks of nearly-constant demand, no zeros.
        ps = date(2026, 5, 4)
        observations = [
            _obs(date(2026, 2, 9) + timedelta(days=7 * i), 100.0 + i * 0.5)
            for i in range(12)
        ]
        state = builder._build_state(
            lane_id=10, period_start=ps, observations=observations,
        )
        assert state.lane_id == 10
        assert state.period_start == ps
        assert state.weeks_of_history == 12
        # Mean ≈ 102.75, std small.
        assert state.mean_demand == pytest.approx(102.75, abs=0.1)
        assert state.demand_std < 5
        # ADI ≈ 1 (every period non-zero).
        assert state.avg_demand_interval == pytest.approx(1.0)
        # CV² very small for this nearly-flat series.
        assert state.squared_cv < 0.01
        # Trend positive (series grows by 0.5/period).
        assert state.trend_slope > 0
        # Proposed forecast is trailing-mean of last 4 obs.
        assert 100 <= state.proposed_forecast_p50 <= 110
        # P10 < P50 < P90.
        assert state.proposed_forecast_p10 <= state.proposed_forecast_p50
        assert state.proposed_forecast_p50 <= state.proposed_forecast_p90


class TestBuildStateIntermittentLane:
    def test_intermittent_with_gaps(self, builder):
        # 10 weeks, only 3 of them have shipments — high ADI, high CV².
        ps = date(2026, 5, 4)
        observations = [
            _obs(date(2026, 2, 23), 200.0),  # week 0
            _obs(date(2026, 3, 16), 50.0),   # week 3
            _obs(date(2026, 4, 27), 300.0),  # week 9
        ]
        state = builder._build_state(
            lane_id=11, period_start=ps, observations=observations,
        )
        # 10 periods of dense series, 3 nonzero.
        assert state.weeks_of_history == 10
        assert state.nonzero_period_pct == pytest.approx(0.3)
        # ADI = average gap between non-zero events = (3 + 6) / 2 = 4.5
        assert state.avg_demand_interval == pytest.approx(4.5)
        # CV² high — values vary 50/200/300.
        assert state.squared_cv > 0.3


class TestBuildStateEmptyHistory:
    def test_no_observations_returns_empty_state(self, builder):
        ps = date(2026, 5, 4)
        state = builder._build_state(
            lane_id=12, period_start=ps, observations=[],
        )
        assert state.lane_id == 12
        assert state.period_start == ps
        assert state.weeks_of_history == 0
        assert state.mean_demand == 0.0


class TestBuildStateSingleObservation:
    def test_single_obs_yields_minimum_history(self, builder):
        ps = date(2026, 5, 4)
        state = builder._build_state(
            lane_id=13,
            period_start=ps,
            observations=[_obs(date(2026, 5, 1), 100.0)],
        )
        assert state.weeks_of_history == 1
        assert state.mean_demand == 100.0
        assert state.demand_std == 0.0  # one obs, no variance
        # Proposed forecast = the single observed value.
        assert state.proposed_forecast_p50 == 100.0


class TestBuildStateDecliningLane:
    def test_strong_negative_trend(self, builder):
        # 10 weeks of monotonically decreasing demand.
        ps = date(2026, 5, 4)
        observations = [
            _obs(date(2026, 2, 23) + timedelta(days=7 * i), 100.0 - i * 8.0)
            for i in range(10)
        ]
        state = builder._build_state(
            lane_id=14, period_start=ps, observations=observations,
        )
        # Trend slope strongly negative.
        assert state.trend_slope < -1.0
        # Last obs (week 9) ≈ 28.0
        assert state.last_period_actual == pytest.approx(28.0, abs=0.1)


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


class TestBuilderValidation:
    def test_default_period_days(self):
        builder = LaneForecastInputBuilder()
        assert builder.period_days == 7
        assert builder.history_weeks == 26

    def test_custom_period_days(self):
        builder = LaneForecastInputBuilder(period_days=14)
        assert builder.period_days == 14

    def test_invalid_period_days(self):
        with pytest.raises(ValueError, match="period_days"):
            LaneForecastInputBuilder(period_days=0)
        with pytest.raises(ValueError, match="period_days"):
            LaneForecastInputBuilder(period_days=400)

    def test_invalid_history_weeks(self):
        with pytest.raises(ValueError, match="history_weeks"):
            LaneForecastInputBuilder(history_weeks=0)
        with pytest.raises(ValueError, match="history_weeks"):
            LaneForecastInputBuilder(history_weeks=300)


# ---------------------------------------------------------------------------
# Trailing-window proposed forecast
# ---------------------------------------------------------------------------


class TestProposedForecastTrailingWindow:
    def test_trailing_mean_of_last_four_periods(self, builder):
        # 10 weeks dense; last 4 are [100, 110, 120, 130] → mean=115.
        ps = date(2026, 5, 4)
        values = [50, 60, 70, 80, 90, 95, 100, 110, 120, 130]
        observations = [
            _obs(date(2026, 2, 23) + timedelta(days=7 * i), float(v))
            for i, v in enumerate(values)
        ]
        state = builder._build_state(
            lane_id=15, period_start=ps, observations=observations,
        )
        assert state.proposed_forecast_p50 == pytest.approx(115.0)
        # Naive bands: P50 ± stdev of trailing window.
        # stdev([100, 110, 120, 130]) ≈ 11.18
        assert state.proposed_forecast_p10 < state.proposed_forecast_p50
        assert state.proposed_forecast_p90 > state.proposed_forecast_p50

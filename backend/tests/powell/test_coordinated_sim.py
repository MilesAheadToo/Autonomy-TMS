"""
Tests for Sprint 5: Coordinated Simulation Runner

Validates that:
1. CoordinatedSimRunner produces traces with the configured TRM types
2. MultiHeadTrace captures per-TRM snapshots and urgency evolution
3. Cross-head reward computation is correct
4. EpisodeResult aggregates properly

Hive curriculum tests were removed when ``hive_curriculum.py`` was
retired from TMS (§3.49 tranche 4, 2026-05-18) — that file held only
SCP-domain curriculum classes (MOExecution / ForecastAdjustment /
ForecastBaseline / etc.) and was fork-leftover from the original
TMS-SCP fork. Curriculum coverage for the 10 dispatch-side TMS TRMs
will land alongside their own curriculum classes (no Core lift
pending — see MIGRATION_REGISTER §3.49 tranche 4).
"""

import pytest
import numpy as np

from app.services.powell.hive_signal import (
    HiveSignalBus,
    HiveSignal,
    HiveSignalType,
)
from app.services.powell.coordinated_sim_runner import (
    CoordinatedSimRunner,
    MultiHeadTrace,
    TRMDecisionSnapshot,
    EpisodeResult,
    compute_cross_head_reward,
)
from app.services.powell.decision_cycle import (
    DecisionCyclePhase,
    CycleResult,
    PhaseResult,
    PHASE_TRM_MAP,
)


# ---------------------------------------------------------------------------
# 1. CoordinatedSimRunner basics
# ---------------------------------------------------------------------------

class TestCoordinatedSimRunner:
    """Test that CoordinatedSimRunner produces valid traces."""

    @pytest.fixture
    def signal_bus(self):
        return HiveSignalBus()

    @pytest.fixture
    def runner(self, signal_bus):
        return CoordinatedSimRunner(
            site_key="TEST_SITE",
            signal_bus=signal_bus,
            seed=42,
        )

    def _simple_executor_factory(self, signal_bus):
        """Factory that returns simple callables for all 11 TRMs."""
        all_trms = [
            "atp_executor", "rebalancing", "po_creation", "order_tracking",
            "mo_execution", "to_execution", "quality", "maintenance",
            "subcontracting", "forecast_adj", "inventory_buffer",
        ]

        def factory(period):
            executors = {}
            for trm in all_trms:
                def make_exec(name=trm):
                    def executor():
                        # Emit a signal
                        sig = HiveSignal(
                            signal_type=HiveSignalType.ATP_SHORTAGE,
                            source_trm=name,
                            urgency=0.5,
                        )
                        signal_bus.emit(sig)
                        signal_bus.urgency.update(name, 0.3, "risk")
                        return name
                    return executor
                executors[trm] = make_exec()
            return executors
        return factory

    def test_run_episode_produces_traces(self, runner, signal_bus):
        factory = self._simple_executor_factory(signal_bus)
        result = runner.run_episode(num_periods=5, executor_factory=factory)

        assert isinstance(result, EpisodeResult)
        assert result.site_key == "TEST_SITE"
        assert result.num_periods == 5
        assert len(result.traces) == 5

    def test_traces_have_decisions(self, runner, signal_bus):
        factory = self._simple_executor_factory(signal_bus)
        result = runner.run_episode(num_periods=3, executor_factory=factory)

        for trace in result.traces:
            assert isinstance(trace, MultiHeadTrace)
            assert len(trace.decisions) > 0
            assert trace.site_key == "TEST_SITE"

    def test_decisions_are_snapshots(self, runner, signal_bus):
        factory = self._simple_executor_factory(signal_bus)
        result = runner.run_episode(num_periods=1, executor_factory=factory)

        for decision in result.traces[0].decisions:
            assert isinstance(decision, TRMDecisionSnapshot)
            assert decision.trm_name in [
                "atp_executor", "rebalancing", "po_creation", "order_tracking",
                "mo_execution", "to_execution", "quality", "maintenance",
                "subcontracting", "forecast_adj", "inventory_buffer",
            ]
            assert decision.phase in [p.name for p in DecisionCyclePhase]

    def test_all_11_trms_appear_in_trace(self, runner, signal_bus):
        factory = self._simple_executor_factory(signal_bus)
        result = runner.run_episode(num_periods=1, executor_factory=factory)

        trm_names = {d.trm_name for d in result.traces[0].decisions}
        assert len(trm_names) == 11

    def test_urgency_snapshot_captured(self, runner, signal_bus):
        factory = self._simple_executor_factory(signal_bus)
        result = runner.run_episode(num_periods=1, executor_factory=factory)

        trace = result.traces[0]
        assert trace.urgency_snapshot is not None
        assert "values" in trace.urgency_snapshot

    def test_cross_head_reward_computed(self, runner, signal_bus):
        factory = self._simple_executor_factory(signal_bus)
        result = runner.run_episode(num_periods=3, executor_factory=factory)

        # Should have some reward from successful phase execution
        assert result.total_cross_head_reward != 0.0

    def test_signal_count_tracked(self, runner, signal_bus):
        factory = self._simple_executor_factory(signal_bus)
        result = runner.run_episode(num_periods=3, executor_factory=factory)

        assert result.avg_signals_per_period > 0

    def test_episode_to_dict(self, runner, signal_bus):
        factory = self._simple_executor_factory(signal_bus)
        result = runner.run_episode(num_periods=2, executor_factory=factory)

        d = result.to_dict()
        assert d["site_key"] == "TEST_SITE"
        assert d["num_periods"] == 2
        assert "total_cross_head_reward" in d
        assert "traces_count" in d

    def test_trace_to_dict(self, runner, signal_bus):
        factory = self._simple_executor_factory(signal_bus)
        result = runner.run_episode(num_periods=1, executor_factory=factory)

        d = result.traces[0].to_dict()
        assert "trace_id" in d
        assert "decisions" in d
        assert "urgency_snapshot" in d
        assert "cross_head_reward" in d

    def test_decision_snapshot_to_dict(self, runner, signal_bus):
        factory = self._simple_executor_factory(signal_bus)
        result = runner.run_episode(num_periods=1, executor_factory=factory)

        d = result.traces[0].decisions[0].to_dict()
        assert "trm_name" in d
        assert "phase" in d
        assert "urgency_before" in d
        assert "urgency_after" in d
        assert "signals_emitted" in d


# ---------------------------------------------------------------------------
# 2. run_batch
# ---------------------------------------------------------------------------

class TestRunBatch:
    """Test batch episode generation."""

    @pytest.fixture
    def signal_bus(self):
        return HiveSignalBus()

    def test_run_batch_returns_multiple_episodes(self, signal_bus):
        runner = CoordinatedSimRunner(site_key="BATCH", signal_bus=signal_bus, seed=99)

        def factory(period):
            return {
                "atp_executor": lambda: "ok",
                "rebalancing": lambda: "ok",
            }

        results = runner.run_batch(num_episodes=3, num_periods=4, executor_factory=factory)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, EpisodeResult)
            assert r.num_periods == 4


# ---------------------------------------------------------------------------
# 3. Cross-head reward computation
# ---------------------------------------------------------------------------

class TestCrossHeadReward:
    """Test compute_cross_head_reward logic."""

    def _make_cycle_result(self, phase_successes, signals_per_phase, conflicts):
        cr = CycleResult()
        for i, (success, signals) in enumerate(zip(phase_successes, signals_per_phase)):
            pr = PhaseResult(phase=list(DecisionCyclePhase)[i])
            if success:
                pr.trms_executed.append("test_trm")
            else:
                pr.errors.append("test error")
            pr.signals_emitted = signals
            cr.phases.append(pr)
        cr.conflicts_detected = [f"conflict_{i}" for i in range(conflicts)]
        return cr

    def test_perfect_cycle_gets_high_reward(self):
        cr = self._make_cycle_result(
            [True] * 6, [1] * 6, conflicts=0
        )
        urgency = {"values": [0.3, 0.3, 0.3]}
        reward = compute_cross_head_reward(cr, urgency)
        # 6 * 0.1 (success) + 6 * 0.05 (signals) + 0.2 (low spread) + 0.3 (no conflicts) = 1.4
        assert abs(reward - 1.4) < 0.01

    def test_all_failures_negative_reward(self):
        cr = self._make_cycle_result(
            [False] * 6, [0] * 6, conflicts=3
        )
        urgency = {"values": []}
        reward = compute_cross_head_reward(cr, urgency)
        # 6 * -0.2 (errors) + 3 * -0.1 (conflicts) = -1.5
        assert reward < 0

    def test_no_conflicts_bonus(self):
        cr = self._make_cycle_result(
            [True] * 6, [0] * 6, conflicts=0
        )
        urgency = {"values": []}  # no active urgency → no spread bonus
        reward = compute_cross_head_reward(cr, urgency)
        # 6 * 0.1 + 0.3 = 0.9
        assert abs(reward - 0.9) < 0.01

    def test_low_urgency_spread_bonus(self):
        cr = self._make_cycle_result(
            [True] * 6, [0] * 6, conflicts=0
        )
        # All similar urgency → low std → bonus
        urgency = {"values": [0.5, 0.5, 0.5, 0.5]}
        reward = compute_cross_head_reward(cr, urgency)
        # 6 * 0.1 + 0.2 (low spread) + 0.3 (no conflicts) = 1.1
        assert abs(reward - 1.1) < 0.01

    def test_high_urgency_spread_no_bonus(self):
        cr = self._make_cycle_result(
            [True] * 6, [0] * 6, conflicts=0
        )
        # Wide spread → no spread bonus
        urgency = {"values": [0.1, 0.9, 0.1, 0.9]}
        reward = compute_cross_head_reward(cr, urgency)
        # 6 * 0.1 + 0.3 (no conflicts) = 0.9 (no spread bonus)
        assert abs(reward - 0.9) < 0.01


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Test edge cases and robustness."""

    def test_empty_executors(self):
        """Runner handles empty executor dict gracefully."""
        bus = HiveSignalBus()
        runner = CoordinatedSimRunner(site_key="EMPTY", signal_bus=bus)
        result = runner.run_episode(num_periods=1, executor_factory=lambda p: {})
        assert len(result.traces) == 1
        assert len(result.traces[0].decisions) == 0

    def test_executor_exception_handled(self):
        """Runner handles executor exceptions gracefully."""
        bus = HiveSignalBus()
        runner = CoordinatedSimRunner(site_key="ERR", signal_bus=bus)

        def bad_factory(period):
            return {"atp_executor": lambda: (_ for _ in ()).throw(ValueError("boom"))}

        result = runner.run_episode(num_periods=1, executor_factory=bad_factory)
        assert len(result.traces) == 1
        # Decision snapshot not added because executor raised
        assert len(result.traces[0].decisions) == 0

    def test_single_period_episode(self):
        bus = HiveSignalBus()
        runner = CoordinatedSimRunner(site_key="SINGLE", signal_bus=bus)
        result = runner.run_episode(
            num_periods=1,
            executor_factory=lambda p: {"atp_executor": lambda: 42},
        )
        assert result.num_periods == 1
        assert len(result.traces) == 1

    def test_urgency_evolves_across_periods(self):
        """Urgency should change over multiple periods."""
        bus = HiveSignalBus()
        runner = CoordinatedSimRunner(site_key="EVOLVE", signal_bus=bus, seed=7)

        call_count = [0]

        def factory(period):
            def atp_exec():
                call_count[0] += 1
                bus.urgency.update("atp_executor", min(1.0, period * 0.1 + 0.1), "risk")
                return "ok"
            return {"atp_executor": atp_exec}

        result = runner.run_episode(num_periods=5, executor_factory=factory)
        assert call_count[0] == 5

        # Urgency should increase across periods
        urgencies = [
            next(d.urgency_after for d in t.decisions if d.trm_name == "atp_executor")
            for t in result.traces
        ]
        assert urgencies[-1] > urgencies[0]

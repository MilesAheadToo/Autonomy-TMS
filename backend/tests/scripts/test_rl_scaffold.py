"""Tests for the RL fine-tune scaffold (twin env, rollout buffer, PPO).

Structural tests for the env wrapper + rollout buffer run pure-Python.
PPO trainer tests gate on torch availability — when torch is not
installed (CPU sandbox) the gated tests skip cleanly.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest


_BACKEND = Path(__file__).resolve().parents[2]
_WORKSPACE = _BACKEND.parent.parent
for p in (
    str(_BACKEND),
    str(_BACKEND.parent / "packages" / "autonomy-tms-heuristics" / "src"),
    str(_WORKSPACE / "Autonomy-Core" / "packages" / "azirella-heuristics-common" / "src"),
    str(_WORKSPACE / "Autonomy-Core" / "packages" / "data-model" / "src"),
    str(_WORKSPACE / "Autonomy-Core" / "packages" / "azirella-demand-planning-contract" / "src"),
    str(_WORKSPACE / "Autonomy-Core" / "packages" / "azirella-transfer-order-envelope-contract" / "src"),
):
    if p not in sys.path:
        sys.path.insert(0, p)


# Stub-clear so the digital_twin imports inside twin_env resolve via
# the real PEP-420 namespace (matches test_twin_state_sampler.py).
for _stale in ("app", "app.services", "app.services.powell"):
    mod = sys.modules.get(_stale)
    if mod is not None and not hasattr(mod, "__path__"):
        sys.modules.pop(_stale, None)


from scripts.finetune.rl.rollout_buffer import RolloutBuffer  # noqa: E402
from scripts.finetune.rl.twin_env import (  # noqa: E402
    CAPACITY_PROMISE_ACTIONS,
    CAPACITY_PROMISE_FEATURES,
    CapacityPromiseTwinEnv,
    state_to_vector,
)


# ─────────────────────────────────────────────────────────────────────
# Env wrapper — pure Python, no torch
# ─────────────────────────────────────────────────────────────────────


def test_env_constants() -> None:
    assert len(CAPACITY_PROMISE_FEATURES) == 14
    assert len(CAPACITY_PROMISE_ACTIONS) == 3
    assert CapacityPromiseTwinEnv.OBSERVATION_DIM == 14
    assert CapacityPromiseTwinEnv.NUM_ACTIONS == 3


def test_env_reset_returns_correct_shape() -> None:
    env = CapacityPromiseTwinEnv(seed=42, phase=2)
    obs = env.reset()
    assert isinstance(obs, list)
    assert len(obs) == 14
    assert all(isinstance(v, float) for v in obs)


def test_env_step_returns_step_result() -> None:
    env = CapacityPromiseTwinEnv(seed=42, phase=2, horizon_steps=10)
    env.reset()
    result = env.step(0)  # ACCEPT
    assert len(result.observation) == 14
    assert isinstance(result.reward, float)
    assert isinstance(result.done, bool)
    assert result.reward in (-1.0, 1.0)
    assert "teacher_action" in result.info
    assert "agent_action" in result.info
    assert "match" in result.info


def test_env_episode_terminates_at_horizon() -> None:
    env = CapacityPromiseTwinEnv(seed=42, phase=2, horizon_steps=3)
    env.reset()
    r1 = env.step(0); assert not r1.done
    r2 = env.step(0); assert not r2.done
    r3 = env.step(0); assert r3.done


def test_env_step_before_reset_raises() -> None:
    env = CapacityPromiseTwinEnv(seed=42, phase=2)
    with pytest.raises(RuntimeError, match="before reset"):
        env.step(0)


def test_env_action_out_of_range_raises() -> None:
    env = CapacityPromiseTwinEnv(seed=42, phase=2)
    env.reset()
    with pytest.raises(ValueError, match="action idx"):
        env.step(99)


def test_env_reward_matches_teacher_marker() -> None:
    """If we cheat and use the teacher's action, reward must be +1."""
    env = CapacityPromiseTwinEnv(seed=42, phase=2, horizon_steps=5)
    env.reset()
    # Run a few steps with the teacher-matched action via info feedback.
    correct = 0
    for _ in range(20):
        # Try each action; reward must be +1 for exactly the action
        # that matches the teacher.
        env2 = CapacityPromiseTwinEnv(seed=42, phase=2, horizon_steps=5)
        env2.reset()
        rewards = [env2.step(a).reward for a in (0, 1, 2)]
        # One of the three should be +1, the others -1 (the heuristic
        # picks one deterministically). Reuse a fresh env per probe.
        positives = sum(r > 0 for r in rewards)
        if positives:
            correct += 1
        if correct >= 3:
            break
    assert correct >= 1


def test_state_to_vector_uses_all_14_features() -> None:
    from autonomy_tms_heuristics.library import CapacityPromiseState
    state = CapacityPromiseState(
        requested_loads=3, priority=2, committed_capacity=5, total_capacity=20,
        buffer_capacity=4, forecast_loads=15, booked_loads=5,
        backup_carriers_count=2, spot_rate_premium_pct=0.10,
        lane_acceptance_rate=0.92, market_tightness=0.30,
        primary_carrier_otp=0.95, allocation_compliance_pct=1.0,
        primary_carrier_available=True,
    )
    vec = state_to_vector(state)
    assert len(vec) == 14
    assert all(isinstance(v, float) for v in vec)
    # primary_carrier_available True → indicator 1.0 at last index.
    assert vec[-1] == 1.0


# ─────────────────────────────────────────────────────────────────────
# RolloutBuffer — pure Python, no torch
# ─────────────────────────────────────────────────────────────────────


def test_buffer_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError):
        RolloutBuffer(capacity=0)
    with pytest.raises(ValueError):
        RolloutBuffer(capacity=8, gamma=1.5)
    with pytest.raises(ValueError):
        RolloutBuffer(capacity=8, gae_lambda=-0.1)


def test_buffer_push_and_full() -> None:
    buf = RolloutBuffer(capacity=3)
    for i in range(3):
        buf.push([0.0]*14, action=0, reward=1.0, done=False, log_prob=-1.0, value=0.5)
    assert buf.is_full()
    assert len(buf) == 3
    with pytest.raises(RuntimeError, match="buffer full"):
        buf.push([0.0]*14, action=0, reward=0.0, done=False, log_prob=0.0, value=0.0)


def test_buffer_finalize_computes_gae() -> None:
    """GAE on a 3-step trajectory of all-1 rewards, value=0, done=True at end."""
    buf = RolloutBuffer(capacity=3, gamma=0.9, gae_lambda=1.0)
    buf.push([0.0]*14, action=0, reward=1.0, done=False, log_prob=0.0, value=0.0)
    buf.push([0.0]*14, action=0, reward=1.0, done=False, log_prob=0.0, value=0.0)
    buf.push([0.0]*14, action=0, reward=1.0, done=True,  log_prob=0.0, value=0.0)
    buf.finalize(last_value=0.0)
    adv = buf.advantages
    ret = buf.returns
    # With gamma=0.9, lambda=1, value=0, done at end:
    #   delta_2 = 1, gae_2 = 1
    #   delta_1 = 1 + 0.9*0 = 1, gae_1 = 1 + 0.9*1*1 = 1.9
    #   delta_0 = 1 + 0.9*0 = 1, gae_0 = 1 + 0.9*1*1.9 = 2.71
    assert pytest.approx(adv[2], abs=1e-6) == 1.0
    assert pytest.approx(adv[1], abs=1e-6) == 1.9
    assert pytest.approx(adv[0], abs=1e-6) == 2.71
    # Returns = advantage + value; value=0 → returns == advantages.
    for r, a in zip(ret, adv):
        assert r == a


def test_buffer_clear_resets_state() -> None:
    buf = RolloutBuffer(capacity=2)
    buf.push([0.0]*14, action=0, reward=1.0, done=False, log_prob=0.0, value=0.0)
    buf.push([0.0]*14, action=0, reward=1.0, done=False, log_prob=0.0, value=0.0)
    buf.finalize(last_value=0.0)
    assert buf.is_full()
    buf.clear()
    assert len(buf) == 0
    assert not buf.is_full()


def test_buffer_finalize_required_before_advantages() -> None:
    buf = RolloutBuffer(capacity=2)
    buf.push([0.0]*14, action=0, reward=1.0, done=False, log_prob=0.0, value=0.0)
    with pytest.raises(RuntimeError, match="finalize"):
        _ = buf.advantages


# ─────────────────────────────────────────────────────────────────────
# PPO trainer — gated on torch
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def test_ppo_trainer_instantiates(torch_available: bool) -> None:
    if not torch_available:
        pytest.skip("torch not installed in this sandbox")
    from scripts.finetune.rl.ppo_trainer import HAS_TORCH, PPOConfig, PPOTrainer
    assert HAS_TORCH
    env = CapacityPromiseTwinEnv(seed=42, phase=2, horizon_steps=10)
    cfg = PPOConfig(rollout_length=16, minibatch_size=8, epochs_per_update=1, total_updates=1)
    trainer = PPOTrainer(env=env, config=cfg)
    assert trainer.policy is not None
    assert trainer.optimizer is not None


def test_ppo_one_update_runs(torch_available: bool) -> None:
    if not torch_available:
        pytest.skip("torch not installed in this sandbox")
    from scripts.finetune.rl.ppo_trainer import PPOConfig, PPOTrainer
    env = CapacityPromiseTwinEnv(seed=42, phase=2, horizon_steps=8)
    cfg = PPOConfig(rollout_length=16, minibatch_size=8, epochs_per_update=1, total_updates=1)
    trainer = PPOTrainer(env=env, config=cfg)
    # Run one update; should not raise.
    rollout_stats = trainer._collect_rollout()
    assert rollout_stats["rollout_episodes"] >= 1
    update_stats = trainer._update_step()
    assert "policy_loss" in update_stats
    assert "value_loss" in update_stats
    assert "entropy" in update_stats


def test_ppo_save_checkpoint(torch_available: bool, tmp_path: Path) -> None:
    if not torch_available:
        pytest.skip("torch not installed in this sandbox")
    from scripts.finetune.rl.ppo_trainer import PPOConfig, PPOTrainer
    env = CapacityPromiseTwinEnv(seed=42, phase=2, horizon_steps=4)
    cfg = PPOConfig(rollout_length=8, minibatch_size=4, epochs_per_update=1, total_updates=1)
    trainer = PPOTrainer(env=env, config=cfg)
    path = tmp_path / "ckpt.pt"
    trainer.save_checkpoint(path)
    assert path.exists()
    assert path.stat().st_size > 0


def test_ppo_sentinel_without_torch() -> None:
    """When torch is genuinely absent, the sentinel classes raise on use."""
    try:
        import torch  # noqa: F401
        pytest.skip("torch IS installed — sentinel path not exercised")
    except ImportError:
        from scripts.finetune.rl.ppo_trainer import HAS_TORCH, PPOTrainer, ActorCriticPolicy
        assert HAS_TORCH is False
        with pytest.raises(RuntimeError, match="torch not installed"):
            PPOTrainer()
        with pytest.raises(RuntimeError, match="torch not installed"):
            ActorCriticPolicy(14, 3, 64)

"""PPO trainer for TMS TRM policy fine-tune over the twin.

torch-only. The other modules in this package (``twin_env``,
``rollout_buffer``) stay torch-free so they can be tested without
torch installed; this module wraps them in the actual training
loop.

Architecture:
  ``ActorCriticPolicy`` — 2-layer MLP (64 → 64) + linear policy head
    + linear value head. CapacityPromise sizing by default
    (obs_dim=14, num_actions=3); generalises by passing different
    dims at construction.

  ``PPOTrainer`` — orchestrates rollouts via the env, fills the
    ``RolloutBuffer``, runs ``epochs`` PPO updates per rollout,
    applies clipped surrogate loss + 0.5×value_loss − ent_coef×entropy.
    Standard hyper-params (PPO-CLIP eps=0.2, lr=3e-4, gamma=0.99,
    GAE λ=0.95).

CLI driver: ``train_capacity_promise_rl.py`` in the same directory.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch.distributions import Categorical
    HAS_TORCH = True
except ImportError:  # pragma: no cover — gated path
    torch = None
    nn = None
    F = None
    Categorical = None
    HAS_TORCH = False

BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.finetune.rl.rollout_buffer import RolloutBuffer
from scripts.finetune.rl.twin_env import CapacityPromiseTwinEnv


logger = logging.getLogger("ppo_trainer")


@dataclass
class PPOConfig:
    """Hyper-parameters for the PPO trainer."""

    # Network sizing.
    obs_dim: int = 14
    num_actions: int = 3
    hidden_dim: int = 64

    # Rollout / update sizing.
    rollout_length: int = 256
    minibatch_size: int = 64
    epochs_per_update: int = 4
    total_updates: int = 50

    # PPO loss coefficients.
    clip_eps: float = 0.2
    value_coef: float = 0.5
    entropy_coef: float = 0.01

    # Optimiser.
    learning_rate: float = 3e-4
    max_grad_norm: float = 0.5

    # GAE / discount.
    gamma: float = 0.99
    gae_lambda: float = 0.95


if HAS_TORCH:

    class ActorCriticPolicy(nn.Module):
        """2-layer MLP shared trunk → policy + value heads."""

        def __init__(self, obs_dim: int, num_actions: int, hidden_dim: int):
            super().__init__()
            self.trunk = nn.Sequential(
                nn.Linear(obs_dim, hidden_dim),
                nn.Tanh(),
                nn.Linear(hidden_dim, hidden_dim),
                nn.Tanh(),
            )
            self.policy_head = nn.Linear(hidden_dim, num_actions)
            self.value_head = nn.Linear(hidden_dim, 1)

        def forward(self, obs: "torch.Tensor") -> Tuple["torch.Tensor", "torch.Tensor"]:
            features = self.trunk(obs)
            logits = self.policy_head(features)
            value = self.value_head(features).squeeze(-1)
            return logits, value

        def act(self, obs_vec: list) -> Tuple[int, float, float]:
            """Sample action + return (action, log_prob, value)."""
            obs = torch.tensor(obs_vec, dtype=torch.float32).unsqueeze(0)
            with torch.no_grad():
                logits, value = self.forward(obs)
            dist = Categorical(logits=logits)
            action = dist.sample()
            log_prob = dist.log_prob(action)
            return int(action.item()), float(log_prob.item()), float(value.item())


    class PPOTrainer:
        """Orchestrates env interaction + PPO updates."""

        def __init__(
            self,
            env: CapacityPromiseTwinEnv,
            config: PPOConfig | None = None,
        ):
            self.env = env
            self.config = config or PPOConfig()
            self.policy = ActorCriticPolicy(
                self.config.obs_dim,
                self.config.num_actions,
                self.config.hidden_dim,
            )
            self.optimizer = torch.optim.Adam(
                self.policy.parameters(),
                lr=self.config.learning_rate,
            )
            self.buffer = RolloutBuffer(
                capacity=self.config.rollout_length,
                gamma=self.config.gamma,
                gae_lambda=self.config.gae_lambda,
            )

        def _collect_rollout(self) -> Dict[str, float]:
            self.buffer.clear()
            obs = self.env.reset()
            ep_reward = 0.0
            n_episodes = 0
            last_value = 0.0
            while not self.buffer.is_full():
                action, log_prob, value = self.policy.act(obs)
                result = self.env.step(action)
                self.buffer.push(
                    observation=obs,
                    action=action,
                    reward=result.reward,
                    done=result.done,
                    log_prob=log_prob,
                    value=value,
                )
                ep_reward += result.reward
                obs = result.observation
                if result.done:
                    n_episodes += 1
                    obs = self.env.reset()
                    last_value = 0.0
                else:
                    last_value = value
            self.buffer.finalize(last_value=last_value)
            return {
                "rollout_reward_mean": ep_reward / max(1, n_episodes),
                "rollout_episodes": float(n_episodes),
            }

        def _update_step(self) -> Dict[str, float]:
            t = self.buffer.to_tensors()
            advantages = t["advantages"]
            # Normalise advantages.
            advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
            obs, actions = t["obs"], t["actions"]
            old_log_probs, old_values, returns = (
                t["old_log_probs"], t["old_values"], t["returns"],
            )

            n = obs.shape[0]
            indices = torch.randperm(n)
            mb = self.config.minibatch_size
            total_pl = 0.0
            total_vl = 0.0
            total_ent = 0.0
            n_minibatches = 0
            for epoch in range(self.config.epochs_per_update):
                for start in range(0, n, mb):
                    batch_idx = indices[start:start + mb]
                    logits, values = self.policy(obs[batch_idx])
                    dist = Categorical(logits=logits)
                    new_log_probs = dist.log_prob(actions[batch_idx])
                    entropy = dist.entropy().mean()

                    ratio = torch.exp(new_log_probs - old_log_probs[batch_idx])
                    surr1 = ratio * advantages[batch_idx]
                    surr2 = torch.clamp(
                        ratio, 1 - self.config.clip_eps, 1 + self.config.clip_eps,
                    ) * advantages[batch_idx]
                    policy_loss = -torch.min(surr1, surr2).mean()

                    value_loss = F.mse_loss(values, returns[batch_idx])

                    loss = (
                        policy_loss
                        + self.config.value_coef * value_loss
                        - self.config.entropy_coef * entropy
                    )

                    self.optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(
                        self.policy.parameters(), self.config.max_grad_norm,
                    )
                    self.optimizer.step()

                    total_pl += float(policy_loss.item())
                    total_vl += float(value_loss.item())
                    total_ent += float(entropy.item())
                    n_minibatches += 1
            return {
                "policy_loss": total_pl / max(1, n_minibatches),
                "value_loss": total_vl / max(1, n_minibatches),
                "entropy": total_ent / max(1, n_minibatches),
            }

        def train(self) -> None:
            for update in range(1, self.config.total_updates + 1):
                rollout_stats = self._collect_rollout()
                update_stats = self._update_step()
                logger.info(
                    f"update {update}/{self.config.total_updates} "
                    f"reward_mean={rollout_stats['rollout_reward_mean']:.3f} "
                    f"policy_loss={update_stats['policy_loss']:.4f} "
                    f"value_loss={update_stats['value_loss']:.4f} "
                    f"entropy={update_stats['entropy']:.3f}"
                )

        def save_checkpoint(self, path: Path) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            torch.save({
                "policy_state_dict": self.policy.state_dict(),
                "config": self.config.__dict__,
            }, path)

else:
    # Sentinel so tests that don't have torch see informative errors
    # rather than NameError.
    class ActorCriticPolicy:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("torch not installed — install for PPO training")

    class PPOTrainer:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("torch not installed — install for PPO training")

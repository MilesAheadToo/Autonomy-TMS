"""Rollout buffer + GAE advantage computation for PPO.

Pure-Python (no torch dependency) — feeds a torch-based trainer via
the ``to_tensors`` method which delegates to torch only when needed.
This keeps the buffer testable in a torch-less sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple


@dataclass
class Transition:
    """One (s, a, r, done, log_prob, value) tuple."""

    observation: List[float]
    action: int
    reward: float
    done: bool
    log_prob: float
    value: float


class RolloutBuffer:
    """Fixed-size FIFO buffer for PPO rollouts.

    Stores ``capacity`` transitions; computes GAE advantages and
    discounted returns at finalisation.
    """

    def __init__(self, capacity: int, gamma: float = 0.99, gae_lambda: float = 0.95):
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0; got {capacity}")
        if not 0.0 < gamma <= 1.0:
            raise ValueError(f"gamma must be in (0, 1]; got {gamma}")
        if not 0.0 < gae_lambda <= 1.0:
            raise ValueError(f"gae_lambda must be in (0, 1]; got {gae_lambda}")
        self.capacity = int(capacity)
        self.gamma = float(gamma)
        self.gae_lambda = float(gae_lambda)
        self._transitions: List[Transition] = []
        self._advantages: List[float] | None = None
        self._returns: List[float] | None = None

    def __len__(self) -> int:
        return len(self._transitions)

    def push(
        self,
        observation: List[float],
        action: int,
        reward: float,
        done: bool,
        log_prob: float,
        value: float,
    ) -> None:
        if len(self._transitions) >= self.capacity:
            raise RuntimeError(
                f"buffer full ({self.capacity}); call clear() or finalize() first"
            )
        self._transitions.append(Transition(
            observation=list(observation),
            action=int(action),
            reward=float(reward),
            done=bool(done),
            log_prob=float(log_prob),
            value=float(value),
        ))

    def finalize(self, last_value: float = 0.0) -> None:
        """Compute GAE advantages + discounted returns over all stored
        transitions. ``last_value`` is the bootstrap value of the
        state that follows the final transition (set to 0 when the
        episode ended cleanly).
        """
        n = len(self._transitions)
        advantages = [0.0] * n
        returns = [0.0] * n
        gae = 0.0
        next_value = float(last_value)
        for t in reversed(range(n)):
            tr = self._transitions[t]
            mask = 0.0 if tr.done else 1.0
            delta = tr.reward + self.gamma * next_value * mask - tr.value
            gae = delta + self.gamma * self.gae_lambda * mask * gae
            advantages[t] = gae
            returns[t] = gae + tr.value
            next_value = tr.value
        self._advantages = advantages
        self._returns = returns

    @property
    def transitions(self) -> List[Transition]:
        return list(self._transitions)

    @property
    def advantages(self) -> List[float]:
        if self._advantages is None:
            raise RuntimeError("call finalize() before reading advantages")
        return list(self._advantages)

    @property
    def returns(self) -> List[float]:
        if self._returns is None:
            raise RuntimeError("call finalize() before reading returns")
        return list(self._returns)

    def clear(self) -> None:
        self._transitions.clear()
        self._advantages = None
        self._returns = None

    def is_full(self) -> bool:
        return len(self._transitions) >= self.capacity

    def to_tensors(self) -> Any:
        """Materialise the buffer as torch tensors. Imports torch
        lazily so the rest of the module stays torch-free."""
        if self._advantages is None or self._returns is None:
            raise RuntimeError("call finalize() before to_tensors()")
        import torch  # noqa: E402

        obs = torch.tensor(
            [tr.observation for tr in self._transitions], dtype=torch.float32,
        )
        actions = torch.tensor(
            [tr.action for tr in self._transitions], dtype=torch.long,
        )
        old_log_probs = torch.tensor(
            [tr.log_prob for tr in self._transitions], dtype=torch.float32,
        )
        old_values = torch.tensor(
            [tr.value for tr in self._transitions], dtype=torch.float32,
        )
        advantages = torch.tensor(self._advantages, dtype=torch.float32)
        returns = torch.tensor(self._returns, dtype=torch.float32)
        return {
            "obs": obs,
            "actions": actions,
            "old_log_probs": old_log_probs,
            "old_values": old_values,
            "advantages": advantages,
            "returns": returns,
        }

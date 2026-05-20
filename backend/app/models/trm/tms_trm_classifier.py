"""Canonical TMS TRM neural classifier — single parameterized MLP.

All TMS TRMs share one model architecture and one 11-class action space,
varying only by ``input_dim`` (state vector width). Each TRM trains its
own checkpoint of this class on its own state schema, and only consumes
a subset of the 11 actions via the ``active_actions`` mask carried in
the checkpoint.

This module is the canonical home for the classifier + the action
vocabulary. Before 2026-05-20 the class lived inside the BC training
script at ``scripts/pretraining/train_tms_trms.py:68`` — that worked
for offline training on acer-nitro but the ``from scripts.pretraining.
train_tms_trms import TRMClassifier`` shape required at inference time
was both fragile (PYTHONPATH-dependent) and architecturally wrong
(scripts are not a library). Lifting here lets the inference path
(``app.services.powell.bc_checkpoint_loader``) import without that
hack, and lets future training rewrites in ``scripts/`` import the
canonical class instead of re-defining it.

Action vocabulary (11 canonical actions, indices fixed for checkpoint
compatibility — DO NOT reorder; checkpoints encode integer labels):

    0 = ACCEPT        — confirm / execute as-proposed
    1 = REJECT        — refuse the proposal
    2 = DEFER         — hold for next cycle
    3 = ESCALATE      — surface to human reviewer
    4 = MODIFY        — adjust quantity / timing / parameters
    5 = RETENDER      — re-issue to alternate provider
    6 = REROUTE       — change lane / mode / path
    7 = CONSOLIDATE   — combine with another instance
    8 = SPLIT         — break into smaller instances
    9 = REPOSITION    — relocate (equipment / inventory / capacity)
   10 = HOLD          — no-op, maintain current state

Each TRM's checkpoint carries an ``active_actions`` list naming which
of these 11 are meaningful for that TRM (e.g. ``broker_routing`` uses
only ``[ACCEPT, ESCALATE]``). The full 11-wide softmax is preserved
at training time so a single classifier head can serve every TRM;
inference logic masks to the active set before argmax.
"""
from __future__ import annotations

from typing import Tuple

import torch
import torch.nn as nn


# ---------------------------------------------------------------------------
# Action vocabulary — CANONICAL, indices fixed for checkpoint compatibility
# ---------------------------------------------------------------------------

ACTION_NAMES: dict[str, int] = {
    "ACCEPT": 0,
    "REJECT": 1,
    "DEFER": 2,
    "ESCALATE": 3,
    "MODIFY": 4,
    "RETENDER": 5,
    "REROUTE": 6,
    "CONSOLIDATE": 7,
    "SPLIT": 8,
    "REPOSITION": 9,
    "HOLD": 10,
}

NUM_ACTIONS: int = len(ACTION_NAMES)

# Reverse mapping for inference-time label rendering.
ACTION_INDEX_TO_NAME: dict[int, str] = {idx: name for name, idx in ACTION_NAMES.items()}


__all__ = [
    "ACTION_NAMES",
    "ACTION_INDEX_TO_NAME",
    "NUM_ACTIONS",
    "TRMClassifier",
]


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class TRMClassifier(nn.Module):
    """Simple MLP for discrete TRM action classification.

    Input: state feature vector (float32, length ``input_dim``).
    Hidden: ``len(hidden_dims)`` dense layers with ReLU + dropout
    between each.
    Output: logits over :data:`NUM_ACTIONS` classes (always 11).

    Default ``hidden_dims=(128, 64)`` matches the April 2026 BC
    checkpoints; changing the default breaks state_dict load
    compatibility with those files. Callers that want a different
    architecture pass it explicitly.
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dims: Tuple[int, ...] = (128, 64),
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        prev = input_dim
        for h in hidden_dims:
            layers.extend([
                nn.Linear(prev, h),
                nn.ReLU(),
                nn.Dropout(dropout),
            ])
            prev = h
        layers.append(nn.Linear(prev, NUM_ACTIONS))
        self.net = nn.Sequential(*layers)
        # Preserved as attributes so checkpoints can be introspected
        # post-load without re-reading the file.
        self.input_dim = input_dim
        self.hidden_dims = tuple(hidden_dims)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Returns logits — caller applies softmax / argmax."""
        return self.net(x)

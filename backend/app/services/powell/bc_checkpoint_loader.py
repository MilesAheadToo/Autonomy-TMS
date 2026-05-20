"""
Shared BC checkpoint loader for TMS TRMs.

Every TRM follows the same load lifecycle:
  1. Abort early if PyTorch is unavailable (heuristic-only deploy).
  2. Abort early if the checkpoint path doesn't exist.
  3. Load the checkpoint and verify the full contract
     (model_state_dict + input_dim + normalization stats).
  4. Reconstruct the TRMClassifier, load weights, `eval()`.
  5. Return a structured `BcCheckpoint` the TRM can stash.

Before this helper existed, each TRM carried a 10-line stub that only
handled steps (1) and (2). FreightProcurementTRM had a fuller loader
with a broken `from scripts.pretraining.train_tms_trms import ...`
path that silently threw at call time. Consolidating into one helper
kills the duplication, fixes the import path, and surfaces missing
contract fields clearly rather than silently falling through to heuristic.

Checkpoint contract (produced by backend/scripts/pretraining/train_tms_trms.py):
    model_state_dict : Dict[str, Tensor]
    input_dim        : int
    hidden_dims      : Tuple[int, int]           (default (128, 64))
    num_actions      : int                        (11, from ACTION_NAMES)
    feature_keys     : List[str]                  state_/derived_ columns
    feature_means    : List[float]                per-column train-set mean
    feature_stds     : List[float]                per-column train-set std
    trm_type         : str                        canonical TRM name
    best_val_acc     : float
    active_actions   : List[int]

The last three are metadata; only the first seven are required for
inference. `load_bc_checkpoint` returns None (non-fatal) when any
required key is missing and logs a structured WARNING naming the gap.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple  # noqa: F401

logger = logging.getLogger(__name__)

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


_REQUIRED_KEYS = (
    "model_state_dict",
    "input_dim",
    "feature_keys",
    "feature_means",
    "feature_stds",
)


@dataclass
class BcCheckpoint:
    """Parsed + validated BC checkpoint, ready for inference."""
    model: Any  # TRMClassifier — typed loosely to avoid torch at import time
    input_dim: int
    feature_keys: List[str]
    feature_means: List[float]
    feature_stds: List[float]
    num_actions: int
    best_val_acc: float
    active_actions: List[int]
    trm_type: str
    raw: Dict[str, Any]  # full checkpoint dict for caller debugging


def load_bc_checkpoint(
    checkpoint_path: str,
    trm_type: str,
) -> Optional[BcCheckpoint]:
    """Load a BC checkpoint produced by train_tms_trms.py.

    Args:
        checkpoint_path: filesystem path to the `.pt` file.
        trm_type: canonical TRM name (for logging only; the checkpoint
            carries its own `trm_type` key but we don't enforce a match).

    Returns:
        BcCheckpoint on success, None on any failure. Failures are
        logged at WARNING (missing PyTorch / missing file / missing
        contract keys) or ERROR (torch load or state_dict load raised).
        Callers should fall through to heuristic when None comes back.
    """
    if not TORCH_AVAILABLE:
        logger.warning(
            "%s: PyTorch unavailable — falling back to heuristic teacher",
            trm_type,
        )
        return None

    if not os.path.exists(checkpoint_path):
        logger.info(
            "%s: no checkpoint at %s — heuristic teacher",
            trm_type, checkpoint_path,
        )
        return None

    try:
        ckpt = torch.load(checkpoint_path, map_location="cpu")
    except Exception as e:
        logger.error(
            "%s: torch.load failed for %s: %s — heuristic teacher",
            trm_type, checkpoint_path, e,
        )
        return None

    missing = [k for k in _REQUIRED_KEYS if k not in ckpt]
    if missing:
        logger.warning(
            "%s: checkpoint %s missing required keys %s — heuristic teacher. "
            "Re-train with the current trainer (train_tms_trms.py) so "
            "feature_keys/means/stds are persisted.",
            trm_type, checkpoint_path, missing,
        )
        return None

    try:
        # Canonical location since 2026-05-20 — ``app.models.trm.tms_trm_classifier``.
        # The class used to live inside the BC training script at
        # ``scripts/pretraining/train_tms_trms.py``; that file now re-exports
        # from the canonical location so offline training scripts keep
        # working without breaking inference's import path.
        from app.models.trm.tms_trm_classifier import TRMClassifier
    except Exception as e:
        logger.error(
            "%s: cannot import TRMClassifier "
            "(app.models.trm.tms_trm_classifier): %s — heuristic teacher",
            trm_type, e,
        )
        return None

    input_dim = int(ckpt["input_dim"])
    hidden_dims: Tuple[int, ...] = tuple(ckpt.get("hidden_dims", (128, 64)))

    try:
        model = TRMClassifier(input_dim=input_dim, hidden_dims=hidden_dims)
        model.load_state_dict(ckpt["model_state_dict"])
        model.eval()
    except Exception as e:
        logger.error(
            "%s: state_dict load failed: %s — heuristic teacher",
            trm_type, e,
        )
        return None

    logger.info(
        "%s: checkpoint loaded (input_dim=%d, val_acc=%.3f, active_actions=%s)",
        trm_type,
        input_dim,
        float(ckpt.get("best_val_acc", 0.0)),
        ckpt.get("active_actions", []),
    )

    return BcCheckpoint(
        model=model,
        input_dim=input_dim,
        feature_keys=list(ckpt["feature_keys"]),
        feature_means=list(ckpt["feature_means"]),
        feature_stds=list(ckpt["feature_stds"]),
        num_actions=int(ckpt.get("num_actions", 11)),
        best_val_acc=float(ckpt.get("best_val_acc", 0.0)),
        active_actions=list(ckpt.get("active_actions", [])),
        trm_type=str(ckpt.get("trm_type", trm_type)),
        raw=ckpt,
    )


def predict_action(
    checkpoint: BcCheckpoint,
    state: Any,
) -> Tuple[int, float, List[float]]:
    """Run a BC-trained checkpoint against a state dataclass.

    Reproduces the trainer's feature pipeline exactly:
      1. Read each `feature_keys[i]` off the state via getattr/dict
         lookup, with a `state_<name>` / `derived_<name>` prefix
         convention falling through.
      2. Coerce to float (bool → 0/1; None → 0.0; non-numeric → 0.0)
         so the model never sees NaN.
      3. Apply the trainer's per-column normalisation
         (x - mean) / std.
      4. Forward-pass through the MLP, softmax over logits.
      5. Return (argmax_action, max_prob, full_prob_vector).

    Args:
        checkpoint: a BcCheckpoint loaded via load_bc_checkpoint().
        state: the TRM's state dataclass instance (e.g.
            CapacityPromiseState). Field names should follow the
            trainer's `state_*` / `derived_*` convention; a `state_X`
            feature_key resolves against `state.X` first, then
            `state["X"]`, then `state.state_X`.

    Returns:
        (action_int, confidence_float, all_probs_list)
        action_int — argmax over NUM_ACTIONS
        confidence_float — max probability (0..1)
        all_probs_list — softmax probabilities for every class
    """
    if not TORCH_AVAILABLE:
        raise RuntimeError(
            "predict_action requires PyTorch but TORCH_AVAILABLE is False"
        )

    state_dict: Dict[str, Any] = {}
    if hasattr(state, "__dict__"):
        state_dict.update(vars(state))
    elif isinstance(state, dict):
        state_dict.update(state)

    # Build the feature vector in the exact order the trainer wrote it
    feats: List[float] = []
    for key in checkpoint.feature_keys:
        # The corpus uses prefixed keys: `state_<field>` / `derived_<field>`.
        # On a live state dataclass, the field name is the unprefixed form.
        # Try the bare name first, then the prefixed form.
        unprefixed = key
        for prefix in ("state_", "derived_"):
            if unprefixed.startswith(prefix):
                unprefixed = unprefixed[len(prefix):]
                break
        val = state_dict.get(unprefixed, state_dict.get(key))
        if val is None or val == "":
            feats.append(0.0)
        elif isinstance(val, bool):
            feats.append(1.0 if val else 0.0)
        elif isinstance(val, (int, float)):
            feats.append(float(val))
        else:
            try:
                feats.append(float(val))
            except (TypeError, ValueError):
                feats.append(0.0)

    # Apply trainer-time normalisation
    means = checkpoint.feature_means
    stds = checkpoint.feature_stds
    normed = [
        (feats[i] - means[i]) / (stds[i] if stds[i] > 1e-8 else 1.0)
        for i in range(len(feats))
    ]

    # Forward pass
    x = torch.tensor([normed], dtype=torch.float32)
    with torch.no_grad():
        logits = checkpoint.model(x)
        probs = torch.softmax(logits, dim=1)[0]
    probs_list = [float(p) for p in probs.tolist()]
    action = int(torch.argmax(probs).item())
    confidence = float(probs[action].item())

    return action, confidence, probs_list

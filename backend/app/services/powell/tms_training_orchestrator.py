"""TMS TRM training orchestrator — BC training from canonical corpus.

Phase 5 of the TMS TRM rework. Closes the pipeline: Phase 4 wrote
TMS-native rows into the canonical ``training_corpus`` table; this
orchestrator reads those rows, runs behavior-cloning training on
Core's :class:`TRMClassifier`, and writes a checkpoint in the format
:func:`app.services.powell.bc_checkpoint_loader.load_bc_checkpoint`
already loads at inference time.

Designed to run on **acer-nitro** (the documented build & train host)
where the GPU lives — the training loop here uses ``torch.device``
selection so it transparently uses CUDA when available and falls back
to CPU on msi-stealth for quick smoke. Training quality on a CPU run
is fine for verification; production retraining belongs on GPU.

Why not just invoke ``scripts/pretraining/train_tms_trms.py``: that
script reads JSONL corpus from disk. Our corpus generator writes to
the ``training_corpus`` DB table — the SCP convention. Bridging
the two via JSONL roundtrip is unnecessary indirection; the
orchestrator here reads the DB directly and produces checkpoints in
the exact format the script's checkpoint format already uses.

Usage::

    from app.services.powell.tms_training_orchestrator import (
        train_tms_trm, train_all_tms_trms,
    )

    # Single TRM
    summary = train_tms_trm(
        db, trm_type="load_build",
        config_id=207,  # or None for cross-config training
        epochs=30,
        device="cuda",  # acer-nitro
    )

    # All 10 TRMs end-to-end
    summaries = train_all_tms_trms(
        db, config_id=207, epochs=30, device="cuda",
    )
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.training_corpus import TrainingCorpusSample
from app.models.trm import MODEL_REGISTRY, TRMClassifier
from app.services.powell.trm_curriculum import TMS_TRM_CURRICULUM_REGISTRY


logger = logging.getLogger(__name__)


__all__ = [
    "DEFAULT_CHECKPOINT_DIR",
    "DEFAULT_EPOCHS",
    "DEFAULT_LR",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_VAL_SPLIT",
    "train_tms_trm",
    "train_all_tms_trms",
]


DEFAULT_CHECKPOINT_DIR = Path(
    "/opt/Autonomy-TMS/backend/training_data/checkpoints"
)
DEFAULT_EPOCHS = 30
DEFAULT_LR = 1e-3
DEFAULT_BATCH_SIZE = 256
DEFAULT_VAL_SPLIT = 0.2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def train_tms_trm(
    db: Session,
    trm_type: str,
    *,
    config_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    epochs: int = DEFAULT_EPOCHS,
    lr: float = DEFAULT_LR,
    batch_size: int = DEFAULT_BATCH_SIZE,
    val_split: float = DEFAULT_VAL_SPLIT,
    device: str = "cpu",
    checkpoint_dir: Optional[Path] = None,
    min_samples: int = 100,
) -> Dict[str, Any]:
    """Train a single TMS TRM from the canonical corpus.

    Args:
        db: SQLAlchemy session.
        trm_type: Canonical TMS TRM name (must be in :data:`MODEL_REGISTRY`).
        config_id: Optional — restrict training to corpus from one
            config. None ⇒ pool corpus across every config that has
            rows for this TRM (cross-config training).
        tenant_id: Optional tenant filter; usually omitted (corpus is
            tenant-scoped at the row level via config FK).
        epochs / lr / batch_size / val_split: Standard BC training
            hyperparameters. Defaults match the April 2026 BC training
            run that produced the current shipped checkpoints.
        device: ``"cpu"`` (default, msi-stealth) or ``"cuda"``
            (acer-nitro).
        checkpoint_dir: Override the default save location.
        min_samples: Refuse to train if the corpus query returns
            fewer than this many rows. Prevents training a model on a
            handful of curriculum samples that wouldn't beat the
            existing checkpoint.

    Returns:
        Dict with keys ``status``, ``trm_type``, ``num_samples``,
        ``best_val_acc``, ``epochs_trained``, ``checkpoint_path``,
        ``duration_seconds``.
    """
    if trm_type not in MODEL_REGISTRY:
        return {
            "status": "failed",
            "trm_type": trm_type,
            "error": (
                f"Unknown TMS TRM type: {trm_type}. "
                f"Choose from: {sorted(MODEL_REGISTRY.keys())}"
            ),
        }

    import torch

    model_cls, expected_input_dim = MODEL_REGISTRY[trm_type]
    curriculum_cls = TMS_TRM_CURRICULUM_REGISTRY[trm_type]
    active_actions = list(curriculum_cls.active_actions)

    t0 = time.time()

    # ----- 1. Pull corpus from training_corpus -----
    stmt = select(TrainingCorpusSample).where(
        TrainingCorpusSample.trm_type == trm_type,
        TrainingCorpusSample.layer == 1.0,
    )
    if config_id is not None:
        stmt = stmt.where(TrainingCorpusSample.config_id == config_id)
    if tenant_id is not None:
        stmt = stmt.where(TrainingCorpusSample.tenant_id == tenant_id)

    rows = db.execute(stmt).scalars().all()
    if len(rows) < min_samples:
        return {
            "status": "skipped",
            "trm_type": trm_type,
            "num_samples": len(rows),
            "error": (
                f"Only {len(rows)} corpus rows for {trm_type} "
                f"(min_samples={min_samples}). Run the corpus generator first."
            ),
        }

    # ----- 2. Marshal corpus → (X, y) tensors -----
    features: List[List[float]] = []
    labels: List[int] = []
    for row in rows:
        sample_data = row.sample_data or {}
        state = sample_data.get("state")
        action = sample_data.get("action")
        if state is None or action is None:
            continue
        if len(state) != expected_input_dim:
            logger.warning(
                "%s: row %s has state dim %d, expected %d — skipping",
                trm_type, row.id, len(state), expected_input_dim,
            )
            continue
        features.append(list(state))
        labels.append(int(action))

    if len(features) < min_samples:
        return {
            "status": "skipped",
            "trm_type": trm_type,
            "num_samples": len(features),
            "error": (
                f"Only {len(features)} valid rows after filtering for "
                f"{trm_type} (min_samples={min_samples})."
            ),
        }

    X = np.asarray(features, dtype=np.float32)
    y = np.asarray(labels, dtype=np.int64)

    # Per-feature mean/std for normalisation — persisted in the
    # checkpoint so inference can normalise input vectors identically.
    feature_means = X.mean(axis=0).tolist()
    feature_stds = (X.std(axis=0) + 1e-8).tolist()
    X_norm = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-8)

    # ----- 3. Train/val split -----
    rng = np.random.default_rng(seed=42)
    perm = rng.permutation(len(X_norm))
    val_n = max(1, int(len(X_norm) * val_split))
    val_idx, train_idx = perm[:val_n], perm[val_n:]
    X_train = torch.tensor(X_norm[train_idx], dtype=torch.float32, device=device)
    y_train = torch.tensor(y[train_idx], dtype=torch.long, device=device)
    X_val = torch.tensor(X_norm[val_idx], dtype=torch.float32, device=device)
    y_val = torch.tensor(y[val_idx], dtype=torch.long, device=device)

    # ----- 4. Construct + train -----
    model = model_cls(input_dim=expected_input_dim).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = torch.nn.CrossEntropyLoss()

    best_val_acc = 0.0
    best_state_dict = None
    best_epoch = 0
    epochs_trained = 0

    for epoch in range(1, epochs + 1):
        model.train()
        perm_t = torch.randperm(X_train.size(0), device=device)
        for i in range(0, X_train.size(0), batch_size):
            idx = perm_t[i : i + batch_size]
            xb, yb = X_train[idx], y_train[idx]
            logits = model(xb)
            loss = loss_fn(logits, yb)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        model.eval()
        with torch.no_grad():
            val_logits = model(X_val)
            val_preds = val_logits.argmax(dim=-1)
            val_acc = (val_preds == y_val).float().mean().item()

        epochs_trained = epoch
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_state_dict = {
                k: v.detach().cpu().clone() for k, v in model.state_dict().items()
            }
            best_epoch = epoch

    # Per-class accuracy keyed by action name — matches the format the
    # April 2026 training script saved and the BC loader reports.
    from app.models.trm import ACTION_INDEX_TO_NAME
    per_class_accuracy: Dict[str, float] = {}
    model.eval()
    with torch.no_grad():
        val_preds = model(X_val).argmax(dim=-1)
        for cls_idx in active_actions:
            mask = y_val == cls_idx
            if mask.sum().item() > 0:
                acc = (val_preds[mask] == cls_idx).float().mean().item()
                per_class_accuracy[ACTION_INDEX_TO_NAME[cls_idx]] = float(acc)

    # ----- 5. Save checkpoint -----
    save_dir = (checkpoint_dir or DEFAULT_CHECKPOINT_DIR)
    save_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = save_dir / f"trm_{trm_type}_bc_v1.pt"

    checkpoint = {
        "model_state_dict": best_state_dict if best_state_dict is not None else model.state_dict(),
        "input_dim": expected_input_dim,
        "hidden_dims": tuple(model.hidden_dims),
        "num_actions": model.num_actions,
        # Feature schema preserved so inference normalises input vectors
        # identically to training. The Phase 4 corpus rows don't tag
        # explicit feature_keys, so we synthesise placeholder names —
        # callers that need column-aware normalisation should re-train
        # from a corpus with explicit feature_keys.
        "feature_keys": [f"feature_{i}" for i in range(expected_input_dim)],
        "feature_means": feature_means,
        "feature_stds": feature_stds,
        "trm_type": trm_type,
        "best_val_acc": float(best_val_acc),
        "best_epoch": int(best_epoch),
        "epochs_trained": int(epochs_trained),
        "per_class_accuracy": per_class_accuracy,
        "active_actions": active_actions,
    }
    torch.save(checkpoint, str(ckpt_path))

    duration = time.time() - t0
    logger.info(
        "TMS BC training complete: %s val_acc=%.3f epochs=%d samples=%d "
        "checkpoint=%s in %.1fs",
        trm_type, best_val_acc, epochs_trained, len(features), ckpt_path, duration,
    )

    return {
        "status": "ok",
        "trm_type": trm_type,
        "num_samples": len(features),
        "best_val_acc": float(best_val_acc),
        "best_epoch": int(best_epoch),
        "epochs_trained": int(epochs_trained),
        "checkpoint_path": str(ckpt_path),
        "per_class_accuracy": per_class_accuracy,
        "duration_seconds": round(duration, 1),
    }


def train_all_tms_trms(
    db: Session,
    *,
    config_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    epochs: int = DEFAULT_EPOCHS,
    lr: float = DEFAULT_LR,
    batch_size: int = DEFAULT_BATCH_SIZE,
    val_split: float = DEFAULT_VAL_SPLIT,
    device: str = "cpu",
    checkpoint_dir: Optional[Path] = None,
    min_samples: int = 100,
) -> Dict[str, Dict[str, Any]]:
    """Train every TMS TRM in MODEL_REGISTRY end-to-end.

    Returns a dict ``{trm_type: train_tms_trm_summary}``. TRMs that
    don't have enough corpus rows are skipped with status='skipped';
    one TRM's failure doesn't abort the rest.
    """
    summaries: Dict[str, Dict[str, Any]] = {}
    for trm_type in sorted(MODEL_REGISTRY.keys()):
        try:
            summaries[trm_type] = train_tms_trm(
                db, trm_type=trm_type,
                config_id=config_id, tenant_id=tenant_id,
                epochs=epochs, lr=lr,
                batch_size=batch_size, val_split=val_split,
                device=device, checkpoint_dir=checkpoint_dir,
                min_samples=min_samples,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Training %s raised: %s — moving to next TRM", trm_type, exc
            )
            summaries[trm_type] = {
                "status": "failed",
                "trm_type": trm_type,
                "error": str(exc),
            }
    n_ok = sum(1 for s in summaries.values() if s.get("status") == "ok")
    n_skipped = sum(1 for s in summaries.values() if s.get("status") == "skipped")
    n_failed = sum(1 for s in summaries.values() if s.get("status") == "failed")
    logger.info(
        "TMS TRM training pipeline complete: %d ok / %d skipped / %d failed "
        "out of %d",
        n_ok, n_skipped, n_failed, len(summaries),
    )
    return summaries

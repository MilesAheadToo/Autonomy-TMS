"""TRM (Tiny Recursive Model) package — TMS native.

Canonical home for the TMS TRM classifier + per-TRM input dimensions.
All 10 TMS execution-domain TRMs share one neural architecture
(:class:`TRMClassifier` — MLP, hidden=(128, 64), 11-class output head)
and vary only by input dimension. Per-TRM state schemas live alongside
each TRM's service module under ``app/services/powell/<trm>_trm.py``;
the input dimensions captured here mirror what the BC training script
emitted into checkpoints, so the registry is a single source of truth
for "what input dim does this TRM take" at inference time.

Plane placement:
  - Demand-domain TRMs (``forecast_adjustment``, ``forecast_baseline``,
    eventually ``demand_sensing``) live in DP, not here. SCP-style
    TRM names (``atp_executor`` / ``po_creation`` / ``inventory_buffer``
    / etc.) deleted from this directory on 2026-05-20 — they were
    copy-pasted from SCP and never matched the actual TMS BC
    checkpoints on disk. See MIGRATION_REGISTER §3.78 lift series.

  - The single ``demand_sensing`` entry in the BC checkpoint set is a
    single-home violation tracked as Phase 3 of the TMS TRM rework; it
    will move to DP shortly. It is intentionally NOT in this registry.

Inference path: TMS plane services (``app/services/powell/<trm>_trm.py``)
load their BC checkpoint via
:func:`app.services.powell.bc_checkpoint_loader.load_bc_checkpoint`,
which constructs :class:`TRMClassifier` with the registered ``input_dim``
and loads the saved weights.
"""
from __future__ import annotations

from .tms_trm_classifier import (
    ACTION_INDEX_TO_NAME,
    ACTION_NAMES,
    NUM_ACTIONS,
    TRMClassifier,
)


# ---------------------------------------------------------------------------
# Per-TRM input dimensions
# ---------------------------------------------------------------------------
# Captured from the April 2026 BC checkpoint metadata. Each constant is the
# ``input_dim`` field of the corresponding ``trm_<name>_bc_v1.pt`` file. The
# registry below uses these so an inference-time loader can construct a
# correctly-shaped ``TRMClassifier`` even when the checkpoint metadata is
# unavailable. Whenever a TRM's state schema changes the constant here MUST
# be bumped in lockstep with re-training, or BC checkpoints stop loading.

BROKER_ROUTING_STATE_DIM       = 19
CAPACITY_BUFFER_STATE_DIM      = 14
CAPACITY_PROMISE_STATE_DIM     = 17
DOCK_SCHEDULING_STATE_DIM      = 19
EQUIPMENT_REPOSITION_STATE_DIM = 15
EXCEPTION_MANAGEMENT_STATE_DIM = 28
FREIGHT_PROCUREMENT_STATE_DIM  = 23
INTERMODAL_TRANSFER_STATE_DIM  = 21
LOAD_BUILD_STATE_DIM           = 24
SHIPMENT_TRACKING_STATE_DIM    = 21


# ---------------------------------------------------------------------------
# MODEL_REGISTRY — TMS-native TRMs only
# ---------------------------------------------------------------------------
# Keys are the canonical TMS TRM names (matching the BC checkpoint
# filenames at ``training_data/checkpoints/trm_<name>_bc_v1.pt``).
# Values are ``(model_class, input_dim)`` pairs — same shape SCP's
# registry uses, so cross-plane training tooling treats the two
# registries uniformly.

MODEL_REGISTRY: dict[str, tuple[type, int]] = {
    "broker_routing":       (TRMClassifier, BROKER_ROUTING_STATE_DIM),
    "capacity_buffer":      (TRMClassifier, CAPACITY_BUFFER_STATE_DIM),
    "capacity_promise":     (TRMClassifier, CAPACITY_PROMISE_STATE_DIM),
    "dock_scheduling":      (TRMClassifier, DOCK_SCHEDULING_STATE_DIM),
    "equipment_reposition": (TRMClassifier, EQUIPMENT_REPOSITION_STATE_DIM),
    "exception_management": (TRMClassifier, EXCEPTION_MANAGEMENT_STATE_DIM),
    "freight_procurement":  (TRMClassifier, FREIGHT_PROCUREMENT_STATE_DIM),
    "intermodal_transfer":  (TRMClassifier, INTERMODAL_TRANSFER_STATE_DIM),
    "load_build":           (TRMClassifier, LOAD_BUILD_STATE_DIM),
    "shipment_tracking":    (TRMClassifier, SHIPMENT_TRACKING_STATE_DIM),
}


from app.models.metrics_hierarchy import (  # noqa: E402
    GARTNER_METRICS,
    MetricConfig,
    POWELL_LAYER_METRICS,
    TRM_METRIC_MAPPING,
    get_metric_config,
)


def load_trm_checkpoint(
    trm_type: str,
    checkpoint_path: str,
    device: str = "cpu",
):
    """Load a TMS TRM BC checkpoint by canonical name.

    Thin wrapper around the registry — constructs a
    :class:`TRMClassifier` with the registered ``input_dim``, loads
    the state dict, switches to eval mode. Callers that want richer
    metadata (per-class accuracy, feature normalisation stats,
    ``active_actions``) should use
    :func:`app.services.powell.bc_checkpoint_loader.load_bc_checkpoint`
    which returns a full ``BcCheckpoint`` dataclass.

    Args:
        trm_type: Canonical TMS TRM name (e.g. ``"load_build"``).
        checkpoint_path: Path to a ``.pt`` file in the format produced
            by ``backend/scripts/pretraining/train_tms_trms.py``.
        device: ``"cpu"`` or ``"cuda"``.

    Raises:
        ValueError: ``trm_type`` not in :data:`MODEL_REGISTRY`.

    Returns:
        The :class:`TRMClassifier` instance in eval mode on the
        specified device.
    """
    import torch

    if trm_type not in MODEL_REGISTRY:
        raise ValueError(
            f"Unknown TMS TRM type: {trm_type}. "
            f"Choose from: {sorted(MODEL_REGISTRY.keys())}"
        )

    model_cls, input_dim = MODEL_REGISTRY[trm_type]
    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
    hidden_dims = tuple(ckpt.get("hidden_dims", (128, 64)))
    # Prefer the checkpoint's own input_dim when present — handles the
    # edge case where the registry constant has drifted but the checkpoint
    # is still load-compatible.
    ckpt_input_dim = int(ckpt.get("input_dim", input_dim))
    model = model_cls(input_dim=ckpt_input_dim, hidden_dims=hidden_dims)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    model.to(device)
    return model


__all__ = [
    "TRMClassifier",
    "ACTION_NAMES",
    "ACTION_INDEX_TO_NAME",
    "NUM_ACTIONS",
    # Per-TRM state-dim constants
    "BROKER_ROUTING_STATE_DIM",
    "CAPACITY_BUFFER_STATE_DIM",
    "CAPACITY_PROMISE_STATE_DIM",
    "DOCK_SCHEDULING_STATE_DIM",
    "EQUIPMENT_REPOSITION_STATE_DIM",
    "EXCEPTION_MANAGEMENT_STATE_DIM",
    "FREIGHT_PROCUREMENT_STATE_DIM",
    "INTERMODAL_TRANSFER_STATE_DIM",
    "LOAD_BUILD_STATE_DIM",
    "SHIPMENT_TRACKING_STATE_DIM",
    # Registry + loader
    "MODEL_REGISTRY",
    "load_trm_checkpoint",
    # Gartner SCOR metric hierarchy
    "TRM_METRIC_MAPPING",
    "GARTNER_METRICS",
    "POWELL_LAYER_METRICS",
    "MetricConfig",
    "get_metric_config",
]

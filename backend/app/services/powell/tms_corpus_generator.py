"""TMS training-corpus generator — curriculum samples → ``training_corpus``.

Phase 4 of the TMS TRM rework. Bridges the Phase 2 curricula
(``TMS_TRM_CURRICULUM_REGISTRY``) into the canonical
``training_corpus`` table so the unified training pipeline (Phase 5)
has TMS-native rows to train against. Until this lands the
``training_corpus`` table held zero TMS-native rows — only SCP-flavored
``trm_type`` labels populated it.

What this generates (Phase 4a, curriculum-derived):
  - Per-TRM Layer-1 samples drawn from the TMS curricula at all 3
    phases, weighted by :data:`PHASE_SAMPLE_WEIGHTS` (20/30/50 split).
  - Each row carries the canonical TMS TRM name in ``trm_type``, the
    full state vector + action + reward in ``sample_data``, and
    ``origin='curriculum'`` so downstream consumers can distinguish
    curriculum samples from real-outcome samples.

What this does NOT generate (Phase 4b, deferred):
  - Real-event corpus rows from TMS synthetic-tenant histories
    (shipments, load assignments, tender outcomes). That requires
    TMS-flavored history generators in Core's
    ``synthetic_tenants/*/history_generator.py`` modules, which is a
    separate workstream.

Cross-plane: TMS-only. SCP populates its own corpus via
``simulation_calibration_service``; DP populates its own via the
forecast pipeline. Each plane owns its corpus-generation pipeline;
the ``training_corpus`` table itself is the shared substrate.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from azirella_data_model.ml import PHASE_SAMPLE_WEIGHTS, SCConfigData

from app.models.training_corpus import TrainingCorpusSample
from app.services.powell.trm_curriculum import (
    CURRICULUM_REGISTRY,
    TMS_TRM_CURRICULUM_REGISTRY,
)


logger = logging.getLogger(__name__)


__all__ = [
    "generate_tms_curriculum_corpus",
    "DEFAULT_SAMPLES_PER_TRM",
]


# Default total sample count per TRM. With the 20/30/50 phase split
# this gives 2K phase-1, 3K phase-2, 5K phase-3 samples per TRM ≈
# the order of magnitude SCP's per-TRM corpus shows in the live DB.
DEFAULT_SAMPLES_PER_TRM = 10_000


def generate_tms_curriculum_corpus(
    db: Session,
    tenant_id: int,
    config_id: int,
    *,
    num_samples_per_trm: int = DEFAULT_SAMPLES_PER_TRM,
    trm_types: Optional[list[str]] = None,
    scenario_id: Optional[str] = None,
    seed: Optional[int] = None,
    commit_chunk_size: int = 1_000,
) -> Dict[str, int]:
    """Generate curriculum-derived corpus rows for a (tenant, config).

    Args:
        db: SQLAlchemy session bound to ``training_corpus``.
        tenant_id: Tenant that owns the corpus rows.
        config_id: Config the rows are scoped to (FK target).
        num_samples_per_trm: Total samples generated per TRM across
            the 3 phases (split 20/30/50 per ``PHASE_SAMPLE_WEIGHTS``).
        trm_types: Optional subset of TRM names to generate for.
            None ⇒ all 10 TMS-native TRMs.
        scenario_id: Optional scenario tag. None ⇒ generate a fresh
            UUID per call so re-runs are distinguishable in the corpus.
        seed: Optional RNG seed for deterministic reproduction —
            forwarded to each curriculum's constructor.
        commit_chunk_size: Number of rows per DB commit. Smaller =
            more frequent flushes (better memory under pressure);
            larger = fewer txns.

    Returns:
        Dict ``{trm_type: rows_inserted}``. Sums to
        ``len(targets) * num_samples_per_trm``.
    """
    if scenario_id is None:
        scenario_id = f"tms-curriculum-{uuid.uuid4().hex[:12]}"

    target_trms = (
        trm_types if trm_types is not None
        else sorted(TMS_TRM_CURRICULUM_REGISTRY.keys())
    )
    unknown = set(target_trms) - set(TMS_TRM_CURRICULUM_REGISTRY)
    if unknown:
        raise ValueError(
            f"Unknown TMS TRM types: {sorted(unknown)}. "
            f"Choose from: {sorted(TMS_TRM_CURRICULUM_REGISTRY)}"
        )

    sc_config = SCConfigData()
    inserted: Dict[str, int] = {trm: 0 for trm in target_trms}
    batch: list[TrainingCorpusSample] = []

    for trm_type in target_trms:
        cls = CURRICULUM_REGISTRY[trm_type]
        curriculum = cls(sc_config, seed=seed)

        for phase in (1, 2, 3):
            phase_count = max(
                1, int(round(num_samples_per_trm * PHASE_SAMPLE_WEIGHTS[phase])),
            )
            data = curriculum.generate(phase=phase, num_samples=phase_count)

            for idx in range(phase_count):
                state_vec = data.state_vectors[idx]
                action = int(data.action_discrete[idx])
                reward = float(data.rewards[idx])
                sample = TrainingCorpusSample(
                    tenant_id=tenant_id,
                    config_id=config_id,
                    layer=1.0,
                    scenario_id=scenario_id,
                    origin="curriculum",
                    trm_type=trm_type,
                    site_id=None,
                    product_id=None,
                    period=None,
                    time_window=None,
                    sample_data={
                        "phase": phase,
                        "state": state_vec.tolist(),
                        "action": action,
                        "reward": reward,
                        "active_actions": list(curriculum.active_actions),
                    },
                    reward=reward,
                    weight=1.0,
                )
                batch.append(sample)
                inserted[trm_type] += 1

                if len(batch) >= commit_chunk_size:
                    db.bulk_save_objects(batch)
                    db.commit()
                    batch = []

    if batch:
        db.bulk_save_objects(batch)
        db.commit()

    total = sum(inserted.values())
    logger.info(
        "TMS curriculum corpus: %d rows across %d TRMs for "
        "tenant_id=%s config_id=%s scenario_id=%s",
        total, len(inserted), tenant_id, config_id, scenario_id,
    )
    return inserted

"""GraphSAGE Movement Planner — §3.38 Phase 3 scaffold.

This module ships the **interface scaffold** for the Phase 3
GraphSAGE-based Movement Planner specified in
``docs/TMS_DECISION_HIERARCHY.md`` §4.2:

    *Movement Planner GraphSAGE — analog of SCP's supply_planning_tgnn
    but with transport semantics: nodes = lanes + hubs, edges = mode
    alternatives, features = rate-card + distance + transit time.*

**The actual model is not trained here.** GraphSAGE training is a
multi-day workstream that requires:

1. **Training data**: tenant historical (lane, period, mode-split,
   carrier-assignment, observed-cost) tuples joined with rate-card
   snapshots. Data prep is in ``backend/scripts/pretraining/``.
2. **Model architecture**: PyTorch GNN with 2-3 GraphSAGE conv layers,
   mean-aggregator, sum-readout for plan-level cost prediction; or
   GATv2 for attention-weighted edge importance.
3. **Training loop**: minibatch sampling per (tenant, period); MSE
   loss on observed-cost vs. predicted; teacher-forced rollouts on
   the digital twin for policy-gradient finetune.
4. **Compute budget**: GPU training (~hours per tenant), checkpointed
   per ``training_run`` (Core's `powell_training_config` substrate).
5. **Evaluation**: held-out-period MAPE on cost prediction, mode-split
   accuracy, downstream plan utilisation.
6. **Deployment**: `inference_service` wired into
   ``MovementPlannerService.plan_movement(model_id=...)``.

This scaffold defines the **public interface** that the trained
model will plug into, so:

- The Phase 2A heuristic Movement Planner has a stable upgrade path
- Tests can exercise the scaffold's contract without GPU dependencies
- Phase 3 ML work is genuinely a separate workstream (model training
  + evaluation + deployment), not "code waiting to be written"

## Phase 3 work plan

1. **§3.41 Phase 3.1 — training data ETL**: build
   ``MovementPlannerTrainingDataExtractor`` that walks
   ``transportation_plan_item`` history joined with ``freight_charge``
   actuals + ``rate_card`` snapshots; emits training tuples.
2. **§3.41 Phase 3.2 — model architecture**: write
   ``GraphSAGEMovementPlannerModel`` (PyTorch). 2 conv layers, mean
   aggregator, MLP head per (mode, equipment) pair.
3. **§3.41 Phase 3.3 — training pipeline**: integrate with
   ``trm_trainer.py`` patterns; checkpoint per training run.
4. **§3.41 Phase 3.4 — inference service**: wire into MovementPlanner
   under a feature flag; A/B against Phase 2A heuristic.
5. **§3.41 Phase 3.5 — production rollout**: per-tenant calibration,
   monitoring (forecast drift, plan-utilisation regression), fall-back
   to Phase 2A on infrastructure failure.

Per CLAUDE.md the model code lives in ``packages/data-model/.../trm/``
(Core, with PyTorch-optional dependency) when its training matures
to cross-product utility; for Phase 3.1-3.4 it lives in this TMS
repo since the TRM is plane-specific.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class GraphSAGEPredictionInput:
    """Input batch for a single inference call.

    The Phase 3 GraphSAGE model takes a transport-graph snapshot
    (nodes = lanes + hubs; edges = mode alternatives) plus the
    forecast volumes per (lane, period) and returns recommended
    mode + equipment + carrier per item.
    """

    tenant_id: int
    config_id: int
    period_start: date
    period_days: int
    """Forecast period scope."""

    lane_volume_forecasts: List[Dict[str, Any]]
    """Per-lane forecast rows from §3.37 LaneVolumePlan, with
    (lane_id, mode, equipment_type, forecast_loads_p50, ...).
    Phase 3 will preprocess these into node features."""

    available_carriers: List[Dict[str, Any]]
    """List of (carrier_id, contract_id, rate_card_id, equipment_type,
    base_rate, capacity_remaining) tuples."""

    transit_time_distribution: Optional[Dict[int, Dict[str, float]]] = None
    """Per-lane transit-time distribution (p10/p50/p90 hours). Phase 3
    will read this from `LaneProfile` historical data."""


@dataclass(frozen=True)
class GraphSAGEPredictionOutput:
    """One prediction per plan item.

    The Phase 3 model emits, per item: a recommended (carrier_id,
    rate_id) pair and a confidence score; the Phase 2A heuristic gets
    a structurally compatible result so it can be the fallback when
    the model's confidence is low.
    """

    item_id: int
    carrier_id: Optional[int]
    rate_id: Optional[int]
    estimated_cost: Optional[float]
    confidence: float
    """Model's confidence in the assignment (0-1). When ``< threshold``
    the planner falls back to the Phase 2A heuristic."""

    rationale: Dict[str, Any] = field(default_factory=dict)
    """Model-emitted explanations: top-K node attentions, alternative
    carriers considered, etc. Useful for AIIO override-with-reasoning."""


class GraphSAGEMovementPlannerModel(ABC):
    """Abstract Phase 3 model interface.

    Real implementations live under ``packages/data-model/.../trm/``
    (PyTorch GNN; Phase 3.2 deliverable). Phase 2A consumers can
    instantiate ``NotYetImplementedModel`` to exercise the scaffold's
    contract without a trained model.
    """

    @abstractmethod
    def fit(self, training_data: List[Dict[str, Any]]) -> None:
        """Train the model on historical (lane, period, observed-cost)
        tuples. Phase 3.3 deliverable."""

    @abstractmethod
    def predict(
        self, inputs: GraphSAGEPredictionInput,
    ) -> List[GraphSAGEPredictionOutput]:
        """Score per-item assignment recommendations. Phase 3.4
        deliverable."""

    @abstractmethod
    def model_version(self) -> str:
        """Unique version identifier (typically a checkpoint hash)
        persisted on `TransportationPlan.optimization_metadata` so
        consumers can audit which model version produced a plan."""


class NotYetImplementedModel(GraphSAGEMovementPlannerModel):
    """Phase 3 scaffold sentinel.

    Raises ``NotImplementedError`` on every method. Used in tests to
    verify the scaffold's contract. Phase 3.2 will replace this with
    the real PyTorch GNN.
    """

    def fit(self, training_data: List[Dict[str, Any]]) -> None:
        raise NotImplementedError(
            "GraphSAGE Phase 3 model — training not yet implemented. "
            "See MIGRATION_REGISTER.md §3.38 'Phase 3 work plan'."
        )

    def predict(
        self, inputs: GraphSAGEPredictionInput,
    ) -> List[GraphSAGEPredictionOutput]:
        raise NotImplementedError(
            "GraphSAGE Phase 3 model — inference not yet implemented. "
            "Use MovementPlannerService Phase 2A heuristic until §3.41 "
            "Phase 3.4 deliverable lands."
        )

    def model_version(self) -> str:
        return "graphsage_not_yet_implemented"


__all__ = [
    "GraphSAGEMovementPlannerModel",
    "GraphSAGEPredictionInput",
    "GraphSAGEPredictionOutput",
    "NotYetImplementedModel",
]

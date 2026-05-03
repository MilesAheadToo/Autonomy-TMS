"""§3.41 Phase 3.3 — Training pipeline orchestrator for the GraphSAGE
Movement Planner.

Wires the Phase 3.1 :class:`MovementPlannerTrainingDataExtractor`
output into the Phase 3.2 :class:`TorchGraphSAGEMovementPlanner.fit`
training method.

Responsibilities:

1. **Extract**: pull historical training examples for a tenant and
   period window via the ETL.
2. **Transform**: group examples by plan, build per-plan node + edge
   tensors, and pair them with observed-cost labels.
3. **Train / validate**: split into train + held-out validation, run
   ``fit()``, log loss curves.
4. **Checkpoint**: stash the trained model + version under a
   ``training_run_id`` so consumers can audit which model produced
   which plan.

Phase 3.3 ships the orchestrator's **shape**. Production training
(Phase 3.5) will run on GPU with proper minibatching, hyperparameter
search, and held-out test-set evaluation. Phase 3.3 does single-pass
SGD on the full extracted dataset — useful for smoke tests and
small-tenant prototyping but not production-grade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.powell.graphsage_movement_planner import (
    GraphSAGEPredictionInput,
    TorchGraphSAGEMovementPlanner,
)
from app.services.powell.movement_planner_training_data import (
    MovementPlannerTrainingDataExtractor,
    MovementPlannerTrainingExample,
)


@dataclass(frozen=True)
class TrainingRunResult:
    """Per-run summary."""

    training_run_id: str
    """Unique identifier for this run (timestamp + tenant_id)."""

    model_version: str
    """The trained model's version stamp (Phase 3.2 emits a version
    based on training-step count + final loss; Phase 3.5 will use a
    proper checkpoint hash)."""

    examples_extracted: int
    """Total training examples pulled from the ETL."""

    examples_used: int
    """Examples that survived the training-eligibility filter (have
    observed-cost OR planner-estimated-cost label)."""

    train_loss_history: List[float] = field(default_factory=list)
    val_loss: Optional[float] = None
    """Held-out validation loss (Phase 3.3 computes a basic 80/20
    split; Phase 3.5 will use proper time-based splits)."""

    extraction_period_start: Optional[date] = None
    extraction_period_end: Optional[date] = None


class MovementPlannerTrainer:
    """Orchestrates a training run for the GraphSAGE Movement Planner.

    Usage::

        trainer = MovementPlannerTrainer(db)
        result = trainer.train(
            tenant_id=42,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 5, 1),
        )
        print(f"trained model version: {result.model_version}")
        # Hand the trained model off to inference (Phase 3.4).

    Phase 3.3 produces a single in-memory model. Phase 3.5 will
    persist to a checkpoint store and attach the model to a
    ``training_run`` row in Core's `powell_training_config` substrate.
    """

    DEFAULT_VAL_SPLIT = 0.2

    def __init__(self, db: Session) -> None:
        self.db = db

    def train(
        self,
        *,
        tenant_id: int,
        period_start: Optional[date] = None,
        period_end: Optional[date] = None,
        only_completed: bool = False,
        learning_rate: float = TorchGraphSAGEMovementPlanner.DEFAULT_LR,
        val_split: float = DEFAULT_VAL_SPLIT,
        model_kwargs: Optional[Dict[str, Any]] = None,
    ) -> TrainingRunResult:
        """Run an end-to-end training pass.

        - Extracts examples for the tenant + period window.
        - Splits into train + validation (random 80/20 by default;
          Phase 3.5 switches to time-based splits).
        - Builds per-plan graph tensors via
          :meth:`_examples_to_training_batches`.
        - Runs ``model.fit(training_batches)``.
        - Computes held-out validation MSE.
        - Returns a :class:`TrainingRunResult` with the trained model
          and run metadata.

        Caller commits any DB state. Phase 3.3 does not write to DB —
        production training (Phase 3.5) will persist a
        ``training_run`` row.
        """
        extractor = MovementPlannerTrainingDataExtractor(self.db)
        all_examples: List[MovementPlannerTrainingExample] = list(
            extractor.extract(
                tenant_id=tenant_id,
                period_start_min=period_start,
                period_start_max=period_end,
                only_completed=only_completed,
            )
        )
        examples_extracted = len(all_examples)

        # Filter: need at least one cost label to train against.
        eligible = [
            ex for ex in all_examples
            if ex.observed_total_cost is not None
            or ex.action_estimated_cost is not None
        ]
        examples_used = len(eligible)

        # Train / val split.
        if val_split > 0 and examples_used >= 10:
            split_idx = int(examples_used * (1 - val_split))
            train_examples = eligible[:split_idx]
            val_examples = eligible[split_idx:]
        else:
            train_examples = eligible
            val_examples = []

        # Build the model.
        kwargs = dict(model_kwargs or {})
        kwargs.setdefault("learning_rate", learning_rate)
        model = TorchGraphSAGEMovementPlanner(**kwargs)

        # Convert examples to per-plan training batches. Call
        # ``model.fit`` unconditionally — when the batch list is
        # empty the model itself short-circuits and sets its
        # ``model_version`` to ``"graphsage_no_training_data"``,
        # which is the state the no-examples result should reflect.
        train_batches = self._examples_to_training_batches(train_examples)
        model.fit(train_batches)

        # Held-out val loss (basic; Phase 3.5 adds proper metrics).
        val_loss: Optional[float] = None
        if val_examples and model._model is not None:
            val_loss = self._compute_val_loss(model, val_examples)

        run_id = (
            f"trainer_{tenant_id}_"
            f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
        )

        return TrainingRunResult(
            training_run_id=run_id,
            model_version=model.model_version(),
            examples_extracted=examples_extracted,
            examples_used=examples_used,
            train_loss_history=model._loss_history,
            val_loss=val_loss,
            extraction_period_start=period_start,
            extraction_period_end=period_end,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _examples_to_training_batches(
        self, examples: List[MovementPlannerTrainingExample],
    ) -> List[Dict[str, Any]]:
        """Group examples by ``plan_id`` and build one training-batch
        dict per plan.

        Each dict has the shape ``TorchGraphSAGEMovementPlanner.fit()``
        expects: ``node_features`` / ``edge_index`` / ``edge_attr`` /
        ``item_carrier_pairs`` / ``observed_costs``.

        Phase 3.3 simplification: each plan = one batch (single forward
        pass per plan during fit). Production training (Phase 3.5) uses
        proper minibatch sampling across plans.
        """
        from collections import defaultdict

        try:
            import torch
        except ImportError:
            return []

        by_plan: Dict[int, List[MovementPlannerTrainingExample]] = defaultdict(list)
        for ex in examples:
            by_plan[ex.plan_id].append(ex)

        batches: List[Dict[str, Any]] = []
        for plan_id, plan_examples in by_plan.items():
            # Distinct item-nodes + carrier-nodes for this plan.
            item_ids = sorted({ex.item_id for ex in plan_examples})
            carrier_ids = sorted({ex.action_carrier_id for ex in plan_examples})
            n_items = len(item_ids)
            n_carriers = len(carrier_ids)
            if n_items == 0 or n_carriers == 0:
                continue

            item_idx_by_id = {iid: i for i, iid in enumerate(item_ids)}
            carrier_idx_by_id = {
                cid: n_items + j for j, cid in enumerate(carrier_ids)
            }

            # Node features (4-dim, matches inference shape):
            # (loads_p50_proxy, mode_idx, equipment_idx, is_item).
            node_features = torch.zeros((n_items + n_carriers, 4))
            for ex in plan_examples:
                idx = item_idx_by_id[ex.item_id]
                node_features[idx, 0] = 1.0  # placeholder for per-item loads
                node_features[idx, 1] = TorchGraphSAGEMovementPlanner._mode_to_idx(ex.mode)
                node_features[idx, 2] = TorchGraphSAGEMovementPlanner._equipment_to_idx(
                    ex.equipment_type,
                )
                node_features[idx, 3] = 1.0  # is_item

            # Edges: (item, carrier) pair the example labels with.
            # Plus reverse edges for SAGEConv symmetry.
            n_pairs = len(plan_examples)
            edge_index = torch.zeros((2, n_pairs * 2), dtype=torch.long)
            edge_attr = torch.zeros((n_pairs * 2, 3))
            item_carrier_pairs = []
            observed_costs = []
            for k, ex in enumerate(plan_examples):
                i = item_idx_by_id[ex.item_id]
                c = carrier_idx_by_id[ex.action_carrier_id]
                edge_index[0, 2 * k] = i
                edge_index[1, 2 * k] = c
                edge_index[0, 2 * k + 1] = c
                edge_index[1, 2 * k + 1] = i
                # Edge feature: (base_rate, distance_miles, _pad)
                rc_meta = ex.rate_card_metadata or {}
                edge_attr[2 * k, 0] = float(rc_meta.get("base_rate") or 0)
                edge_attr[2 * k, 1] = ex.distance_miles or 0.0
                edge_attr[2 * k + 1, 0] = float(rc_meta.get("base_rate") or 0)
                edge_attr[2 * k + 1, 1] = ex.distance_miles or 0.0
                item_carrier_pairs.append((i, c))
                # Use observed cost if present, else the planner's
                # estimate as a fallback label.
                cost_label = (
                    ex.observed_total_cost
                    if ex.observed_total_cost is not None
                    else (ex.action_estimated_cost or 0.0)
                )
                observed_costs.append(float(cost_label))

            batches.append({
                "node_features": node_features,
                "edge_index": edge_index,
                "edge_attr": edge_attr,
                "item_carrier_pairs": item_carrier_pairs,
                "observed_costs": observed_costs,
            })
        return batches

    def _compute_val_loss(
        self,
        model: TorchGraphSAGEMovementPlanner,
        val_examples: List[MovementPlannerTrainingExample],
    ) -> float:
        """Held-out MSE. Phase 3.5 will replace this with proper
        held-out metrics (MAPE, mode-split accuracy, plan-utilisation
        regression)."""
        try:
            import torch
        except ImportError:
            return float("nan")

        batches = self._examples_to_training_batches(val_examples)
        if not batches:
            return float("nan")

        total_loss = 0.0
        n_batches = 0
        device = next(model._model.parameters()).device
        model._model.eval()
        with torch.no_grad():
            for batch in batches:
                x = batch["node_features"].to(device)
                edge_index = batch["edge_index"].to(device)
                edge_attr = batch["edge_attr"].to(device)
                pairs = batch["item_carrier_pairs"]
                item_idx = torch.tensor(
                    [p[0] for p in pairs], dtype=torch.long, device=device,
                )
                carrier_idx = torch.tensor(
                    [p[1] for p in pairs], dtype=torch.long, device=device,
                )
                observed = torch.tensor(
                    batch["observed_costs"], dtype=torch.float32, device=device,
                )
                pred = model._model(
                    x, edge_index, edge_attr, item_idx, carrier_idx,
                ).squeeze(-1)
                total_loss += float(
                    torch.nn.functional.mse_loss(pred, observed).item()
                )
                n_batches += 1
        return total_loss / n_batches if n_batches > 0 else float("nan")


__all__ = [
    "MovementPlannerTrainer",
    "TrainingRunResult",
]

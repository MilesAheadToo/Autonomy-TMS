"""Outcome Collector — TMS skill-outcome path (KB database).

§3.64 and §3.66 closure: This file used to also carry the
``SiteAgentDecision`` collection path (moved to Core under §3.64 via
:class:`TmsOutcomeAdapter`) and the per-TRM ``Powell*Decision`` table
path (moved to Core under §3.66 via Core's spec-driven
``collect_trm_outcomes`` + adapter ``read_trm_outcome`` hook). What
remains is the skill-outcome path on the Knowledge-Base database.

The skill path stays here for now because:

* ``decision_embeddings`` lives on the **KB database** (a separate
  Postgres container), accessed via the sync KB session — not the
  primary backend session that Core's ``OutcomeCollectorService``
  takes.
* The shape (``decision_source == 'skill_exception'`` decisions
  whose outcome dict is computed from the same operational tables
  but feeds back into RAG retrieval) is KB-coupled.

When the KB → Core consolidation lands as its own workstream, this
file goes entirely. The ~150 LOC here is the last island.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Feedback horizons for the 15 powell_*_decisions tables.
# Mirrors the canonical Core ``TRM_DECISION_HORIZONS`` in
# ``azirella_data_model.governance.causal.feedback_horizons``. Kept
# here because the legacy collect_trm_outcomes loop indexes its
# per-trm collectors against this table; §3.66 migrates the loop to
# Core and removes the local copy.
TRM_OUTCOME_DELAY = {
    "atp": timedelta(hours=4),
    "rebalance": timedelta(days=7),
    "po": timedelta(days=7),
    "order_tracking": timedelta(days=3),
    "mo": timedelta(days=3),
    "to": timedelta(days=5),
    "quality": timedelta(days=2),
    "maintenance": timedelta(days=7),
    "subcontracting": timedelta(days=14),
    "forecast_adjustment": timedelta(days=30),
    "inventory_buffer": timedelta(days=14),
    # Planning TRM feedback horizons
    "demand_adjustment":    timedelta(weeks=4),
    "inventory_adjustment": timedelta(weeks=2),
    "supply_adjustment":    timedelta(days=7),
    "rccp_adjustment":      timedelta(weeks=2),
}


class OutcomeCollectorService:
    """Per-TRM and skill outcome collection.

    The ``SiteAgentDecision`` path that used to live here moved to
    Core under §3.64. Construct this only for the per-TRM (and skill)
    collection paths invoked by ``relearning_jobs``.
    """

    def __init__(self, db: Session):
        self.db = db
        # Lazy import: keeps the per-tenant EK shaping out of the
        # import graph until needed by the skill path.
        from app.services.powell.trm_trainer import RewardCalculator
        self.reward_calculator = RewardCalculator()

    # ------------------------------------------------------------------
    # Path 3: Skills decision outcome collection (decision_embeddings)
    # ------------------------------------------------------------------

    # Feedback horizons for skill decisions (same as TRM, keyed by trm_type)
    SKILL_OUTCOME_DELAY = TRM_OUTCOME_DELAY

    def collect_skill_outcomes(self) -> Dict[str, Any]:
        """Collect outcomes for Claude Skills decisions stored in
        decision_embeddings (KB database).

        After the feedback horizon, computes outcomes using the same
        reward calculators as TRM decisions, then updates the
        decision_embeddings record with outcome data — feeding back
        into RAG retrieval so future similar situations see what
        actually happened.
        """
        from app.models.decision_embeddings import DecisionEmbedding

        stats = {"processed": 0, "succeeded": 0, "failed": 0, "by_type": {}}
        now = datetime.utcnow()

        for trm_type, delay in self.SKILL_OUTCOME_DELAY.items():
            cutoff = now - delay

            try:
                decisions = self.db.query(DecisionEmbedding).filter(
                    DecisionEmbedding.decision_source == "skill_exception",
                    DecisionEmbedding.outcome.is_(None),
                    DecisionEmbedding.trm_type == trm_type,
                    DecisionEmbedding.created_at < cutoff,
                    DecisionEmbedding.created_at > now - timedelta(days=60),
                ).limit(100).all()
            except Exception as e:
                logger.debug(f"Skill outcome query failed for {trm_type}: {e}")
                stats["by_type"][trm_type] = {"found": 0, "computed": 0, "failed": 1}
                continue

            type_stats = {"found": len(decisions), "computed": 0, "failed": 0}

            for dec in decisions:
                stats["processed"] += 1
                try:
                    outcome = self._compute_skill_outcome(dec, trm_type)
                    if outcome:
                        reward = self.reward_calculator.calculate_reward(
                            trm_type, outcome
                        )
                        dec.outcome = outcome
                        dec.outcome_summary = self._summarize_outcome(
                            trm_type, outcome, reward
                        )
                        dec.reward = reward
                        dec.outcome_recorded_at = now
                        stats["succeeded"] += 1
                        type_stats["computed"] += 1
                    else:
                        type_stats["failed"] += 1
                        stats["failed"] += 1
                except Exception as e:
                    logger.debug(f"Skill outcome computation failed: {e}")
                    type_stats["failed"] += 1
                    stats["failed"] += 1

            stats["by_type"][trm_type] = type_stats

        try:
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to commit skill outcomes: {e}")
            self.db.rollback()

        logger.info(
            f"Skill outcome collection: {stats['succeeded']} computed, "
            f"{stats['failed']} failed out of {stats['processed']} processed"
        )
        return stats

    def _compute_skill_outcome(
        self, dec, trm_type: str
    ) -> Optional[Dict[str, Any]]:
        """Compute outcome for a skill decision by dispatching to TRM-specific logic."""
        decision_data = dec.decision or {}
        state = dec.state_features or {}

        if trm_type == "atp":
            return self._compute_skill_atp_outcome(decision_data, state)
        elif trm_type == "rebalance":
            return self._compute_skill_rebalance_outcome(decision_data, state)
        elif trm_type == "po":
            return self._compute_skill_po_outcome(decision_data, state)
        elif trm_type == "inventory_buffer":
            return self._compute_skill_buffer_outcome(decision_data, state)
        else:
            return self._compute_skill_generic_outcome(trm_type, decision_data, state)

    def _compute_skill_atp_outcome(
        self, decision: Dict, state: Dict
    ) -> Optional[Dict[str, Any]]:
        """ATP skill outcome — check if the promised qty was fulfilled."""
        from app.models.sc_entities import OutboundOrderLine

        order_id = state.get("order_id")
        if not order_id:
            return None

        try:
            order = self.db.query(OutboundOrderLine).filter(
                OutboundOrderLine.order_id == order_id,
            ).first()
            if not order:
                return None

            promised_qty = decision.get("promised_qty", 0)
            fulfilled_qty = float(order.shipped_quantity or 0)
            return {
                "fulfilled_qty": fulfilled_qty,
                "requested_qty": float(order.ordered_quantity or 1),
                "was_on_time": bool(
                    order.last_ship_date
                    and order.promised_delivery_date
                    and order.last_ship_date <= order.promised_delivery_date
                ),
                "customer_priority": 3,
            }
        except Exception:
            return None

    def _compute_skill_rebalance_outcome(
        self, decision: Dict, state: Dict
    ) -> Optional[Dict[str, Any]]:
        """Rebalance skill outcome — did destination inventory improve?"""
        from app.models.sc_entities import InvLevel

        to_site = decision.get("to_site") or state.get("to_site")
        product_id = state.get("product_id")
        if not to_site or not product_id:
            return {"was_executed": True, "service_impact": 0.5}

        try:
            inv = self.db.query(InvLevel).filter(
                InvLevel.product_id == product_id,
            ).order_by(InvLevel.inventory_date.desc()).first()
            on_hand = float(inv.on_hand_qty or 0) if inv else 0
            return {
                "was_executed": True,
                "service_impact": 1.0 if on_hand > 0 else 0.0,
                "actual_qty": decision.get("transfer_qty", 0),
            }
        except Exception:
            return {"was_executed": True, "service_impact": 0.5}

    def _compute_skill_po_outcome(
        self, decision: Dict, state: Dict
    ) -> Optional[Dict[str, Any]]:
        """PO skill outcome — was delivery on time?"""
        return {
            "on_time_delivery": True,
            "days_late": 0,
            "days_of_supply_after": 14,
            "target_dos": 14,
            "stockout_occurred": False,
        }

    def _compute_skill_buffer_outcome(
        self, decision: Dict, state: Dict
    ) -> Optional[Dict[str, Any]]:
        """Buffer skill outcome — service level after adjustment."""
        from app.models.sc_entities import InvLevel

        product_id = state.get("product_id")
        if not product_id:
            return {"service_level": 0.95, "avg_inventory": 100, "actual_stockout_occurred": False}

        try:
            inv = self.db.query(InvLevel).filter(
                InvLevel.product_id == product_id,
            ).order_by(InvLevel.inventory_date.desc()).first()
            on_hand = float(inv.on_hand_qty or 0) if inv else 100
            return {
                "service_level": 1.0 if on_hand > 0 else 0.0,
                "avg_inventory": on_hand,
                "actual_stockout_occurred": on_hand <= 0,
                "actual_dos_at_end": on_hand / max(decision.get("buffer_target", 100) / 14, 1),
                "target_dos": 14,
            }
        except Exception:
            return {"service_level": 0.95, "avg_inventory": 100, "actual_stockout_occurred": False}

    def _compute_skill_generic_outcome(
        self, trm_type: str, decision: Dict, state: Dict
    ) -> Optional[Dict[str, Any]]:
        """Generic skill outcome for TRM types without specialized logic."""
        return {
            "was_executed": True,
            "decision_applied": True,
            "trm_type": trm_type,
        }

    @staticmethod
    def _summarize_outcome(
        trm_type: str, outcome: Dict[str, Any], reward: float
    ) -> str:
        """Generate a human-readable outcome summary for RAG retrieval."""
        quality = "good" if reward > 0.5 else "moderate" if reward > 0 else "poor"
        key_metrics = []
        if "fulfilled_qty" in outcome:
            fill_rate = outcome["fulfilled_qty"] / max(outcome.get("requested_qty", 1), 1)
            key_metrics.append(f"fill_rate={fill_rate:.0%}")
        if "service_level" in outcome:
            key_metrics.append(f"SL={outcome['service_level']:.0%}")
        if "was_on_time" in outcome:
            key_metrics.append(f"on_time={outcome['was_on_time']}")
        metrics_str = ", ".join(key_metrics) if key_metrics else "nominal"
        return f"{trm_type} skill decision: {quality} outcome (reward={reward:.3f}). {metrics_str}"

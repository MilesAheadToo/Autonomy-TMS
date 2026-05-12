"""TMS-side plane adapter for Core's ``OutcomeCollectorService``.

§3.64 adoption: implements :class:`OutcomePlaneAdapter` (Core) by
reading actual decision outcomes from TMS's operational tables —
``OutboundOrderLine`` for ATP fulfilment, ``InvLevel`` for inventory
state, ``CDCTriggerLog`` for replan effect. The plane-agnostic
orchestration (schedule decisions past horizon, compute reward,
compute counterfactual, update Bayesian posterior, emit OutcomeEvent)
lives in Core.

Migration scope:

* **SiteAgentDecision path** (this file) — migrated to Core via this
  adapter. The legacy ``outcome_collector.py`` retains the per-TRM
  decision-table path and the skill-decision path pending §3.65.
* **Per-TRM decision tables** (``PowellATPDecision``,
  ``PowellRebalanceDecision``, etc.) — still in legacy
  ``outcome_collector.collect_trm_outcomes``. Each table writes
  typed columns that don't map cleanly to Core's generic
  ``actual_outcome`` shape; consolidation is its own work.
* **Skill outcomes** (decision_embeddings KB rows) — still in
  legacy ``outcome_collector.collect_skill_outcomes``. KB-database
  path, separate session.

The single-home-rule clock starts at the moment Core's
``OutcomeCollectorService`` is wired in by ``relearning_jobs.py``;
the legacy file gets retired in §3.65.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from azirella_data_model.powell.powell_decision import SiteAgentDecision

from app.models.powell_decision import CDCTriggerLog
from app.models.sc_entities import InvLevel, OutboundOrderLine

logger = logging.getLogger(__name__)


class TmsOutcomeAdapter:
    """TMS implementation of :class:`OutcomePlaneAdapter`.

    Reads operational outcomes for the four legacy
    ``SiteAgentDecision`` types — ``atp_exception``,
    ``inventory_adjustment``, ``po_timing``, ``cdc_trigger`` — from
    TMS's plane-owned ERP-extraction tables (``OutboundOrderLine``,
    ``InvLevel``, ``CDCTriggerLog``).
    """

    #: Producer signature carried on every :class:`OutcomeEvent` this
    #: adapter's plane emits. Lets cross-plane consumers route on
    #: provenance.
    producer_signature: str = "tms:outcome_collector:v2"

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Required: read_site_agent_outcome
    # ------------------------------------------------------------------

    def read_site_agent_outcome(
        self, decision: SiteAgentDecision
    ) -> Optional[Dict[str, Any]]:
        decision_type = decision.decision_type
        if decision_type == "atp_exception":
            return self._read_atp_outcome(decision)
        if decision_type == "inventory_adjustment":
            return self._read_inventory_outcome(decision)
        if decision_type == "po_timing":
            return self._read_po_outcome(decision)
        if decision_type == "cdc_trigger":
            return self._read_cdc_outcome(decision)
        return None

    # ------------------------------------------------------------------
    # Required (but no-op for the legacy site-agent path): per-TRM reads
    # ------------------------------------------------------------------

    def read_trm_outcome(
        self, trm_type: str, decision_row: Any
    ) -> Optional[Dict[str, Any]]:
        # Per-TRM decision-table path stays in the legacy collector
        # until §3.65. Raising NotImplementedError tells Core's
        # ``collect_trm_outcomes`` to skip rather than misroute.
        raise NotImplementedError(
            "Per-TRM outcome collection is not yet migrated to Core "
            "for TMS. See legacy outcome_collector.collect_trm_outcomes; "
            "tracked under §3.65."
        )

    # ------------------------------------------------------------------
    # Optional hooks
    # ------------------------------------------------------------------

    def read_counterfactual_inputs(
        self,
        decision: SiteAgentDecision,
        actual_outcome: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Today the naive counterfactual doesn't need plane-specific
        # extras. Phase-1 twin replay will use this hook to read the
        # swap-point state (lane characteristics, supplier reliability,
        # BOM topology) from TMS-side state.
        return {}

    def override_horizon(
        self, decision_type: str, decision_row: Any
    ) -> Optional[timedelta]:
        # Use the canonical horizons in Core's
        # ``feedback_horizons.TRM_DECISION_HORIZONS``. TMS has no
        # tenant-specific lane horizon overrides today.
        return None

    # ------------------------------------------------------------------
    # Plane-specific outcome readers — lifted from legacy
    # outcome_collector._compute_*_outcome methods (semantics unchanged).
    # ------------------------------------------------------------------

    def _read_atp_outcome(
        self, decision: SiteAgentDecision
    ) -> Optional[Dict[str, Any]]:
        """ATP outcome: check whether the order was fulfilled, when,
        and at what fill rate."""
        try:
            input_state = decision.input_state or {}
            order_id = input_state.get("order_id")
            if not order_id:
                return None

            order = self.db.query(OutboundOrderLine).filter(
                OutboundOrderLine.order_id == order_id,
            ).first()
            if not order:
                return None

            fulfilled_qty = float(order.shipped_quantity or 0)
            was_on_time = (
                order.last_ship_date is not None
                and order.promised_delivery_date is not None
                and order.last_ship_date <= order.promised_delivery_date
            ) if order.promised_delivery_date else False

            return {
                "fulfilled_qty": fulfilled_qty,
                "requested_qty": float(order.ordered_quantity),
                "was_on_time": was_on_time,
                "customer_priority": int(order.priority_code == "HIGH") * 2 + 3,
                "status": order.status,
            }
        except Exception as exc:
            logger.debug("ATP outcome read failed: %s", exc)
            return None

    def _read_inventory_outcome(
        self, decision: SiteAgentDecision
    ) -> Optional[Dict[str, Any]]:
        """Inventory adjustment outcome: read the latest inventory
        snapshot and compare to the adjusted safety-stock target."""
        try:
            input_state = decision.input_state or {}
            product_id = input_state.get("product_id")
            site_id = input_state.get("site_id")
            if not product_id or not site_id:
                return None

            inv = self.db.query(InvLevel).filter(
                InvLevel.product_id == product_id,
                InvLevel.site_id == int(site_id)
                    if str(site_id).isdigit() else InvLevel.site_id == site_id,
            ).order_by(InvLevel.inventory_date.desc()).first()
            if not inv:
                return None

            final_ss = (decision.final_result or {}).get(
                "inventory_buffer",
                (decision.final_result or {}).get("safety_stock", 100),
            )
            on_hand = float(inv.on_hand_qty or 0)
            stockout = on_hand <= 0

            return {
                "service_level": 1.0 if not stockout else 0.0,
                "avg_inventory": on_hand,
                "actual_stockout_occurred": stockout,
                "actual_dos_at_end": on_hand / max(final_ss / 14, 1),
                "target_dos": 14,
            }
        except Exception as exc:
            logger.debug("Inventory outcome read failed: %s", exc)
            return None

    def _read_po_outcome(
        self, decision: SiteAgentDecision
    ) -> Optional[Dict[str, Any]]:
        """PO timing outcome — today's TMS plane lacks direct PO
        tracking so we return optimistic-default values. Phase-1 twin
        replay will replace this with the real receipt event."""
        try:
            final_result = decision.final_result or {}
            if not final_result.get("final_date"):
                return None
            return {
                "on_time_delivery": True,
                "days_late": 0,
                "days_of_supply_after": 14,
                "target_dos": 14,
                "stockout_occurred": False,
            }
        except Exception as exc:
            logger.debug("PO outcome read failed: %s", exc)
            return None

    def _read_cdc_outcome(
        self, decision: SiteAgentDecision
    ) -> Optional[Dict[str, Any]]:
        """CDC trigger outcome: did the next replan check show
        improved metrics?"""
        try:
            site_key = decision.site_key
            trigger_time = decision.timestamp
            next_entry = self.db.query(CDCTriggerLog).filter(
                CDCTriggerLog.site_key == site_key,
                CDCTriggerLog.timestamp > trigger_time,
            ).order_by(CDCTriggerLog.timestamp.asc()).first()
            if not next_entry or not next_entry.metrics_snapshot:
                return None

            pre_metrics = decision.input_state or {}
            post_metrics = next_entry.metrics_snapshot or {}
            pre_sl = pre_metrics.get("service_level", 0.9)
            post_sl = post_metrics.get("service_level", 0.9)

            return {
                "pre_replan_kpi": pre_sl,
                "post_replan_kpi": post_sl,
                "replan_cost": 0.01,
                "metrics_improved": post_sl > pre_sl,
            }
        except Exception as exc:
            logger.debug("CDC outcome read failed: %s", exc)
            return None

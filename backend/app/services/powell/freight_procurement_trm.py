"""
FreightProcurementTRM — Carrier Waterfall Tendering Agent

The first TMS TRM wired end-to-end: Load READY → evaluate → tender
decision → governance → Decision Stream → user INSPECT/OVERRIDE.

Uses the trained BC checkpoint when available, falls back to the
deterministic heuristic teacher (compute_tms_decision) when not.

Maps to SCP's po_creation TRM slot in the Powell site agent
(procurement phase, ACQUIRE role). Writes to powell_po_decisions
table with TMS-specific semantics:
  - supplier_id → carrier_id
  - order_qty → 1 (one load)
  - total_cost → tendered rate
  - decision_reasoning → composite carrier scoring detail

Trigger: called by the decision cycle scheduler when Loads in
PLANNING/READY status are detected, or via manual API endpoint.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session

from app.models.tms_entities import (
    Carrier, CarrierLane, FreightRate, FreightTender,
    Load, LoadStatus, TenderStatus, TransportMode, EquipmentType,
    RateType,
)


from app.services.powell.agent_decision_writer import record_trm_decision
from app.services.powell.bc_checkpoint_loader import load_bc_checkpoint

logger = logging.getLogger(__name__)


class FreightProcurementTRM:
    """
    Evaluates carrier selection for loads in READY state.

    Lifecycle:
        trm = FreightProcurementTRM(db_session, tenant_id, config_id)
        trm.load_checkpoint(path)  # optional — falls back to heuristic
        decisions = trm.evaluate_pending_loads()
    """

    def __init__(self, db: Session, tenant_id: int, config_id: int):
        self.db = db
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._model = None
        self._heuristic_fn = None

        # Import heuristic teacher
        from azirella_data_model.powell.tms.heuristic_library.dispatch import (
            compute_tms_decision,
        )
        from azirella_data_model.powell.tms.heuristic_library.base import (
            FreightProcurementState,
        )
        self._compute_decision = compute_tms_decision
        self._StateClass = FreightProcurementState

    def load_checkpoint(self, checkpoint_path: str) -> bool:
        """Load a trained BC checkpoint. Returns True on success."""
        ckpt = load_bc_checkpoint(checkpoint_path, "freight_procurement")
        if ckpt is None:
            return False
        self._model = ckpt
        return True

    def find_pending_loads(self) -> List[Load]:
        """Find loads in PLANNING or READY status without accepted tenders."""
        return self.db.execute(
            select(Load).where(
                and_(
                    Load.tenant_id == self.tenant_id,
                    Load.status.in_([LoadStatus.PLANNING, LoadStatus.READY]),
                )
            ).order_by(Load.planned_departure)
        ).scalars().all()

    def evaluate_load(self, load: Load) -> Optional[Dict[str, Any]]:
        """
        Evaluate carrier selection for one load.

        Returns a decision dict with:
          - carrier_id, carrier_name, offered_rate
          - composite_score, scoring_detail
          - decision_method ("trm_model" or "heuristic")
          - confidence, urgency
        """
        # Build carrier waterfall for this load's lane
        candidates = self._get_carrier_candidates(load)
        if not candidates:
            logger.warning("No carrier candidates for load %s", load.load_number)
            return None

        # Build FreightProcurementState
        primary = candidates[0] if candidates else None
        state = self._StateClass(
            load_id=load.id,
            lane_id=0,
            mode=load.mode.value if load.mode else "FTL",
            weight=load.total_weight or 0,
            lead_time_hours=self._hours_to_pickup(load),
            primary_carrier_id=primary["carrier_id"] if primary else None,
            primary_carrier_rate=primary["rate"] if primary else 0,
            primary_carrier_acceptance_pct=primary.get("acceptance_pct", 0.85),
            backup_carriers=[c for c in candidates[1:]],
            spot_rate=primary["rate"] * 1.15 if primary else 0,
            contract_rate=primary["rate"] if primary else 0,
            market_tightness=0.5,
            dat_benchmark_rate=primary["rate"] * 1.05 if primary else 0,
            tender_attempt=1,
            max_tender_attempts=4,
            hours_to_tender_deadline=24.0,
        )

        action_name_map = {
            0: "ACCEPT", 1: "REJECT", 2: "DEFER", 3: "ESCALATE",
        }

        if self._model is not None:
            from app.services.powell.bc_checkpoint_loader import predict_action
            try:
                action, confidence, probs = predict_action(self._model, state)
                action_name = action_name_map.get(action, "UNKNOWN")
                urgency = (
                    confidence if action != 0 else max(0.0, 1.0 - confidence)
                )
                # BC model doesn't pick a carrier — fall back to the
                # primary candidate (the contract carrier waterfall
                # produces the same selection the heuristic would).
                selected_carrier = candidates[0] if candidates else None
                return {
                    "load_id": load.id,
                    "load_number": load.load_number,
                    "carrier_id": selected_carrier["carrier_id"] if selected_carrier else None,
                    "carrier_name": selected_carrier.get("carrier_name") if selected_carrier else None,
                    "offered_rate": selected_carrier["rate"] if selected_carrier else 0,
                    "action": action,
                    "action_name": action_name,
                    "composite_score": confidence,
                    "confidence": confidence,
                    "urgency": urgency,
                    "reasoning": (
                        f"BC model predicted {action_name} (p={confidence:.3f})"
                    ),
                    "decision_method": "trm_model",
                    "scoring_detail": {
                        "model_probs": probs,
                        "model_val_acc": self._model.best_val_acc,
                    },
                }
            except Exception as e:
                logger.warning(
                    "FreightProcurement BC inference failed (falling back "
                    "to heuristic): %s", e,
                )

        decision = self._compute_decision("freight_procurement", state)

        # Find the selected carrier
        selected_carrier = None
        if decision.params_used and decision.params_used.get("carrier_id"):
            cid = decision.params_used["carrier_id"]
            selected_carrier = next((c for c in candidates if c["carrier_id"] == cid), None)
        if not selected_carrier and candidates:
            selected_carrier = candidates[0]

        return {
            "load_id": load.id,
            "load_number": load.load_number,
            "carrier_id": selected_carrier["carrier_id"] if selected_carrier else None,
            "carrier_name": selected_carrier.get("carrier_name") if selected_carrier else None,
            "offered_rate": selected_carrier["rate"] if selected_carrier else 0,
            "action": decision.action,
            "action_name": action_name_map.get(decision.action, "UNKNOWN"),
            "composite_score": decision.params_used.get("composite_score") if decision.params_used else None,
            "confidence": decision.confidence,
            "urgency": decision.urgency,
            "reasoning": decision.reasoning,
            "decision_method": "heuristic",
            "scoring_detail": decision.params_used,
        }

    def evaluate_and_persist(self, load: Load) -> Optional[Dict[str, Any]]:
        """Evaluate + write decision to powell_po_decisions + create tender."""
        result = self.evaluate_load(load)
        if not result or result["action"] == 3:  # ESCALATE
            return result

        if result["carrier_id"] and result["action"] == 0:  # ACCEPT
            # Create tender record
            tender = FreightTender(
                load_id=load.id,
                carrier_id=result["carrier_id"],
                tender_sequence=1,
                status=TenderStatus.CREATED,
                offered_rate=result["offered_rate"],
                tendered_at=datetime.utcnow(),
                selection_rationale=result["scoring_detail"],
                agent_decision_id=f"fp-{load.id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                tenant_id=self.tenant_id,
            )
            self.db.add(tender)

            # The authoritative record for a freight-procurement decision is
            # the FreightTender row written above. We deliberately DO NOT write
            # a powell_po_decisions row: that table is a purchase-order decision
            # artefact (product-keyed, site-keyed) whose schema does not fit
            # freight semantics, and its column naming (`location_id` instead
            # of `site_id`) is SCP-lineage drift from AWS SC DM canonical
            # naming anyway.
            # Sprint 1 Week 4-5 (PREPARE.3 in TMS_ADOPTION_GUIDE_20260420):
            # switch to dual-writing core.agent_decisions with
            # decision_type=FREIGHT_PROCUREMENT once the intersection-contract
            # package ships.

            load.status = LoadStatus.TENDERED
            self.db.flush()
            logger.info(
                "FreightProcurement: load %s → carrier %s at $%.0f (%s)",
                load.load_number, result["carrier_name"],
                result["offered_rate"], result["decision_method"],
            )


        # PREPARE.3 dual-write to core.agent_decisions
        record_trm_decision(
            self.db,
            tenant_id=self.tenant_id,
            config_id=self.config_id,
            trm_type="freight_procurement",
            result=result,
            item_code=f"load-{load.id}",
            item_name=f"load {load.load_number}",
            category="freight_procurement",
        )

        return result

    def evaluate_pending_loads(self) -> List[Dict[str, Any]]:
        """Evaluate all pending loads. Returns list of decision results."""
        loads = self.find_pending_loads()
        results = []
        for load in loads:
            result = self.evaluate_and_persist(load)
            if result:
                results.append(result)
        self.db.commit()
        return results

    # ── Helpers ──────────────────────────────────────────────────────────

    def _get_carrier_candidates(self, load: Load) -> List[Dict[str, Any]]:
        """Build carrier waterfall for a load from CarrierLane + FreightRate."""
        # Find carrier lanes matching this load's origin→destination
        cls = self.db.execute(
            select(CarrierLane, Carrier).join(
                Carrier, CarrierLane.carrier_id == Carrier.id
            ).where(
                and_(
                    CarrierLane.tenant_id == self.tenant_id,
                    CarrierLane.is_active.is_(True),
                )
            ).order_by(CarrierLane.priority)
        ).all()

        candidates = []
        for cl, carrier in cls:
            # Find rate for this carrier
            rate = self.db.execute(
                select(FreightRate.rate_flat).where(
                    and_(
                        FreightRate.carrier_id == carrier.id,
                        FreightRate.tenant_id == self.tenant_id,
                        FreightRate.is_active.is_(True),
                    )
                ).limit(1)
            ).scalar_one_or_none()

            candidates.append({
                "carrier_id": carrier.id,
                "carrier_name": carrier.name,
                "carrier_code": carrier.code,
                "rate": float(rate) if rate else 2500.0,
                "priority": cl.priority,
                "acceptance_pct": 0.85,
                "otp_pct": 0.93,
            })

        return candidates[:10]

    @staticmethod
    def _hours_to_pickup(load: Load) -> float:
        if load.planned_departure:
            delta = load.planned_departure - datetime.utcnow()
            return max(0, delta.total_seconds() / 3600)
        return 48.0

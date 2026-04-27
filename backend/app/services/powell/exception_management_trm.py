"""
ExceptionManagementTRM — Shipment Exception Triage (ASSESS phase)

Fourth TMS-native TRM. Maps to SCP's `quality_disposition` slot but
operates on freight exceptions (delay, damage, refused, rolled,
temperature excursion, route deviation) rather than inspection
dispositions.

Trigger entity: `ShipmentException` rows in resolution_status DETECTED
or INVESTIGATING. One evaluation per exception per cycle.

Action space (from Core dispatch):
  ACCEPT (auto-resolve within appointment buffer)
  RETENDER (re-tender to alternate carrier)
  REROUTE (reroute on remaining window)
  ESCALATE (human intervention)

TMS does NOT mutate ShipmentException.resolution_status in v1 — state
transitions are driven by the ExceptionResolution workflow separately.
The TRM produces a decision dict for Decision Stream consumption +
logs at severity matching the action's urgency. Persistence to
core.agent_decisions lands in Sprint 1 Week 4-5 PREPARE.3.

Feature-vector sources (v1):
- exception_type / severity / detected_at / estimated_delay_hrs /
  estimated_cost_impact / revenue_at_risk ← ShipmentException columns
- is_temperature_sensitive / is_hazmat / declared_value / latest_delivery
  ← parent TMSShipment (joined via shipment_id)
- carrier_id / carrier_reliability_score ← ShipmentLeg.carrier_id
  (joined via leg_id when present) + CarrierScorecard.on_time_delivery_pct
- alternate_carriers_available ← count(CarrierLane) for the same
  origin→destination (floored at 0; rough proxy)
- can_retender / can_reroute ← conservative defaults (True/False)

Not sourced (honest defaults, wire up later):
- shipment_priority (default 3; needs priority field on TMSShipment)
- customer_tier (default 3; needs customer-tiering lookup)
- penalty_exposure / expedite_cost_estimate (default 0.0; needs SLA +
  rate tables)
- downstream_shipments_affected (default 0; needs cascade analysis)
- appointment_buffer_hrs (default 2.0)
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from app.models.tms_entities import (
    CarrierLane,
    CarrierScorecard,
    ExceptionResolutionStatus,
    ShipmentException,
    ShipmentLeg,
    TMSShipment,
)


from app.services.powell.agent_decision_writer import record_trm_decision
from app.services.powell.policy_reader import PolicyCache, lowest_tier_priority
from app.services.powell.terminal_coordinator_service import (
    get_active_urgency_multiplier,
)

logger = logging.getLogger(__name__)



class ExceptionManagementTRM:
    """Evaluates shipment exceptions and recommends resolution action."""

    DEFAULT_CARRIER_RELIABILITY = 0.80
    DEFAULT_APPOINTMENT_BUFFER_HRS = 2.0
    DEFAULT_PRIORITY = 3
    DEFAULT_CUSTOMER_TIER = 3
    DEFAULT_CARRIER_RESPONSE_HRS = 2.0

    def __init__(self, db: Session, tenant_id: int, config_id: int):
        self.db = db
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._model = None
        # Lazy active-policy reader. Falls back to class-level
        # DEFAULT_* constants when no policy is provisioned (with a
        # one-time WARNING from PolicyCache).
        self._policy = PolicyCache(tenant_id=tenant_id, config_id=config_id)

        from azirella_data_model.powell.tms.heuristic_library.dispatch import (
            compute_tms_decision,
        )
        from azirella_data_model.powell.tms.heuristic_library.base import (
            ExceptionManagementState,
        )
        self._compute_decision = compute_tms_decision
        self._StateClass = ExceptionManagementState

    def load_checkpoint(self, checkpoint_path: str) -> bool:
        """Load a trained BC checkpoint. Returns True on success."""
        ckpt = load_bc_checkpoint(checkpoint_path, "exception_management")
        if ckpt is None:
            return False
        self._model = ckpt
        return True

    def find_open_exceptions(self) -> List[ShipmentException]:
        """Return exceptions in DETECTED or INVESTIGATING status."""
        return self.db.execute(
            select(ShipmentException).where(
                and_(
                    ShipmentException.tenant_id == self.tenant_id,
                    ShipmentException.resolution_status.in_([
                        ExceptionResolutionStatus.DETECTED,
                        ExceptionResolutionStatus.INVESTIGATING,
                    ]),
                )
            ).order_by(
                # Most-recent-detected first; critical severity wins ties
                desc(ShipmentException.severity),
                desc(ShipmentException.detected_at),
            )
        ).scalars().all()

    def evaluate_exception(self, exc: ShipmentException) -> Optional[Dict[str, Any]]:
        """Evaluate one exception. Never mutates DB."""
        state = self._build_state(exc)

        action_name_map = {
            0: "ACCEPT", 1: "REJECT", 2: "DEFER", 3: "ESCALATE",
            4: "MODIFY", 5: "RETENDER", 6: "REROUTE",
        }

        l2_multiplier = self._l2_urgency_multiplier(exc)

        if self._model is not None:
            from app.services.powell.bc_checkpoint_loader import predict_action
            try:
                action, confidence, probs = predict_action(self._model, state)
                action_name = action_name_map.get(action, "UNKNOWN")
                baseline_urgency = (
                    confidence if action != 0 else max(0.0, 1.0 - confidence)
                )
                modulated_urgency = max(
                    0.0, min(1.0, baseline_urgency * l2_multiplier),
                )
                return {
                    "exception_id": exc.id,
                    "shipment_id": exc.shipment_id,
                    "exception_type": state.exception_type,
                    "severity": state.severity,
                    "resolution_status": exc.resolution_status.value,
                    "estimated_delay_hrs": state.estimated_delay_hrs,
                    "delivery_window_hrs": state.delivery_window_remaining_hrs,
                    "action": action,
                    "action_name": action_name,
                    "priority_score": None,
                    "confidence": confidence,
                    "urgency": modulated_urgency,
                    "baseline_urgency": baseline_urgency,
                    "l2_urgency_multiplier": l2_multiplier,
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
                    "ExceptionManagement BC inference failed (falling back "
                    "to heuristic): %s", e,
                )

        decision = self._compute_decision("exception_management", state)

        action_name = action_name_map.get(decision.action, "UNKNOWN")

        # L2 Terminal Coordinator urgency modulation. Falls back to
        # 1.0 (neutral) when no override exists at this exception's
        # hub. We key off the shipment's origin_site_id — that's the
        # canonical "where the work is happening" hub for an
        # in-flight exception.
        baseline_urgency = float(decision.urgency)
        modulated_urgency = max(0.0, min(1.0, baseline_urgency * l2_multiplier))

        return {
            "exception_id": exc.id,
            "shipment_id": exc.shipment_id,
            "exception_type": state.exception_type,
            "severity": state.severity,
            "resolution_status": exc.resolution_status.value,
            "estimated_delay_hrs": state.estimated_delay_hrs,
            "delivery_window_hrs": state.delivery_window_remaining_hrs,
            "action": decision.action,
            "action_name": action_name,
            "priority_score": (
                decision.params_used.get("priority_score")
                if decision.params_used else None
            ),
            "confidence": decision.confidence,
            "urgency": modulated_urgency,
            "baseline_urgency": baseline_urgency,
            "l2_urgency_multiplier": l2_multiplier,
            "reasoning": decision.reasoning,
            "decision_method": "heuristic",
            "scoring_detail": decision.params_used,
        }

    def _l2_urgency_multiplier(self, exc: ShipmentException) -> float:
        """Resolve the L2 Terminal Coordinator's urgency override for
        this exception's hub. Returns 1.0 (no modulation) when no
        active override exists. Hub is the shipment's origin_site_id.
        """
        if exc.shipment_id is None:
            return 1.0
        shipment = self.db.execute(
            select(TMSShipment.origin_site_id).where(
                TMSShipment.id == exc.shipment_id
            )
        ).scalar()
        if shipment is None:
            return 1.0
        return get_active_urgency_multiplier(
            self.db,
            tenant_id=self.tenant_id,
            hub_site_id=int(shipment),
            trm_type="exception_management",
        )

    def evaluate_and_log(self, exc: ShipmentException) -> Optional[Dict[str, Any]]:
        """Evaluate + log at severity matching the action.

        ESCALATE / RETENDER / REROUTE → WARNING (operational intervention needed).
        ACCEPT → INFO (auto-absorb within tolerance).
        """
        result = self.evaluate_exception(exc)
        if not result:
            return result

        action = result["action_name"]
        if action in ("ESCALATE", "RETENDER", "REROUTE"):
            logger.warning(
                "ExceptionManagement %s: exc %d (shipment %d, %s/%s) — %s",
                action,
                exc.id,
                exc.shipment_id,
                result["exception_type"],
                result["severity"],
                result["reasoning"],
            )
        else:
            logger.info(
                "ExceptionManagement %s: exc %d (shipment %d, %s) — urg=%.2f",
                action,
                exc.id,
                exc.shipment_id,
                result["exception_type"],
                result["urgency"],
            )

        # PREPARE.3 dual-write to core.agent_decisions
        record_trm_decision(
            self.db,
            tenant_id=self.tenant_id,
            config_id=self.config_id,
            trm_type="exception_management",
            result=result,
            item_code=f"exception-{exc.id}",
            item_name=f"{exc.exception_type} on shipment {exc.shipment_id}",
            category="exception_management",
        )

        return result

    def evaluate_pending_exceptions(self) -> List[Dict[str, Any]]:
        """Evaluate every DETECTED/INVESTIGATING exception for the tenant.

        Also checks the open-exception backlog against
        `policy.escalation_thresholds.exception_backlog_count`. When the
        backlog exceeds the threshold, every result in the batch is
        flagged with `policy_escalation=True` and a structured WARNING
        is logged once. The L4 Strategic agent reads the warning logs
        when re-considering staffing / network policy.
        """
        exceptions = self.find_open_exceptions()
        results = []
        backlog_breach = self._policy_backlog_breach(len(exceptions))
        for exc in exceptions:
            result = self.evaluate_and_log(exc)
            if result:
                if backlog_breach is not None:
                    result["policy_escalation"] = True
                    result["policy_backlog_threshold"] = backlog_breach
                results.append(result)
        if backlog_breach is not None:
            logger.warning(
                "ExceptionManagement backlog escalation: tenant=%s open=%d "
                "threshold=%d (escalation_thresholds.exception_backlog_count) "
                "— L4 review recommended",
                self.tenant_id, len(exceptions), backlog_breach,
            )
        return results

    def _policy_backlog_breach(self, open_count: int) -> Optional[int]:
        """Return the threshold value when breached, else None.

        Reads `policy.escalation_thresholds.exception_backlog_count`.
        Falls back to `None` (no escalation) when policy is absent —
        consistent with the no-fallbacks invariant.
        """
        policy = self._policy.get(self.db)
        if policy is None:
            return None
        thresholds = policy.escalation_thresholds or {}
        try:
            threshold = int(thresholds.get("exception_backlog_count"))
        except (TypeError, ValueError):
            return None
        if open_count > threshold:
            return threshold
        return None

    # ── Helpers ──────────────────────────────────────────────────────────

    def _build_state(self, exc: ShipmentException):
        """Construct ExceptionManagementState from ShipmentException + joins.

        `customer_tier` is sourced from `policy.service_level_tiers` —
        until a per-shipment customer→tier mapping wires through, every
        shipment gets the lowest-tier priority (most permissive) as a
        conservative default. When that mapping lands, swap the
        `lowest_tier_priority(policy)` call for a per-shipment lookup.
        """
        now = datetime.utcnow()
        hours_since_detected = 0.0
        if exc.detected_at:
            hours_since_detected = max(
                0.0, (now - exc.detected_at).total_seconds() / 3600
            )

        # Parent shipment — commodity sensitivity + delivery window + value.
        shipment = self.db.execute(
            select(TMSShipment).where(TMSShipment.id == exc.shipment_id)
        ).scalar_one_or_none()

        is_temp_sensitive = bool(getattr(shipment, "is_temperature_sensitive", False))
        is_hazmat = bool(getattr(shipment, "is_hazmat", False))
        shipment_value = float(getattr(shipment, "declared_value", 0) or 0)

        # Delivery-window remaining: latest_delivery is the best bound.
        delivery_window_hrs = 0.0
        if shipment:
            target = getattr(shipment, "latest_delivery", None) or \
                     getattr(shipment, "requested_delivery_date", None)
            if target:
                delivery_window_hrs = max(0.0, (target - now).total_seconds() / 3600)

        # Leg → carrier lookup
        carrier_id = 0
        carrier_reliability = self.DEFAULT_CARRIER_RELIABILITY
        if exc.leg_id:
            leg = self.db.execute(
                select(ShipmentLeg).where(ShipmentLeg.id == exc.leg_id)
            ).scalar_one_or_none()
            if leg and leg.carrier_id:
                carrier_id = int(leg.carrier_id)
                scorecard = self.db.execute(
                    select(CarrierScorecard)
                    .where(CarrierScorecard.carrier_id == carrier_id)
                    .order_by(desc(CarrierScorecard.period_end))
                    .limit(1)
                ).scalar_one_or_none()
                if scorecard and scorecard.on_time_delivery_pct is not None:
                    carrier_reliability = max(
                        0.5, min(1.0, float(scorecard.on_time_delivery_pct))
                    )

        # Alternate carriers on the same lane (origin→destination).
        alternate_carriers = 0
        if shipment:
            origin = getattr(shipment, "origin_site_id", None)
            dest = getattr(shipment, "destination_site_id", None)
            if origin and dest:
                alt = self.db.execute(
                    select(func.count(CarrierLane.id)).where(
                        and_(
                            CarrierLane.tenant_id == self.tenant_id,
                            CarrierLane.is_active.is_(True),
                            CarrierLane.carrier_id != carrier_id,
                        )
                    )
                ).scalar_one_or_none()
                alternate_carriers = int(alt or 0)

        return self._StateClass(
            exception_id=exc.id,
            shipment_id=exc.shipment_id,
            exception_type=exc.exception_type.value if exc.exception_type else "",
            severity=exc.severity.value if exc.severity else "MEDIUM",
            hours_since_detected=hours_since_detected,
            estimated_delay_hrs=float(exc.estimated_delay_hrs or 0),
            estimated_cost_impact=float(exc.estimated_cost_impact or 0),
            revenue_at_risk=float(exc.revenue_at_risk or 0),
            shipment_priority=self.DEFAULT_PRIORITY,
            is_temperature_sensitive=is_temp_sensitive,
            is_hazmat=is_hazmat,
            delivery_window_remaining_hrs=delivery_window_hrs,
            carrier_id=carrier_id,
            carrier_reliability_score=carrier_reliability,
            carrier_response_time_hrs=self.DEFAULT_CARRIER_RESPONSE_HRS,
            can_retender=alternate_carriers > 0,
            alternate_carriers_available=alternate_carriers,
            can_reroute=delivery_window_hrs > 4,
            can_partial_deliver=False,
            shipment_value=shipment_value,
            penalty_exposure=0.0,
            expedite_cost_estimate=0.0,
            appointment_buffer_hrs=self.DEFAULT_APPOINTMENT_BUFFER_HRS,
            downstream_shipments_affected=0,
            customer_tier=self._resolve_customer_tier(),
        )

    def _resolve_customer_tier(self) -> int:
        """Customer-tier priority for this shipment.

        v1: pulls the lowest tier (highest priority integer) from
        policy.service_level_tiers. Falls back to DEFAULT_CUSTOMER_TIER
        when no policy is provisioned. When a per-shipment customer
        → tier mapping wires through, swap this for a tier-specific
        lookup.
        """
        policy = self._policy.get(self.db)
        if policy is None:
            return self.DEFAULT_CUSTOMER_TIER
        prio = lowest_tier_priority(policy)
        if prio is None:
            return self.DEFAULT_CUSTOMER_TIER
        return int(prio)

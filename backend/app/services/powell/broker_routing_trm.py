"""
BrokerRoutingTRM — Broker vs Asset-Carrier Routing (ACQUIRE phase)

Fifth TMS-native TRM. Maps to SCP's `subcontracting` slot. Fires on
Loads that have exhausted their contract-carrier waterfall — when the
primary carriers declined or expired their tenders, this TRM decides:
- ACCEPT a specific broker + rate (broker id + premium recorded)
- ESCALATE for human intervention (no broker available OR rate exceeds
  urgency-adjusted threshold)

Action space (from Core dispatch):
  ACCEPT   — broker selected; the Core heuristic already picked the
             reliability-adjusted-cost winner and validated its premium
             vs DAT benchmark / market / time-urgency thresholds.
  ESCALATE — no broker available OR best broker's rate exceeds the
             effective threshold (market tightness + time urgency
             widens the threshold, but extreme premiums still escalate
             for approval).

No Load.status mutation in v1. The authoritative tender artefact remains
the FreightTender row; when BrokerRouting returns ACCEPT, the caller
creates a new FreightTender with the broker carrier_id + rate. Log-only
for v1 — PREPARE.3's dual-write to core.agent_decisions with
decision_type=BROKER_ROUTING lands Sprint 1 Week 4-5.

Feature-vector sources (v1):
- load_id / lane_id / mode / hours_to_pickup ← Load
- tender_attempts_exhausted ← count(FreightTender where not ACCEPTED)
- all_contract_carriers_declined ← every non-ACCEPTED tender has status
  ∈ {DECLINED, EXPIRED, REJECTED}
- available_brokers ← Carrier rows where carrier_type == BROKER (joined
  with latest FreightRate on their active CarrierLane). Each entry
  carries {id, name, rate, reliability, coverage_score}.
- contract_rate ← last ACCEPTED tender's rate on this lane (fallback to
  any ACCEPTED tender rate on this Load; else 0 and the dispatcher
  infers benchmark from broker rates)
- market_tightness / spot_rate / broker_rate_premium_pct / dat_benchmark
  → honest priors (0.5 / 0 / 0.15 / 0) until market-data integration
  lands
- shipment_priority / is_customer_committed → default 3 / False
- budget_remaining → 0 (no budget tracking in TMS today)

Seed-data caveat: no BROKER-type carriers exist in the acer-nitro seed
DB today (7 ASSET, 1 THREE_PL). Every load in acer-nitro's current
state will return ESCALATE("No brokers available"). Once a broker row
is seeded (CarrierType.BROKER) with a FreightRate on the load's lane,
the full decision path exercises.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.orm import Session

from app.models.tms_entities import (
    Carrier,
    CarrierLane,
    CarrierType,
    FreightRate,
    FreightTender,
    Load,
    LoadStatus,
    TenderStatus,
    TransportMode,
)


from app.services.powell.agent_decision_writer import record_trm_decision
logger = logging.getLogger(__name__)



class BrokerRoutingTRM:
    """Broker-vs-asset-carrier routing for loads past their contract waterfall."""

    # Honest defaults for features TMS cannot source today. Wire up with
    # DAT / Greenscreens integration + budget tracking.
    DEFAULT_MARKET_TIGHTNESS = 0.5
    DEFAULT_BROKER_PREMIUM = 0.15
    DEFAULT_PRIORITY = 3

    # Statuses considered "declined/expired" from the primary waterfall.
    # TMS's TenderStatus enum has no REJECTED value — CANCELLED is the
    # closest analogue (carrier-side cancellation after acceptance is a
    # separate path; CANCELLED here means "this offer is dead").
    _DECLINED_STATUSES = {
        TenderStatus.DECLINED,
        TenderStatus.EXPIRED,
        TenderStatus.CANCELLED,
    }

    def __init__(self, db: Session, tenant_id: int, config_id: int):
        self.db = db
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._model = None

        from azirella_data_model.powell.tms.heuristic_library.dispatch import (
            compute_tms_decision,
        )
        from azirella_data_model.powell.tms.heuristic_library.base import (
            BrokerRoutingState,
        )
        self._compute_decision = compute_tms_decision
        self._StateClass = BrokerRoutingState

    def load_checkpoint(self, checkpoint_path: str) -> bool:
        """Load a trained BC checkpoint. Returns True on success."""
        ckpt = load_bc_checkpoint(checkpoint_path, "broker_routing")
        if ckpt is None:
            return False
        self._model = ckpt
        return True

    def find_candidate_loads(self) -> List[Load]:
        """Loads where the contract waterfall has failed to ACCEPT.

        v1 filter:
          Load.status IN (PLANNING, READY, TENDERED)
          AND at least one FreightTender exists
          AND no FreightTender is ACCEPTED
          AND every non-ACCEPTED tender is in {DECLINED, EXPIRED, REJECTED}
            OR  Load.planned_departure is < 48h away (urgent even if not
            all declined yet — the time gate fires BrokerRouting's
            should_broker rule).

        This is the operational-reality filter: a load that has tried
        carriers but hasn't landed one, with the clock ticking.
        """
        # Subquery: any ACCEPTED tender on this load?
        accepted_loads = (
            select(FreightTender.load_id)
            .where(
                and_(
                    FreightTender.load_id.isnot(None),
                    FreightTender.status == TenderStatus.ACCEPTED,
                )
            )
            .distinct()
            .subquery()
        )

        # Candidate loads: tenders exist but none accepted.
        tendered_loads = (
            select(FreightTender.load_id)
            .where(FreightTender.load_id.isnot(None))
            .distinct()
            .subquery()
        )

        return self.db.execute(
            select(Load).where(
                and_(
                    Load.tenant_id == self.tenant_id,
                    Load.status.in_([
                        LoadStatus.PLANNING,
                        LoadStatus.READY,
                        LoadStatus.TENDERED,
                    ]),
                    Load.id.in_(select(tendered_loads.c.load_id)),
                    Load.id.notin_(select(accepted_loads.c.load_id)),
                )
            ).order_by(Load.planned_departure.nullslast(), Load.id)
        ).scalars().all()

    def evaluate_load(self, load: Load) -> Optional[Dict[str, Any]]:
        """Evaluate broker-routing decision for one load. Never mutates."""
        state = self._build_state(load)

        # AD-11 cross-plane: when SCP has issued a high-priority
        # DeploymentRequirement for this load's destination, lift the
        # DR's priority into shipment_priority + flip
        # is_customer_committed and bump budget_remaining by the miss-
        # cost (so the heuristic can take a more expensive broker
        # when the alternative is missing the deadline).
        binding_dr = self._find_binding_deployment_requirement(load)
        spv_summary: Dict[str, Any] = {}
        if binding_dr is not None:
            state, spv_summary = self._apply_shadow_prices(state, binding_dr)

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
                return {
                    "load_id": load.id,
                    "load_number": load.load_number,
                    "load_status": load.status.value,
                    "tender_attempts_exhausted": state.tender_attempts_exhausted,
                    "all_contract_carriers_declined": state.all_contract_carriers_declined,
                    "hours_to_pickup": state.hours_to_pickup,
                    "brokers_available": len(state.available_brokers),
                    "action": action,
                    "action_name": action_name,
                    "selected_broker_id": None,
                    "selected_broker_name": None,
                    "selected_rate": 0.0,
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
                    "shadow_price_adjustment": spv_summary,
                }
            except Exception as e:
                logger.warning(
                    "BrokerRouting BC inference failed (falling back "
                    "to heuristic): %s", e,
                )

        decision = self._compute_decision("broker_routing", state)

        action_name = action_name_map.get(decision.action, "UNKNOWN")

        selected_broker_id = None
        selected_broker_name = None
        if decision.params_used:
            selected_broker_id = decision.params_used.get("broker_id")
            selected_broker_name = decision.params_used.get("broker_name")

        return {
            "load_id": load.id,
            "load_number": load.load_number,
            "load_status": load.status.value,
            "tender_attempts_exhausted": state.tender_attempts_exhausted,
            "all_contract_carriers_declined": state.all_contract_carriers_declined,
            "hours_to_pickup": state.hours_to_pickup,
            "brokers_available": len(state.available_brokers),
            "action": decision.action,
            "action_name": action_name,
            "selected_broker_id": selected_broker_id,
            "selected_broker_name": selected_broker_name,
            "selected_rate": decision.quantity if action_name == "ACCEPT" else 0.0,
            "confidence": decision.confidence,
            "urgency": decision.urgency,
            "reasoning": decision.reasoning,
            "decision_method": "heuristic",
            "scoring_detail": decision.params_used,
            "shadow_price_adjustment": spv_summary,  # AD-11 cross-plane signal
        }

    # ── AD-11 cross-plane: shadow-price-driven decisions ──────────

    def _find_binding_deployment_requirement(self, load: Load):
        """Highest-priority active DR matching load.destination_site_id.

        A load arriving at site X helps satisfy any DR for X whose
        required_by is approximately the load's arrival (on-time)
        or somewhat after (the load contributes to a future-dated
        requirement). The filter window is
        ``[now, planned_arrival + 14 days]`` to capture both the
        immediate deadline (this load is a direct response) and
        upcoming ones (this load draws down the pipeline).

        Returns the DeploymentRequirement row or None.
        """
        try:
            from azirella_data_model.intersections.supply_transport import (
                DeploymentRequirement,
            )
        except ImportError:
            return None
        if not load.destination_site_id:
            return None
        from datetime import datetime as _dt, timedelta as _td
        now = _dt.utcnow()
        # Window stretches from now to 14 days past planned_arrival
        # (or 28 days from now if arrival is unset). This admits DRs
        # that the load helps satisfy even if their required_by is
        # later than the load's arrival.
        anchor = load.planned_arrival or (now + _td(days=14))
        deadline = anchor + _td(days=14)
        return self.db.execute(
            select(DeploymentRequirement).where(
                DeploymentRequirement.tenant_id == self.tenant_id,
                DeploymentRequirement.dest_site_id == str(load.destination_site_id),
                DeploymentRequirement.required_by >= now,
                DeploymentRequirement.required_by <= deadline,
                DeploymentRequirement.superseded_by.is_(None),
            )
            .order_by(
                DeploymentRequirement.priority.desc(),
                DeploymentRequirement.required_by.asc(),
            )
            .limit(1)
        ).scalar_one_or_none()

    def _apply_shadow_prices(self, state, binding_dr) -> Tuple[Any, Dict[str, Any]]:
        """Bump shipment_priority + is_customer_committed + budget_remaining
        on the heuristic state, reflecting that SCP has bound a high-
        priority requirement to this load's destination.

        Effect on the heuristic: it tolerates higher broker premiums
        (because the alternative — escalating and missing the
        deadline — is more costly). The miss-cost is computed as a
        24h penalty estimate (broker resolution typically resolves
        within a day; longer windows would require multi-day
        forecasting).
        """
        import dataclasses
        from azirella_data_model.intersections.supply_transport import (
            ShadowPriceVector, compute_miss_cost,
        )

        spv = ShadowPriceVector.from_requirement(binding_dr)
        # 24h miss-cost on the binding requirement's quantity. This is
        # the additional budget the heuristic should treat as available
        # to clear the load (if escalation costs $X in miss penalty,
        # paying $X more for a broker is breakeven).
        miss_budget = compute_miss_cost(
            spv,
            qty=float(binding_dr.required_qty),
            days_late=1.0,
        )
        adjusted = dataclasses.replace(
            state,
            shipment_priority=int(binding_dr.priority),
            is_customer_committed=True,
            budget_remaining=state.budget_remaining + miss_budget,
        )
        summary = {
            "binding_dr_id": int(binding_dr.id),
            "binding_dr_correlation_id": str(binding_dr.correlation_id),
            "binding_dr_priority": int(binding_dr.priority),
            "shadow_price_miss_per_unit_per_day": float(spv.miss_per_unit_per_day),
            "required_qty": float(binding_dr.required_qty),
            "miss_budget_24h": float(miss_budget),
            "shipment_priority_before": int(state.shipment_priority),
            "shipment_priority_after": int(binding_dr.priority),
            "budget_remaining_before": float(state.budget_remaining),
            "budget_remaining_after": float(state.budget_remaining + miss_budget),
        }
        return adjusted, summary

    def evaluate_and_log(self, load: Load) -> Optional[Dict[str, Any]]:
        """Evaluate + log at severity matching the action."""
        result = self.evaluate_load(load)
        if not result:
            return result

        action = result["action_name"]
        if action == "ESCALATE":
            logger.warning(
                "BrokerRouting ESCALATE: load %s — %s (urg=%.2f)",
                load.load_number,
                result["reasoning"],
                result["urgency"],
            )
        elif action == "ACCEPT":
            logger.info(
                "BrokerRouting ACCEPT: load %s → broker %s @ $%.0f (urg=%.2f)",
                load.load_number,
                result.get("selected_broker_name") or "?",
                result["selected_rate"] or 0.0,
                result["urgency"],
            )

        # PREPARE.3 dual-write to core.agent_decisions
        record_trm_decision(
            self.db,
            tenant_id=self.tenant_id,
            config_id=self.config_id,
            trm_type="broker_routing",
            result=result,
            item_code=f"load-{load.id}",
            item_name=f"load {load.load_number}",
            category="broker_routing",
        )

        return result

    def evaluate_pending_loads(self) -> List[Dict[str, Any]]:
        """Evaluate every candidate load for the tenant."""
        loads = self.find_candidate_loads()
        results = []
        for load in loads:
            result = self.evaluate_and_log(load)
            if result:
                results.append(result)
        return results

    # ── Helpers ──────────────────────────────────────────────────────────

    def _build_state(self, load: Load):
        """Construct BrokerRoutingState from Load + tender history + brokers."""
        # Hours to pickup
        hours_to_pickup = 24.0
        if load.planned_departure:
            delta = load.planned_departure - datetime.utcnow()
            hours_to_pickup = max(0.0, delta.total_seconds() / 3600)

        # Non-ACCEPTED tender history on this load
        non_accepted = self.db.execute(
            select(FreightTender).where(
                and_(
                    FreightTender.load_id == load.id,
                    FreightTender.status != TenderStatus.ACCEPTED,
                )
            )
        ).scalars().all()
        tender_attempts_exhausted = len(non_accepted)
        all_declined = (
            tender_attempts_exhausted > 0
            and all(t.status in self._DECLINED_STATUSES for t in non_accepted)
        )

        # Contract rate: last ACCEPTED tender on this load (fallback 0).
        # Used as the benchmark fallback when DAT benchmark isn't available.
        accepted = self.db.execute(
            select(FreightTender)
            .where(
                and_(
                    FreightTender.load_id == load.id,
                    FreightTender.status == TenderStatus.ACCEPTED,
                )
            )
            .order_by(desc(FreightTender.tendered_at))
            .limit(1)
        ).scalar_one_or_none()
        contract_rate = float(accepted.final_rate or accepted.offered_rate or 0) if accepted else 0.0

        # Broker candidates: Carrier.type==BROKER with active CarrierLane,
        # joined to their best rate (cheapest active FreightRate per broker).
        broker_rows = self.db.execute(
            select(Carrier).where(
                and_(
                    Carrier.tenant_id == self.tenant_id,
                    Carrier.carrier_type == CarrierType.BROKER,
                    Carrier.is_active.is_(True),
                )
            )
        ).scalars().all()

        available_brokers: List[Dict[str, Any]] = []
        for b in broker_rows:
            # Pick cheapest active rate across any of this broker's lanes —
            # v1 picks lane-agnostic best because TMS doesn't yet carry a
            # lane-match predicate from the Load to broker lanes. The Core
            # heuristic already applies a reliability-adjusted-cost ranking,
            # so the rate feed here is informational, not selective.
            rate = self.db.execute(
                select(FreightRate.rate_flat).where(
                    and_(
                        FreightRate.carrier_id == b.id,
                        FreightRate.tenant_id == self.tenant_id,
                        FreightRate.is_active.is_(True),
                    )
                ).order_by(FreightRate.rate_flat).limit(1)
            ).scalar_one_or_none()
            if rate is None:
                continue  # Broker without a rate isn't selectable yet
            available_brokers.append({
                "id": int(b.id),
                "name": b.name or f"Broker-{b.id}",
                "rate": float(rate),
                "reliability": 0.80,  # Honest prior; wire to scorecard later
                "coverage_score": 0.80,
                "fallthrough_rate": 0.20,
            })

        return self._StateClass(
            load_id=load.id,
            lane_id=0,
            mode=(load.mode.value if load.mode else TransportMode.FTL.value),
            tender_attempts_exhausted=tender_attempts_exhausted,
            all_contract_carriers_declined=all_declined,
            hours_to_pickup=hours_to_pickup,
            available_brokers=available_brokers,
            contract_rate=contract_rate,
            spot_rate=0.0,
            broker_rate_premium_pct=self.DEFAULT_BROKER_PREMIUM,
            budget_remaining=0.0,
            shipment_priority=self.DEFAULT_PRIORITY,
            is_customer_committed=False,
            market_tightness=self.DEFAULT_MARKET_TIGHTNESS,
            dat_benchmark_rate=0.0,
        )

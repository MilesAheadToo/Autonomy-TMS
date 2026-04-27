"""
CapacityPromiseTRM — Capacity-Promise Agent (SENSE phase)

The second TMS TRM wired end-to-end. Maps to SCP's `atp_executor` slot in
the Powell site agent but operates on Load-status promotion rather than
order-level ATP consumption.

Behaviour (v1, per docs/internal/plans/CAPACITY_PROMISE_TRM_DESIGN.md):
  Load (PLANNING)
    └─ CapacityPromiseTRM  →  ACCEPT / DEFER / REJECT
         ACCEPT   → Load.status = READY (unlocks FreightProcurementTRM)
         DEFER    → leave at PLANNING (re-evaluated next cycle)
         REJECT   → leave at PLANNING + structured warning log

No `REJECTED` LoadStatus value exists today (audit flagged this in the
design note); adding one is out of v1 scope. No persistence to a Powell
decision table — the design note concludes log-only until the
intersection-contract package ships PREPARE.3's core.agent_decisions
dual-write.

Uses the Core-hosted deterministic teacher (compute_tms_decision with
trm_type="capacity_promise") when no BC checkpoint is loaded. Checkpoint
loading is stubbed for now (no capacity-promise BC model has been
trained yet).
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.tms_entities import (
    Carrier, CarrierLane, FreightRate, FreightTender,
    Load, LoadStatus, TenderStatus, TransportMode,
)
from app.services.powell.agent_decision_writer import record_trm_decision
from app.services.powell.bc_checkpoint_loader import load_bc_checkpoint

logger = logging.getLogger(__name__)


class CapacityPromiseTRM:
    """
    Evaluates capacity-promise decisions for loads in PLANNING state.

    Lifecycle:
        trm = CapacityPromiseTRM(db_session, tenant_id, config_id)
        trm.load_checkpoint(path)  # optional — falls back to heuristic
        decisions = trm.evaluate_pending_loads()
    """

    # Defaults for signals TMS cannot source today — these match the
    # design note's "Feature-vector population" section. Replace with
    # real rollups as the lane_performance_actuals / carrier_capacity_state
    # feedback tables land (Sprint 1 PREPARE.5).
    DEFAULT_LANE_ACCEPTANCE = 0.85
    DEFAULT_PRIMARY_OTP = 0.93
    DEFAULT_MARKET_TIGHTNESS = 0.5
    DEFAULT_ALLOCATION_COMPLIANCE = 1.0
    DEFAULT_SPOT_PREMIUM = 0.0

    def __init__(self, db: Session, tenant_id: int, config_id: int):
        self.db = db
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._model = None

        from azirella_data_model.powell.tms.heuristic_library.dispatch import (
            compute_tms_decision,
        )
        from azirella_data_model.powell.tms.heuristic_library.base import (
            CapacityPromiseState,
        )
        self._compute_decision = compute_tms_decision
        self._StateClass = CapacityPromiseState

    def load_checkpoint(self, checkpoint_path: str) -> bool:
        """Load a trained BC checkpoint. Returns True on success."""
        ckpt = load_bc_checkpoint(checkpoint_path, "capacity_promise")
        if ckpt is None:
            return False
        self._model = ckpt
        return True

    def find_pending_loads(self) -> List[Load]:
        """Find loads in PLANNING status (candidates for capacity promise)."""
        return self.db.execute(
            select(Load).where(
                and_(
                    Load.tenant_id == self.tenant_id,
                    Load.status == LoadStatus.PLANNING,
                )
            ).order_by(Load.planned_departure.nullslast(), Load.id)
        ).scalars().all()

    def evaluate_load(self, load: Load) -> Optional[Dict[str, Any]]:
        """
        Evaluate capacity-promise decision for one load.

        Returns a decision dict with the action, reasoning, and the
        composite scoring detail. Does NOT mutate the load.
        """
        state = self._build_state(load)
        decision = self._compute_decision("capacity_promise", state)

        action_name = {
            0: "ACCEPT",
            1: "REJECT",
            2: "DEFER",
            3: "ESCALATE",
        }.get(decision.action, "UNKNOWN")

        return {
            "load_id": load.id,
            "load_number": load.load_number,
            "load_status": load.status.value,
            "lane_id": getattr(state, "lane_id", 0),
            "priority": state.priority,
            "requested_loads": state.requested_loads,
            "available_capacity": state.available_capacity(),
            "action": decision.action,
            "action_name": action_name,
            "composite_score": (
                decision.params_used.get("composite_score")
                if decision.params_used else None
            ),
            "confidence": decision.confidence,
            "urgency": decision.urgency,
            "reasoning": decision.reasoning,
            "decision_method": "trm_model" if self._model else "heuristic",
            "scoring_detail": decision.params_used,
        }

    def evaluate_and_promote(self, load: Load) -> Optional[Dict[str, Any]]:
        """Evaluate + advance Load.status on ACCEPT.

        ACCEPT → PLANNING → READY (unlocks FreightProcurementTRM)
        DEFER  → no change (load remains in PLANNING for next cycle)
        REJECT → no change; logs a structured warning. Adding a
                 LoadStatus.REJECTED enum value is a separate audit
                 (see design note).
        """
        result = self.evaluate_load(load)
        if not result:
            return result

        action = result["action"]
        if action == 0:  # ACCEPT
            load.status = LoadStatus.READY
            self.db.flush()
            logger.info(
                "CapacityPromise ACCEPT: load %s → READY (score=%s, conf=%.2f, urg=%.2f)",
                load.load_number,
                result.get("composite_score"),
                result["confidence"],
                result["urgency"],
            )
        elif action == 1:  # REJECT
            logger.warning(
                "CapacityPromise REJECT: load %s (score=%s, urg=%.2f) — %s",
                load.load_number,
                result.get("composite_score"),
                result["urgency"],
                result["reasoning"],
            )
        # DEFER (2) and any ESCALATE (3) path: leave status untouched.

        # PREPARE.3 dual-write to core.agent_decisions
        record_trm_decision(
            self.db,
            tenant_id=self.tenant_id,
            config_id=self.config_id,
            trm_type="capacity_promise",
            result=result,
            item_code=f"load-{load.id}",
            item_name=f"load {load.load_number}",
            category="capacity_promise",
            impact_description=result.get("reasoning") or None,
        )

        return result

    def evaluate_pending_loads(self) -> List[Dict[str, Any]]:
        """Evaluate every PLANNING load and persist any status changes."""
        loads = self.find_pending_loads()
        results = []
        for load in loads:
            result = self.evaluate_and_promote(load)
            if result:
                results.append(result)
        self.db.commit()
        return results

    # ── Helpers ──────────────────────────────────────────────────────────

    def _build_state(self, load: Load):
        """Construct CapacityPromiseState from load + lane context.

        Honest priors are used for market / carrier-performance signals
        that TMS cannot source today — see class-level DEFAULT_* constants
        and the design note's feature-vector population section.
        """
        # Committed & total capacity — rough rollup from CarrierLane.
        # Real capacity-state rollups will arrive with PREPARE.5's
        # carrier_capacity_state view.
        lane_rows = self.db.execute(
            select(CarrierLane).where(
                and_(
                    CarrierLane.tenant_id == self.tenant_id,
                    CarrierLane.is_active.is_(True),
                )
            )
        ).scalars().all()
        total_capacity = sum(
            int(getattr(cl, "max_volume_daily", 0) or 0) for cl in lane_rows
        )

        # Booked loads on the same day = Loads in READY / TENDERED / ASSIGNED
        # / IN_TRANSIT on the same origin-destination pair around the
        # planned_departure date. Use a single-day window for v1.
        booked = 0
        if load.planned_departure:
            day_start = load.planned_departure.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_end = day_start.replace(hour=23, minute=59, second=59)
            booked = self.db.execute(
                select(Load).where(
                    and_(
                        Load.tenant_id == self.tenant_id,
                        Load.origin_site_id == load.origin_site_id,
                        Load.destination_site_id == load.destination_site_id,
                        Load.status.in_([
                            LoadStatus.READY,
                            LoadStatus.TENDERED,
                            LoadStatus.ASSIGNED,
                            LoadStatus.IN_TRANSIT,
                        ]),
                        Load.planned_departure >= day_start,
                        Load.planned_departure <= day_end,
                    )
                )
            ).scalars().all()
            booked = len(booked)

        primary_available = bool(load.carrier_id) or len(lane_rows) > 0
        backup_count = max(0, len(lane_rows) - 1)

        # Default priority 3 if the Load doesn't carry one.
        priority = int(getattr(load, "priority", 3) or 3)

        # Cross-plane signal: read SCP-issued DeploymentRequirement
        # rows that pertain to this load's destination over the next
        # ~7 days. Any upward forecast adjustment in SCP shows up here
        # as future load pressure on the same lane endpoint. Helper
        # no-ops at Tier 0a (SCP not registered), so SCP-less TMS
        # deployments degrade cleanly to forecast_loads=0.
        forecast_loads, scp_priority_lift = self._scp_demand_signal(load)
        if scp_priority_lift is not None and scp_priority_lift < priority:
            # SCP requirement carries a higher priority than the load's
            # default — lift the load's priority so the heuristic /
            # TRM scoring weights this load accordingly.
            priority = scp_priority_lift

        return self._StateClass(
            shipment_id=load.id,
            lane_id=0,
            requested_date=load.planned_departure,
            requested_loads=1,
            mode=(load.mode.value if load.mode else TransportMode.FTL.value),
            priority=priority,
            committed_capacity=booked,
            total_capacity=total_capacity or 0,
            buffer_capacity=0,
            forecast_loads=forecast_loads,
            booked_loads=booked,
            primary_carrier_available=primary_available,
            backup_carriers_count=backup_count,
            spot_rate_premium_pct=self.DEFAULT_SPOT_PREMIUM,
            lane_acceptance_rate=self.DEFAULT_LANE_ACCEPTANCE,
            market_tightness=self.DEFAULT_MARKET_TIGHTNESS,
            primary_carrier_otp=self.DEFAULT_PRIMARY_OTP,
            allocation_compliance_pct=self.DEFAULT_ALLOCATION_COMPLIANCE,
        )

    def _scp_demand_signal(self, load: Load) -> tuple[int, Optional[int]]:
        """Read SCP-issued DeploymentRequirement rows relevant to this
        load and return (forecast_loads_count, max_priority_seen).

        Filters to rows whose ``dest_site_id`` matches the load's
        destination and whose ``required_by`` falls within ±7 days of
        the load's planned_departure. Each matching row counts as one
        forecast load on this lane — a proxy for the SCP-side demand
        pressure the carrier should size capacity around.

        Returns ``(0, None)`` when SCP isn't registered (Tier 0a) or
        when the load lacks a destination / planned_departure.
        """
        if not load.destination_site_id or not load.planned_departure:
            return (0, None)
        try:
            from datetime import timedelta as _td
            from azirella_data_model.intersections.supply_transport import (
                consume_deployment_requirements_if_live,
            )
            window_start = load.planned_departure - _td(days=7)
            rows = consume_deployment_requirements_if_live(
                self.db,
                tenant_id=self.tenant_id,
                # Leave config_id unset so the SUPPLY-plane registry
                # check matches the common tenant-wildcard
                # registration (PlaneRegistry._active_row uses exact
                # config_id matching, no wildcard fallback).
                since=window_start,
                limit=200,
            )
            if not rows:
                return (0, None)
            window_end = load.planned_departure + _td(days=7)
            dest_match = str(load.destination_site_id)
            count = 0
            best_priority: Optional[int] = None
            for r in rows:
                # Filter on destination site + window. The contract
                # column is String(100); compare by string to avoid
                # int/string mismatches between SCP and TMS.
                if str(r.dest_site_id) != dest_match:
                    continue
                rby = r.required_by
                if rby is not None and (rby < window_start or rby > window_end):
                    continue
                count += 1
                p = int(getattr(r, "priority", 5) or 5)
                if best_priority is None or p < best_priority:
                    best_priority = p
            return (count, best_priority)
        except Exception as exc:
            logger.debug("SCP deployment-requirement read skipped: %s", exc)
            return (0, None)

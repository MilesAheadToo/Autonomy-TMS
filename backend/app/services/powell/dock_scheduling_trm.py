"""
DockSchedulingTRM — Dock-door appointment triage (PROTECT phase)

Sixth TMS-native TRM. First PROTECT-phase TRM in TMS. Maps to SCP's
`maintenance_scheduling` slot but operates on dock-door appointments
rather than equipment maintenance windows.

Evaluates open Appointment rows and classifies each into:
  ACCEPT   — nominal; schedule as requested (priority override or clean
             window).
  MODIFY   — elevated detention risk, queue congestion (convert live →
             drop-trailer), or expedite-turnaround recommendation.
  DEFER    — no compatible dock door available OR yard full; push to a
             later window.
  (ESCALATE — Core heuristic final fall-through when nothing else fits)

No Appointment.status mutation in v1 — the existing appointment-
workflow owns state transitions (REQUESTED → CONFIRMED → CHECKED_IN →
AT_DOCK → LOADING → COMPLETED). The TRM is an advisor that logs at
severity matching the recommended action; persistence to
core.agent_decisions lands with PREPARE.3 dual-write Sprint 1 Week 4-5.

Feature-vector sources (v1):
- facility_id / appointment_id / appointment_type ← Appointment
- total_dock_doors ← count(DockDoor where site_id + is_active)
- available_dock_doors ← total minus count(overlapping-window busy appts)
- requested_time ← Appointment.scheduled_start
- appointments_in_window ← count(other appts at same site in ±1 hr of
  scheduled_start, excluding COMPLETED/CANCELLED/NO_SHOW)
- current_queue_depth ← count(Appointment at site, status=CHECKED_IN)
- is_live_load ← AppointmentType ∈ {LIVE_LOAD, LIVE_UNLOAD}
- equipment_type ← Load.equipment_type (via load_id join) else DRY_VAN
- is_hazmat ← TMSShipment.is_hazmat (via shipment_id join) else False

Honest priors (TMS doesn't yet model yard capacity or detention
accounting at the facility level):
- yard_spots_total = 50, yard_spots_available = 20
- avg_dwell_time_minutes = 45.0
- free_time_minutes = 120.0, detention_rate_per_hour = 75.0
- carrier_avg_dwell_minutes = 90.0  (below free time → default low risk;
  real values need CarrierScorecard.avg_dwell aggregation)
- estimated_load_time_minutes = 60.0
- shipment_priority = 3
- required_door_type = "BOTH"

Wire these up as yard-capacity + detention tracking land in later
sprints (not blocking).
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.tms_entities import (
    Appointment,
    AppointmentStatus,
    AppointmentType,
    DockDoor,
    EquipmentType,
    Load,
    TMSShipment,
)


from app.services.powell.agent_decision_writer import record_trm_decision
logger = logging.getLogger(__name__)



class DockSchedulingTRM:
    """Evaluates dock-door appointment decisions for open appointments."""

    DEFAULT_YARD_TOTAL = 50
    DEFAULT_YARD_AVAILABLE = 20
    DEFAULT_AVG_DWELL_MIN = 45.0
    DEFAULT_FREE_TIME_MIN = 120.0
    DEFAULT_DETENTION_PER_HR = 75.0
    DEFAULT_CARRIER_DWELL_MIN = 90.0
    DEFAULT_LOAD_TIME_MIN = 60.0
    DEFAULT_PRIORITY = 3

    # Status set considered "in-window / busy" for door-availability calc.
    _BUSY_STATUSES = {
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.CHECKED_IN,
        AppointmentStatus.AT_DOCK,
        AppointmentStatus.LOADING,
        AppointmentStatus.UNLOADING,
    }

    # Status set considered a real-world "open" candidate for evaluation.
    _OPEN_STATUSES = [
        AppointmentStatus.REQUESTED,
        AppointmentStatus.CONFIRMED,
    ]

    def __init__(self, db: Session, tenant_id: int, config_id: int):
        self.db = db
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._model = None

        from autonomy_tms_heuristics.library.dispatch import (
            compute_tms_decision,
        )
        from autonomy_tms_heuristics.library.base import (
            DockSchedulingState,
        )
        self._compute_decision = compute_tms_decision
        self._StateClass = DockSchedulingState

    def load_checkpoint(self, checkpoint_path: str) -> bool:
        """Load a trained BC checkpoint. Returns True on success."""
        ckpt = load_bc_checkpoint(checkpoint_path, "dock_scheduling")
        if ckpt is None:
            return False
        self._model = ckpt
        return True

    def find_open_appointments(self) -> List[Appointment]:
        """Open appointments (REQUESTED or CONFIRMED) for the tenant."""
        return self.db.execute(
            select(Appointment).where(
                and_(
                    Appointment.tenant_id == self.tenant_id,
                    Appointment.status.in_(self._OPEN_STATUSES),
                )
            ).order_by(Appointment.scheduled_start.nullslast(), Appointment.id)
        ).scalars().all()

    def evaluate_appointment(self, appt: Appointment) -> Optional[Dict[str, Any]]:
        """Evaluate one appointment. Never mutates DB."""
        state = self._build_state(appt)

        action_name_map = {
            0: "ACCEPT", 1: "REJECT", 2: "DEFER", 3: "ESCALATE", 4: "MODIFY",
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
                    "appointment_id": appt.id,
                    "site_id": appt.site_id,
                    "appointment_type": state.appointment_type,
                    "status": appt.status.value,
                    "scheduled_start": (
                        appt.scheduled_start.isoformat() if appt.scheduled_start else None
                    ),
                    "total_doors": state.total_dock_doors,
                    "available_doors": state.available_dock_doors,
                    "queue_depth": state.current_queue_depth,
                    "utilization_pct": 0.0,
                    "detention_risk": 0.0,
                    "projected_detention_cost": 0.0,
                    "action": action,
                    "action_name": action_name,
                    "recommendation": None,
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
                    "DockScheduling BC inference failed (falling back "
                    "to heuristic): %s", e,
                )

        decision = self._compute_decision("dock_scheduling", state)

        action_name = action_name_map.get(decision.action, "UNKNOWN")

        scoring = decision.params_used or {}
        recommendation = scoring.get("recommendation")

        return {
            "appointment_id": appt.id,
            "site_id": appt.site_id,
            "appointment_type": state.appointment_type,
            "status": appt.status.value,
            "scheduled_start": (
                appt.scheduled_start.isoformat() if appt.scheduled_start else None
            ),
            "total_doors": state.total_dock_doors,
            "available_doors": state.available_dock_doors,
            "queue_depth": state.current_queue_depth,
            "utilization_pct": round(
                scoring.get("utilization", 0.0) * 100, 1
            ),
            "detention_risk": round(scoring.get("detention_risk", 0.0), 3),
            "projected_detention_cost": scoring.get("projected_detention_cost", 0.0),
            "action": decision.action,
            "action_name": action_name,
            "recommendation": recommendation,
            "confidence": decision.confidence,
            "urgency": decision.urgency,
            "reasoning": decision.reasoning,
            "decision_method": "heuristic",
            "scoring_detail": scoring,
        }

    def evaluate_and_log(self, appt: Appointment) -> Optional[Dict[str, Any]]:
        """Evaluate + log at severity matching the action."""
        result = self.evaluate_appointment(appt)
        if not result:
            return result

        action = result["action_name"]
        if action in ("ESCALATE", "MODIFY", "DEFER"):
            logger.warning(
                "DockScheduling %s: appt %d (site %d, %s) — %s",
                action,
                appt.id,
                appt.site_id,
                result["appointment_type"],
                result["reasoning"],
            )
        else:
            logger.info(
                "DockScheduling %s: appt %d (site %d, util=%.0f%%)",
                action,
                appt.id,
                appt.site_id,
                result["utilization_pct"],
            )

        # PREPARE.3 dual-write to core.agent_decisions
        record_trm_decision(
            self.db,
            tenant_id=self.tenant_id,
            config_id=self.config_id,
            trm_type="dock_scheduling",
            result=result,
            item_code=f"appt-{appt.id}",
            item_name=f"{appt.appointment_type} at dock {appt.dock_door_id}",
            category="dock_scheduling",
        )

        return result

    def evaluate_pending_appointments(self) -> List[Dict[str, Any]]:
        """Evaluate every open appointment for the tenant."""
        appts = self.find_open_appointments()
        results = []
        for appt in appts:
            result = self.evaluate_and_log(appt)
            if result:
                results.append(result)
        return results

    # ── Helpers ──────────────────────────────────────────────────────────

    def _build_state(self, appt: Appointment):
        """Construct DockSchedulingState from Appointment + facility + joins."""
        # Facility dock-door capacity
        total_doors = self.db.execute(
            select(func.count(DockDoor.id)).where(
                and_(
                    DockDoor.site_id == appt.site_id,
                    DockDoor.tenant_id == self.tenant_id,
                    DockDoor.is_active.is_(True),
                )
            )
        ).scalar_one() or 0

        # Available doors = total minus count of busy appts in a ±1h window
        # around this appointment's scheduled_start. Conservative proxy —
        # real availability needs a calendar intersection.
        busy_now = 0
        if appt.scheduled_start:
            window_start = appt.scheduled_start - timedelta(hours=1)
            window_end = appt.scheduled_start + timedelta(hours=1)
            busy_now = self.db.execute(
                select(func.count(Appointment.id)).where(
                    and_(
                        Appointment.site_id == appt.site_id,
                        Appointment.id != appt.id,
                        Appointment.status.in_(self._BUSY_STATUSES),
                        Appointment.scheduled_start <= window_end,
                        Appointment.scheduled_end >= window_start,
                    )
                )
            ).scalar_one() or 0
        available_doors = max(0, int(total_doors) - int(busy_now))

        # Appointments in the wider 2hr window (shift context for congestion)
        appts_in_window = 0
        if appt.scheduled_start:
            two_hr_start = appt.scheduled_start - timedelta(hours=2)
            two_hr_end = appt.scheduled_start + timedelta(hours=2)
            appts_in_window = self.db.execute(
                select(func.count(Appointment.id)).where(
                    and_(
                        Appointment.site_id == appt.site_id,
                        Appointment.id != appt.id,
                        Appointment.status.notin_([
                            AppointmentStatus.COMPLETED,
                            AppointmentStatus.CANCELLED,
                            AppointmentStatus.NO_SHOW,
                        ]),
                        Appointment.scheduled_start <= two_hr_end,
                        Appointment.scheduled_start >= two_hr_start,
                    )
                )
            ).scalar_one() or 0

        # Queue depth: trucks actively CHECKED_IN at this site
        queue_depth = self.db.execute(
            select(func.count(Appointment.id)).where(
                and_(
                    Appointment.site_id == appt.site_id,
                    Appointment.status == AppointmentStatus.CHECKED_IN,
                )
            )
        ).scalar_one() or 0

        # Live-load semantic
        is_live_load = appt.appointment_type in (
            AppointmentType.LIVE_LOAD,
            AppointmentType.LIVE_UNLOAD,
            AppointmentType.PICKUP,
            AppointmentType.DELIVERY,
        )

        # Equipment + hazmat context from associated Load / Shipment
        equipment_type = "DRY_VAN"
        is_hazmat = False
        if appt.load_id:
            load = self.db.execute(
                select(Load).where(Load.id == appt.load_id)
            ).scalar_one_or_none()
            if load and load.equipment_type:
                equipment_type = load.equipment_type.value
        if appt.shipment_id:
            shipment = self.db.execute(
                select(TMSShipment).where(TMSShipment.id == appt.shipment_id)
            ).scalar_one_or_none()
            if shipment:
                is_hazmat = bool(getattr(shipment, "is_hazmat", False))

        return self._StateClass(
            facility_id=appt.site_id,
            appointment_id=appt.id,
            appointment_type=(
                appt.appointment_type.value
                if appt.appointment_type else "DELIVERY"
            ),
            total_dock_doors=int(total_doors),
            available_dock_doors=available_doors,
            yard_spots_total=self.DEFAULT_YARD_TOTAL,
            yard_spots_available=self.DEFAULT_YARD_AVAILABLE,
            requested_time=appt.scheduled_start,
            earliest_available_slot=appt.scheduled_start,
            latest_acceptable_slot=appt.scheduled_end,
            appointments_in_window=int(appts_in_window),
            avg_dwell_time_minutes=self.DEFAULT_AVG_DWELL_MIN,
            current_queue_depth=int(queue_depth),
            shipment_priority=self.DEFAULT_PRIORITY,
            is_live_load=is_live_load,
            estimated_load_time_minutes=self.DEFAULT_LOAD_TIME_MIN,
            free_time_minutes=self.DEFAULT_FREE_TIME_MIN,
            detention_rate_per_hour=self.DEFAULT_DETENTION_PER_HR,
            carrier_avg_dwell_minutes=self.DEFAULT_CARRIER_DWELL_MIN,
            required_door_type="BOTH",
            equipment_type=equipment_type,
            is_hazmat=is_hazmat,
            commodity_type="",
        )

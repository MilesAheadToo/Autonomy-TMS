"""
IntermodalTransferTRM — Mode-Shift Eligibility & Economic Viability (BUILD)

Tenth TMS-native TRM. Completes the BUILD phase alongside LoadBuildTRM (#9).

Evaluates whether a TMSShipment currently planned for truck (FTL / LTL)
should shift to intermodal (truck+rail / rail-carload / ocean). Emits
ACCEPT (proceed with intermodal) or REJECT (stay on truck) based on
the Oracle-OTM / J.B. Hunt 360 pattern.

Trigger entity: `TMSShipment` (typically DRAFT or PLANNED status, before
carrier tender). One evaluation per shipment per request. Intermodal
evaluation is an option-pricing question — the planner or upstream
orchestration supplies the candidate intermodal route + rate, and this
TRM says GO / NO-GO.

v1 design note: the intermodal network (ramp catalog, intermodal
rate-sheet, ramp-congestion feed) is not yet in TMS. The service
therefore accepts the intermodal-specific inputs as explicit overrides
on the endpoint — matching how Oracle OTM's mode-shift evaluator is
called from the optimizer. When the intermodal network lands in Sprint 2,
the state-builder will fill these in from canonical state.

Hard gates (heuristic REJECTs immediately):

1. Hazmat freight (rail restrictions) → REJECT
2. Temperature-controlled (limited reefer intermodal availability) → REJECT
3. Ramp distance > 100 miles either end (drayage kills economics) → REJECT
4. Lane < 500 miles (below intermodal breakeven) → REJECT
5. Ramp congestion > 70% → REJECT
6. Transit time exceeds delivery window → REJECT
7. Reliability < 80% with window < 2 days → REJECT

Soft evaluation:

- Drayage-decomposed all-in cost vs truck rate, with inventory
  carrying-cost adjustment (44,000 lb × 10%/365 × penalty_days).
- 8% savings threshold (5% on long-haul > 800 mi) to ACCEPT.

Observational v1 — no TMSShipment mutation. ACCEPT decisions are logged
at INFO severity; REJECT at INFO with rationale. The mode-change
write-path lands alongside PREPARE.3's dual-write to core.agent_decisions
with decision_type=INTERMODAL_TRANSFER.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.intermodal_network import (
    IntermodalRate, RampCongestionSnapshot, SpotRateSnapshot,
)
from app.models.tms_entities import TMSShipment, TransportMode
from app.models.transportation_config import LaneProfile
from app.services.powell.agent_decision_writer import record_trm_decision

logger = logging.getLogger(__name__)



class IntermodalTransferTRM:
    """
    Evaluates truck→intermodal mode shift for a candidate shipment.

    Lifecycle:
        trm = IntermodalTransferTRM(db_session, tenant_id, config_id)
        decision = trm.evaluate_shipment(shipment, overrides=...)

    `overrides` carries the intermodal-specific inputs the planner
    supplies: ramp proximity, intermodal rate, transit times, etc.
    Missing overrides default to values that fail the gates (i.e., the
    heuristic REJECTs unless the caller provides a credible alternative)
    — the conservative default when network data is not yet wired.

    No TMSShipment mutation in v1.
    """

    # Defaults chosen to REJECT by default when overrides are missing:
    # ramp distance > 100 mi fails gate 3.
    _DEFAULT_ORIGIN_RAMP_MILES = 9999.0
    _DEFAULT_DEST_RAMP_MILES = 9999.0
    _DEFAULT_INTERMODAL_RELIABILITY = 0.85
    _DEFAULT_RAMP_CONGESTION = 0.3

    def __init__(self, db: Session, tenant_id: int, config_id: int):
        self.db = db
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._model = None

        from azirella_data_model.powell.tms.heuristic_library.dispatch import (
            compute_tms_decision,
        )
        from azirella_data_model.powell.tms.heuristic_library.base import (
            IntermodalTransferState,
        )
        self._compute_decision = compute_tms_decision
        self._StateClass = IntermodalTransferState

    def load_checkpoint(self, checkpoint_path: str) -> bool:
        """Load a trained BC checkpoint. Returns True on success."""
        ckpt = load_bc_checkpoint(checkpoint_path, "intermodal_transfer")
        if ckpt is None:
            return False
        self._model = ckpt
        return True

    def _find_binding_deployment_requirement(
        self, shipment: TMSShipment,
    ):
        """Look up the highest-priority active DeploymentRequirement
        whose dest matches the shipment's destination AND whose
        required_by falls within the shipment's delivery window.

        Returns the canonical Core DR row or None when no DR exists.
        Reads from the shared intersection table; works regardless of
        which plane emitted the DR (for now SCP-only, but design
        admits other emitters).

        Lookup is intentionally coarse for v1:
          * matches on dest_site_id (string-compared, since the
            intersection table column is String(100) per AD-11)
          * does NOT match on product_id (a shipment carries multiple
            products via load_items; product-aware DR matching is
            v2 scope)
          * filters required_by between now and the shipment's
            requested_delivery_date

        When multiple DRs match, returns the one with the highest
        priority integer (most urgent), then earliest required_by as
        tiebreaker.
        """
        try:
            from azirella_data_model.intersections.supply_transport import (
                DeploymentRequirement,
            )
        except ImportError:
            return None
        if not shipment.destination_site_id:
            return None
        now = datetime.utcnow()
        deadline = shipment.requested_delivery_date or (now + timedelta(days=30))
        return self.db.execute(
            select(DeploymentRequirement).where(
                DeploymentRequirement.tenant_id == self.tenant_id,
                DeploymentRequirement.dest_site_id == str(shipment.destination_site_id),
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

    def evaluate_shipment(
        self,
        shipment: TMSShipment,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate mode shift for one shipment. Never mutates it.

        Args:
            shipment: the candidate TMSShipment (any status — typically
                DRAFT/PLANNED, before carrier tender).
            overrides: intermodal-specific inputs the planner supplies
                (see `_build_state` for the full key list). Missing keys
                default to gate-failing values so the heuristic REJECTs
                in the absence of network data.
        """
        state = self._build_state(shipment, overrides or {})

        # AD-11 cross-plane: look up SCP-emitted DeploymentRequirement
        # for this shipment's destination. When found, the shadow-price
        # vector adjusts the heuristic's intermodal_rate input — the
        # extra transit days × required_qty × shadow_price_miss flows
        # into the cost the heuristic compares against truck. This is
        # how SCP's service-objective propagates into TMS's mode-shift
        # decision.
        binding_dr = self._find_binding_deployment_requirement(shipment)
        spv_summary: Dict[str, Any] = {}
        if binding_dr is not None:
            state, spv_summary = self._apply_shadow_prices(state, binding_dr)

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
                    "shipment_id": shipment.id,
                    "shipment_number": shipment.shipment_number,
                    "current_mode": state.current_mode,
                    "candidate_mode": state.candidate_mode,
                    "truck_rate": state.truck_rate,
                    "intermodal_rate": state.intermodal_rate,
                    "total_truck_miles": state.total_truck_miles,
                    "origin_ramp_distance_miles": state.origin_ramp_distance_miles,
                    "dest_ramp_distance_miles": state.dest_ramp_distance_miles,
                    "truck_transit_days": state.truck_transit_days,
                    "intermodal_transit_days": state.intermodal_transit_days,
                    "delivery_window_days": state.delivery_window_days,
                    "cost_savings_pct": state.cost_savings_pct(),
                    "transit_penalty_days": state.transit_time_penalty_days(),
                    "is_hazmat": state.is_hazmat,
                    "is_temperature_controlled": state.is_temperature_controlled,
                    "action": action,
                    "action_name": action_name,
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
                    "IntermodalTransfer BC inference failed (falling back "
                    "to heuristic): %s", e,
                )

        decision = self._compute_decision("intermodal_transfer", state)

        action_name = action_name_map.get(decision.action, "UNKNOWN")

        return {
            "shipment_id": shipment.id,
            "shipment_number": shipment.shipment_number,
            "current_mode": state.current_mode,
            "candidate_mode": state.candidate_mode,
            "truck_rate": state.truck_rate,
            "intermodal_rate": state.intermodal_rate,
            "total_truck_miles": state.total_truck_miles,
            "origin_ramp_distance_miles": state.origin_ramp_distance_miles,
            "dest_ramp_distance_miles": state.dest_ramp_distance_miles,
            "truck_transit_days": state.truck_transit_days,
            "intermodal_transit_days": state.intermodal_transit_days,
            "delivery_window_days": state.delivery_window_days,
            "cost_savings_pct": state.cost_savings_pct(),
            "transit_penalty_days": state.transit_time_penalty_days(),
            "is_hazmat": state.is_hazmat,
            "is_temperature_controlled": state.is_temperature_controlled,
            "action": decision.action,
            "action_name": action_name,
            "confidence": decision.confidence,
            "urgency": decision.urgency,
            "reasoning": decision.reasoning,
            "decision_method": "heuristic",
            "scoring_detail": decision.params_used,
            "shadow_price_adjustment": spv_summary,  # AD-11 cross-plane signal
        }

    def _apply_shadow_prices(self, state, binding_dr) -> Tuple[Any, Dict[str, Any]]:
        """Adjust the intermodal_rate input to the heuristic to
        incorporate the cost-of-miss implied by the binding DR.

        Intermodal mode is typically slower than truck. When SCP has
        issued a high-priority DeploymentRequirement for this
        destination, the extra intermodal transit days carry a
        miss-cost = ``qty × days × shadow_price_miss``. Folding that
        cost into ``intermodal_rate`` makes the heuristic compare
        truck-cost vs intermodal-cost-with-miss-penalty cleanly,
        without modifying the heuristic itself.

        Conversely, when intermodal is FASTER than truck (rare but
        possible — air-intermodal etc.), the savings show up as an
        earliness-bonus that REDUCES intermodal_rate.

        Returns ``(adjusted_state, summary_dict)``. The summary dict
        is attached to the result so observers can see why the
        intermodal_rate moved (correlation_id, original rate, miss
        adjustment, final rate).
        """
        import dataclasses
        from azirella_data_model.intersections.supply_transport import (
            ShadowPriceVector,
            compute_earliness_bonus,
            compute_miss_cost,
        )

        spv = ShadowPriceVector.from_requirement(binding_dr)
        days_delta = state.intermodal_transit_days - state.truck_transit_days
        adjustment = 0.0

        if days_delta > 0:
            # Intermodal is slower → miss-cost penalty
            adjustment = compute_miss_cost(
                spv,
                qty=float(binding_dr.required_qty),
                days_late=days_delta,
            )
            new_intermodal_rate = state.intermodal_rate + adjustment
        elif days_delta < 0:
            # Intermodal is faster → earliness bonus
            bonus = compute_earliness_bonus(
                spv,
                qty=float(binding_dr.required_qty),
                days_early=abs(days_delta),
            )
            adjustment = -bonus
            new_intermodal_rate = max(0.0, state.intermodal_rate + adjustment)
        else:
            new_intermodal_rate = state.intermodal_rate

        adjusted = dataclasses.replace(
            state, intermodal_rate=new_intermodal_rate,
        )
        summary = {
            "binding_dr_id": int(binding_dr.id),
            "binding_dr_correlation_id": str(binding_dr.correlation_id),
            "binding_dr_priority": int(binding_dr.priority),
            "shadow_price_miss_per_unit_per_day": float(spv.miss_per_unit_per_day),
            "shadow_price_earliness_per_unit_per_day": float(
                spv.earliness_per_unit_per_day
            ),
            "required_qty": float(binding_dr.required_qty),
            "transit_days_delta": float(days_delta),
            "intermodal_rate_before": float(state.intermodal_rate),
            "intermodal_rate_after": float(new_intermodal_rate),
            "rate_adjustment": float(adjustment),
        }
        return adjusted, summary

    def evaluate_and_log(
        self,
        shipment: TMSShipment,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate + log. No DB write in v1."""
        result = self.evaluate_shipment(shipment, overrides=overrides)
        if not result:
            return result

        action_name = result["action_name"]
        logger.info(
            "IntermodalTransfer %s: shipment %s — %s (savings=%.1f%%, urg=%.2f)",
            action_name,
            shipment.shipment_number,
            result["reasoning"],
            (result["cost_savings_pct"] or 0.0) * 100,
            result["urgency"],
        )

        # PREPARE.3 dual-write to core.agent_decisions
        record_trm_decision(
            self.db,
            tenant_id=self.tenant_id,
            config_id=self.config_id,
            trm_type="intermodal_transfer",
            result=result,
            item_code=f"shipment-{shipment.id}",
            item_name=f"shipment {shipment.shipment_number}",
            category="intermodal_transfer",
            impact_description=result.get("reasoning") or None,
        )

        # AD-11 cross-plane: write a DispatchCommitment row when the
        # decision ACCEPTs the intermodal route. SCP reads these from
        # the Core intersection table to update its in-transit MPS.
        # Helper no-ops at Tier 0a (SCP not registered) — caller never
        # branches on the registry.
        #
        # Uses a synthetic transfer_order_id (`shipment-{id}`) until
        # the shipment-to-TransferOrder linkage wires through. Per
        # AD-11 §5.3 the column is String(100) keying to whatever the
        # canonical TO identifier is in this tenant; the synthetic
        # form lets the row land cleanly today, and the synth can be
        # rewritten to the canonical TO id once that linkage exists.
        if action_name == "ACCEPT":
            self._emit_dispatch_commitment(shipment, result)

        return result

    def _emit_dispatch_commitment(
        self,
        shipment: TMSShipment,
        result: Dict[str, Any],
    ) -> None:
        """Emit a DispatchCommitment for an ACCEPTed intermodal route.

        Pickup window = (requested_pickup_date, +2h) — a placeholder
        until the planner-supplied pickup window threads through. ETA
        bands are computed from the shipment's requested_delivery_date
        +/- the conformal spread; if no delivery date, defaults to
        intermodal_transit_days from the result.
        """
        from datetime import timedelta
        from azirella_data_model.intersections.supply_transport import (
            DispatchState,
            emit_dispatch_commitment_if_live,
        )

        # Pickup window — narrow band around the requested pickup
        pickup_start = (
            shipment.requested_pickup_date
            or datetime.utcnow()
        )
        pickup_end = pickup_start + timedelta(hours=2)

        # ETA bands. Use the result's intermodal_transit_days as the
        # P50 spine; conservative ±20% as the P10/P90 spread until
        # conformal calibration runs publish per-lane bands.
        transit_days_p50 = float(result.get("intermodal_transit_days") or 3.0)
        eta_p50 = pickup_start + timedelta(days=transit_days_p50)
        eta_p10 = pickup_start + timedelta(days=transit_days_p50 * 0.8)
        eta_p90 = pickup_start + timedelta(days=transit_days_p50 * 1.2)

        # Carrier id — not directly on the shipment in v1; if there's
        # an accepted FreightTender for this shipment's load, use that.
        # Fallback to the shipment's primary carrier if set.
        carrier_id = getattr(shipment, "carrier_id", None)

        emit_dispatch_commitment_if_live(
            self.db,
            tenant_id=self.tenant_id,
            correlation_id=f"intermodal-shipment-{shipment.id}",
            transfer_order_id=f"shipment-{shipment.id}",  # synthetic — see caller comment
            carrier_id=str(carrier_id) if carrier_id else "UNKNOWN",
            load_id=str(getattr(shipment, "load_id", "") or ""),
            pickup_window_start=pickup_start,
            pickup_window_end=pickup_end,
            eta_p10=eta_p10,
            eta_p50=eta_p50,
            eta_p90=eta_p90,
            state=DispatchState.COMMITTED,
            config_id=self.config_id,
        )

    # ── State-builder helpers ────────────────────────────────────────────

    def _latest_truck_spot_rate(
        self, shipment: TMSShipment, mode: TransportMode
    ) -> Optional[float]:
        """Most recent spot_rate_snapshot for this lane × mode (any source).

        Lookup strategy: lane_id match first, then (origin, destination)
        fallback. Returns the rate_per_load of the most recent valid_at
        snapshot, or None when no data is wired yet.
        """
        # Prefer lane_id-keyed match
        if shipment.lane_id:
            row = self.db.execute(
                select(SpotRateSnapshot).where(
                    SpotRateSnapshot.tenant_id == self.tenant_id,
                    SpotRateSnapshot.lane_id == shipment.lane_id,
                    SpotRateSnapshot.mode == mode,
                ).order_by(SpotRateSnapshot.valid_at.desc()).limit(1)
            ).scalar_one_or_none()
            if row:
                return float(row.rate_per_load)

        # Fall back to (origin, destination) keying
        if shipment.origin_site_id and shipment.destination_site_id:
            row = self.db.execute(
                select(SpotRateSnapshot).where(
                    SpotRateSnapshot.tenant_id == self.tenant_id,
                    SpotRateSnapshot.origin_site_id == shipment.origin_site_id,
                    SpotRateSnapshot.destination_site_id == shipment.destination_site_id,
                    SpotRateSnapshot.mode == mode,
                ).order_by(SpotRateSnapshot.valid_at.desc()).limit(1)
            ).scalar_one_or_none()
            if row:
                return float(row.rate_per_load)
        return None

    def _resolve_intermodal_rate(
        self,
        candidate_mode_str: str,
        valid_on: Optional[datetime] = None,
    ) -> Optional[IntermodalRate]:
        """Cheapest active intermodal_rate for this tenant on the given mode.

        Without ramp coordinates wired into Sites, we don't yet do
        (origin_ramp, destination_ramp) resolution. v1 picks the cheapest
        active rate for the mode — sufficient for the planner-supplied-
        overrides path. The lookup tightens to per-route once
        site→ramp linkage data lands.
        """
        try:
            mode_enum = TransportMode(candidate_mode_str)
        except ValueError:
            return None

        as_of = valid_on or datetime.utcnow()
        return self.db.execute(
            select(IntermodalRate).where(
                IntermodalRate.tenant_id == self.tenant_id,
                IntermodalRate.mode == mode_enum,
                IntermodalRate.is_active.is_(True),
                IntermodalRate.valid_from <= as_of.date(),
                IntermodalRate.valid_to >= as_of.date(),
            ).order_by(IntermodalRate.rate_per_load.asc()).limit(1)
        ).scalar_one_or_none()

    def _latest_ramp_congestion(self, ramp_id: int) -> Optional[float]:
        """Most recent congestion_level for a ramp, or None."""
        row = self.db.execute(
            select(RampCongestionSnapshot.congestion_level).where(
                RampCongestionSnapshot.tenant_id == self.tenant_id,
                RampCongestionSnapshot.ramp_id == ramp_id,
            ).order_by(RampCongestionSnapshot.snapshot_at.desc()).limit(1)
        ).scalar_one_or_none()
        return float(row) if row is not None else None

    def _build_state(
        self, shipment: TMSShipment, overrides: Dict[str, Any]
    ):
        """Construct IntermodalTransferState from shipment + overrides + lane.

        Overrides (planner-supplied intermodal-specific inputs):
            candidate_mode: str                     (default: RAIL_INTERMODAL)
            origin_to_ramp_miles: float
            ramp_to_ramp_miles: float
            ramp_to_dest_miles: float
            truck_rate: float                       (falls back to shipment.estimated_cost)
            intermodal_rate: float
            drayage_rate_origin: float
            drayage_rate_dest: float
            truck_transit_days: float               (falls back to lane.p50_transit_days)
            intermodal_transit_days: float
            origin_ramp_distance_miles: float       (default 9999 → REJECT)
            dest_ramp_distance_miles: float         (default 9999 → REJECT)
            ramp_congestion_level: float            (default 0.3)
            intermodal_reliability_pct: float       (default 0.85)
            rail_capacity_available: bool           (default True)
            weather_risk_score: float               (default 0.0)
        """
        # Route miles — prefer LaneProfile.distance_miles when shipment.lane_id known
        total_truck_miles = 0.0
        truck_transit_days_from_lane = 0.0
        if shipment.lane_id:
            lp = self.db.execute(
                select(LaneProfile).where(
                    LaneProfile.lane_id == shipment.lane_id,
                    LaneProfile.config_id == self.config_id,
                )
            ).scalar_one_or_none()
            if lp:
                total_truck_miles = float(lp.distance_miles or 0.0)
                truck_transit_days_from_lane = float(lp.p50_transit_days or lp.avg_transit_days or 0.0)

        # Delivery window days — requested_delivery − requested_pickup in days
        delivery_window_days = 0.0
        if shipment.requested_pickup_date and shipment.requested_delivery_date:
            delta = shipment.requested_delivery_date - shipment.requested_pickup_date
            delivery_window_days = max(0.0, delta.total_seconds() / 86400.0)

        # Commodity value per lb for inventory-carrying-cost
        commodity_value_per_lb = 0.0
        if shipment.declared_value and shipment.weight and shipment.weight > 0:
            commodity_value_per_lb = float(shipment.declared_value) / float(shipment.weight)

        current_mode = (
            shipment.mode.value if getattr(shipment.mode, "value", None) else str(shipment.mode or "FTL")
        )
        candidate_mode_str = str(overrides.get("candidate_mode", "RAIL_INTERMODAL"))

        # ── DB-backed defaults (populated by Sprint-2 ratesheet ingest) ──
        # When tables are empty, these all return None and the override
        # path with its conservative-REJECT defaults takes over. When
        # data is wired, an override-less call gets sane fallbacks.
        truck_spot = None
        intermodal_rate_db: Optional[IntermodalRate] = None
        try:
            current_mode_enum = TransportMode(current_mode)
            truck_spot = self._latest_truck_spot_rate(shipment, current_mode_enum)
        except ValueError:
            pass

        intermodal_rate_db = self._resolve_intermodal_rate(candidate_mode_str)

        # Default truck rate: override > spot_rate_snapshot > shipment.estimated_cost
        truck_rate_default = (
            truck_spot
            if truck_spot is not None
            else float(shipment.estimated_cost or 0.0)
        )

        # Default intermodal rate / transit / reliability: override > intermodal_rate row > 0
        intermodal_rate_default = (
            float(intermodal_rate_db.rate_per_load) if intermodal_rate_db else 0.0
        )
        intermodal_transit_default = (
            float(intermodal_rate_db.transit_days_p50) if intermodal_rate_db else 0.0
        )
        intermodal_reliability_default = (
            float(intermodal_rate_db.reliability_pct)
            if intermodal_rate_db and intermodal_rate_db.reliability_pct is not None
            else self._DEFAULT_INTERMODAL_RELIABILITY
        )

        # Ramp congestion: override > most-recent snapshot for the
        # planner-named ramps > class default. Until the ramp catalog
        # has site-coordinate linkage we can't pick the ramps from the
        # shipment alone — leave that resolution to the override caller.
        congestion_origin = self._latest_ramp_congestion(
            int(overrides["origin_ramp_id"])
        ) if overrides.get("origin_ramp_id") else None
        congestion_dest = self._latest_ramp_congestion(
            int(overrides["destination_ramp_id"])
        ) if overrides.get("destination_ramp_id") else None
        congestion_default = max(
            v for v in (congestion_origin, congestion_dest)
            if v is not None
        ) if (congestion_origin is not None or congestion_dest is not None) \
            else self._DEFAULT_RAMP_CONGESTION

        return self._StateClass(
            shipment_id=shipment.id,
            current_mode=current_mode,
            candidate_mode=candidate_mode_str,
            origin_to_ramp_miles=float(overrides.get("origin_to_ramp_miles", 0.0)),
            ramp_to_ramp_miles=float(overrides.get("ramp_to_ramp_miles", 0.0)),
            ramp_to_dest_miles=float(overrides.get("ramp_to_dest_miles", 0.0)),
            total_truck_miles=float(overrides.get("total_truck_miles", total_truck_miles)),
            truck_rate=float(overrides.get("truck_rate", truck_rate_default)),
            intermodal_rate=float(overrides.get("intermodal_rate", intermodal_rate_default)),
            drayage_rate_origin=float(overrides.get("drayage_rate_origin", 0.0)),
            drayage_rate_dest=float(overrides.get("drayage_rate_dest", 0.0)),
            truck_transit_days=float(overrides.get(
                "truck_transit_days", truck_transit_days_from_lane
            )),
            intermodal_transit_days=float(overrides.get(
                "intermodal_transit_days", intermodal_transit_default
            )),
            delivery_window_days=float(overrides.get(
                "delivery_window_days", delivery_window_days
            )),
            rail_capacity_available=bool(overrides.get("rail_capacity_available", True)),
            ramp_congestion_level=float(overrides.get(
                "ramp_congestion_level", congestion_default
            )),
            intermodal_reliability_pct=float(overrides.get(
                "intermodal_reliability_pct", intermodal_reliability_default
            )),
            weather_risk_score=float(overrides.get("weather_risk_score", 0.0)),
            is_hazmat=bool(shipment.is_hazmat),
            is_temperature_controlled=bool(shipment.is_temperature_sensitive),
            commodity_value_per_lb=commodity_value_per_lb,
            origin_ramp_distance_miles=float(overrides.get(
                "origin_ramp_distance_miles", self._DEFAULT_ORIGIN_RAMP_MILES
            )),
            dest_ramp_distance_miles=float(overrides.get(
                "dest_ramp_distance_miles", self._DEFAULT_DEST_RAMP_MILES
            )),
        )

"""
LoadBuildTRM — Shipment→Load consolidation (BUILD phase)

Seventh TMS-native TRM. Maps to SCP's `mo_execution` slot but operates
on freight consolidation — "should these N shipments travel as one FTL
load?" — rather than manufacturing-order execution.

Action space (from Core dispatch):
  ACCEPT      — route single shipment as LTL / accept current FTL plan
  CONSOLIDATE — combine N shipments into one multi-stop load
  SPLIT       — over-capacity, delivery-window conflict, or >max_stops
  DEFER       — underutilized single shipment; hold for consolidation
                window (default 24h)
  REJECT      — hazmat / temperature incompatibility

No TMSShipment.status mutation in v1. The authoritative Load
creation belongs to a separate workflow; this TRM advises the
CONSOLIDATE decision. Log-only (PREPARE.3 dual-write lands Sprint 1
Week 4-5).

Trigger grouping: DRAFT shipments clustered by
(origin_site_id, destination_site_id, mode, date(requested_pickup_date)).
Each group is one TRM evaluation. A group of size 1 still evaluates —
the TRM can return ACCEPT (LTL-best) or DEFER (underutilized, wait
for more).

Feature-vector sources (v1):
- shipment_ids / lane_id / mode / equipment_type ← group
- total_weight / total_volume / total_pallets / shipment_count ← SUM
- has_hazmat_conflict ← any shipment in group hazmat=True AND mixed
- has_temp_conflict ← any shipment is_temperature_sensitive AND temp
  ranges incompatible within the group
- earliest_pickup / latest_pickup ← MIN/MAX of requested_pickup_date
- stop_count ← COUNT(DISTINCT destination_site_id) in group
- delivery_windows_compatible ← latest_delivery span ≤ 24h
- ftl_rate ← FreightRate.rate_flat for this lane+mode (honest fallback
  $2500 matches FreightProcurement seed)
- ltl_rate_sum ← SUM(weight/100 × DEFAULT_LTL_PER_CWT) per shipment
- consolidation_savings ← max(0, ltl_rate_sum - ftl_rate)

Honest defaults (wire up as data/configuration arrives):
- max_weight = 44000 lbs, max_volume = 2700 cuft, max_pallets = 26
  (FTL 53ft dry-van standard)
- max_stops = 3 (industry consolidation norm)
- stop_off_charge_per_stop = $75
- consolidation_window_hours = 24
- ltl_class_rate_per_cwt = 0.25 ($/cwt × 100 lbs = $25 per 10K lbs base)
- volume_ltl_rate = 0.0
"""
import logging
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.tms_entities import (
    EquipmentType,
    FreightRate,
    ShipmentStatus,
    TMSShipment,
    TransportMode,
)
from app.services.powell.agent_decision_writer import record_trm_decision

logger = logging.getLogger(__name__)



class LoadBuildTRM:
    """Shipment-group consolidation evaluator."""

    # FTL 53ft dry-van capacity (industry standard)
    FTL_MAX_WEIGHT = 44000.0
    FTL_MAX_VOLUME = 2700.0
    FTL_MAX_PALLETS = 26

    # Consolidation policy
    MAX_STOPS = 3
    CONSOLIDATION_WINDOW_HOURS = 24.0
    STOP_OFF_CHARGE = 75.0

    # Rate fallbacks
    DEFAULT_FTL_RATE = 2500.0
    # $/cwt = $/100 lbs. Class-70 general freight runs ~$20-30/cwt at
    # median distance on a dry-van lane; $25 is the midpoint. Earlier
    # prior of 0.25 was 100× too low — priors were interpreted "per lb"
    # which made every single-shipment group fall below FTL breakeven
    # even at 40K lbs. Keep units explicit: this is dollars per
    # hundredweight, i.e. per 100 lbs.
    DEFAULT_LTL_PER_CWT = 25.0

    def __init__(self, db: Session, tenant_id: int, config_id: int):
        self.db = db
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._model = None

        from azirella_data_model.powell.tms.heuristic_library.dispatch import (
            compute_tms_decision,
        )
        from azirella_data_model.powell.tms.heuristic_library.base import (
            LoadBuildState,
        )
        self._compute_decision = compute_tms_decision
        self._StateClass = LoadBuildState

    def load_checkpoint(self, checkpoint_path: str) -> bool:
        """Load a trained BC checkpoint. Returns True on success."""
        ckpt = load_bc_checkpoint(checkpoint_path, "load_build")
        if ckpt is None:
            return False
        self._model = ckpt
        return True

    def find_draft_groups(self) -> List[Dict[str, Any]]:
        """Group DRAFT shipments by (origin, destination, mode, pickup-day).

        Returns a list of group descriptors, each carrying the shipment
        IDs plus the grouping key. Groups with a single shipment are
        retained — the TRM can still return LTL-accept or DEFER on them.
        """
        rows = self.db.execute(
            select(TMSShipment).where(
                and_(
                    TMSShipment.tenant_id == self.tenant_id,
                    TMSShipment.status == ShipmentStatus.DRAFT,
                )
            )
        ).scalars().all()

        groups: Dict[tuple, List[TMSShipment]] = {}
        for s in rows:
            pickup_day = (
                s.requested_pickup_date.date()
                if s.requested_pickup_date else None
            )
            key = (
                s.origin_site_id,
                s.destination_site_id,
                s.mode.value if s.mode else "FTL",
                pickup_day,
            )
            groups.setdefault(key, []).append(s)

        return [
            {
                "origin_site_id": k[0],
                "destination_site_id": k[1],
                "mode": k[2],
                "pickup_date": k[3],
                "shipments": v,
            }
            for k, v in groups.items()
        ]

    def evaluate_group(
        self,
        shipments: List[TMSShipment],
        origin_site_id: int,
        destination_site_id: int,
        mode: str,
        pickup_date: Optional[date],
    ) -> Optional[Dict[str, Any]]:
        """Evaluate consolidation decision for a shipment group."""
        if not shipments:
            return None

        state = self._build_state(
            shipments, origin_site_id, destination_site_id, mode
        )

        action_name_map = {
            0: "ACCEPT", 1: "REJECT", 2: "DEFER", 3: "ESCALATE",
            4: "MODIFY", 5: "RETENDER", 6: "REROUTE",
            7: "CONSOLIDATE", 8: "SPLIT",
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
                    "origin_site_id": origin_site_id,
                    "destination_site_id": destination_site_id,
                    "mode": mode,
                    "pickup_date": pickup_date.isoformat() if pickup_date else None,
                    "shipment_ids": [s.id for s in shipments],
                    "shipment_count": len(shipments),
                    "total_weight": state.total_weight,
                    "total_volume": state.total_volume,
                    "total_pallets": state.total_pallets,
                    "weight_util_pct": 0.0,
                    "volume_util_pct": 0.0,
                    "stop_count": state.stop_count,
                    "has_hazmat_conflict": state.has_hazmat_conflict,
                    "has_temp_conflict": state.has_temp_conflict,
                    "ftl_rate": state.ftl_rate,
                    "ltl_rate_sum": state.ltl_rate_sum,
                    "consolidation_savings": state.consolidation_savings,
                    "optimal_mode": None,
                    "total_savings": None,
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
                }
            except Exception as e:
                logger.warning(
                    "LoadBuild BC inference failed (falling back "
                    "to heuristic): %s", e,
                )

        decision = self._compute_decision("load_build", state)

        action_name = action_name_map.get(decision.action, "UNKNOWN")

        scoring = decision.params_used or {}

        return {
            "origin_site_id": origin_site_id,
            "destination_site_id": destination_site_id,
            "mode": mode,
            "pickup_date": pickup_date.isoformat() if pickup_date else None,
            "shipment_ids": [s.id for s in shipments],
            "shipment_count": len(shipments),
            "total_weight": state.total_weight,
            "total_volume": state.total_volume,
            "total_pallets": state.total_pallets,
            "weight_util_pct": round(scoring.get("weight_util", 0) * 100, 1),
            "volume_util_pct": round(scoring.get("volume_util", 0) * 100, 1),
            "stop_count": state.stop_count,
            "has_hazmat_conflict": state.has_hazmat_conflict,
            "has_temp_conflict": state.has_temp_conflict,
            "ftl_rate": state.ftl_rate,
            "ltl_rate_sum": state.ltl_rate_sum,
            "consolidation_savings": state.consolidation_savings,
            "optimal_mode": scoring.get("optimal_mode"),
            "total_savings": scoring.get("total_savings"),
            "action": decision.action,
            "action_name": action_name,
            "confidence": decision.confidence,
            "urgency": decision.urgency,
            "reasoning": decision.reasoning,
            "decision_method": "heuristic",
            "scoring_detail": scoring,
        }

    def evaluate_and_log(
        self,
        shipments: List[TMSShipment],
        origin_site_id: int,
        destination_site_id: int,
        mode: str,
        pickup_date: Optional[date],
    ) -> Optional[Dict[str, Any]]:
        """Evaluate + log."""
        result = self.evaluate_group(
            shipments, origin_site_id, destination_site_id, mode, pickup_date
        )
        if not result:
            return result

        action = result["action_name"]
        if action in ("CONSOLIDATE", "SPLIT", "REJECT"):
            logger.warning(
                "LoadBuild %s: origin=%s→dest=%s mode=%s ships=%d — %s",
                action,
                origin_site_id,
                destination_site_id,
                mode,
                result["shipment_count"],
                result["reasoning"],
            )
        else:
            logger.info(
                "LoadBuild %s: origin=%s→dest=%s ships=%d util_w=%.0f%% — %s",
                action,
                origin_site_id,
                destination_site_id,
                result["shipment_count"],
                result["weight_util_pct"],
                result["reasoning"],
            )

        # PREPARE.3 dual-write to core.agent_decisions
        record_trm_decision(
            self.db,
            tenant_id=self.tenant_id,
            config_id=self.config_id,
            trm_type="load_build",
            result=result,
            item_code=f"group-{origin_site_id}-{destination_site_id}-{mode}",
            item_name=(
                f"{result['shipment_count']} shipments "
                f"{origin_site_id}→{destination_site_id} ({mode})"
            ),
            category="load_build",
            impact_description=result.get("reasoning") or None,
        )

        return result

    def evaluate_all_groups(self) -> List[Dict[str, Any]]:
        """Evaluate every DRAFT group for the tenant."""
        groups = self.find_draft_groups()
        results = []
        for g in groups:
            result = self.evaluate_and_log(
                g["shipments"],
                g["origin_site_id"],
                g["destination_site_id"],
                g["mode"],
                g["pickup_date"],
            )
            if result:
                results.append(result)
        return results

    # ── Helpers ──────────────────────────────────────────────────────────

    def _build_state(
        self,
        shipments: List[TMSShipment],
        origin_site_id: int,
        destination_site_id: int,
        mode: str,
    ):
        """Construct LoadBuildState from a shipment group."""
        total_weight = sum(float(s.weight or 0) for s in shipments)
        total_volume = sum(float(s.volume or 0) for s in shipments)
        total_pallets = sum(int(s.pallet_count or 0) for s in shipments)
        count = len(shipments)

        # Hazmat conflict: any hazmat row in a mixed group
        hazmat_count = sum(1 for s in shipments if s.is_hazmat)
        has_hazmat_conflict = 0 < hazmat_count < count

        # Temp conflict: any reefer shipment in a group that also has
        # non-reefer shipments, OR incompatible temp ranges among reefers.
        reefers = [s for s in shipments if s.is_temperature_sensitive]
        has_temp_conflict = (
            (0 < len(reefers) < count)
            or self._temp_ranges_incompatible(reefers)
        )

        # Multi-stop: unique destination count (all shipments in this
        # group share destination_site_id by construction, so stop_count
        # is 1 here. For a future multi-lane grouping the stop-count
        # would vary — leave the plumbing in place.)
        stop_count = len({s.destination_site_id for s in shipments}) or 1

        # Pickup window
        pickups = [s.requested_pickup_date for s in shipments if s.requested_pickup_date]
        earliest = min(pickups) if pickups else None
        latest = max(pickups) if pickups else None

        # Delivery windows compatible if all latest_delivery span ≤ 24h
        deliveries = [
            getattr(s, "latest_delivery", None) or getattr(s, "requested_delivery_date", None)
            for s in shipments
        ]
        deliveries = [d for d in deliveries if d]
        delivery_compat = True
        if len(deliveries) >= 2:
            span = (max(deliveries) - min(deliveries)).total_seconds() / 3600
            delivery_compat = span <= 24

        # Equipment type: majority wins, else DRY_VAN
        equip_counts: Dict[str, int] = {}
        for s in shipments:
            key = s.required_equipment.value if s.required_equipment else "DRY_VAN"
            equip_counts[key] = equip_counts.get(key, 0) + 1
        equipment_type = (
            max(equip_counts, key=equip_counts.get) if equip_counts else "DRY_VAN"
        )

        # Rate lookup: cheapest active FreightRate on this lane (by flat),
        # fallback to default. TMS TransportationLane IDs aren't directly
        # resolvable from (origin, destination) without a join; skip
        # lookup for v1 and use the default (matches FreightProcurement
        # seed $2500 prior). Wire up to TransportationLane resolution
        # when the LoadBuild → TMSShipment.lane_id link is populated in
        # the seed.
        ftl_rate = self.DEFAULT_FTL_RATE

        # LTL rate sum: $0.25 per 100 lbs per shipment, crude industry prior
        ltl_rate_sum = sum(
            (float(s.weight or 0) / 100.0) * self.DEFAULT_LTL_PER_CWT
            for s in shipments
        )

        consolidation_savings = max(0.0, ltl_rate_sum - ftl_rate) if count > 1 else 0.0
        avg_weight = total_weight / count if count else 0.0

        return self._StateClass(
            shipment_ids=[s.id for s in shipments],
            lane_id=0,
            mode=mode,
            equipment_type=equipment_type,
            max_weight=self.FTL_MAX_WEIGHT,
            max_volume=self.FTL_MAX_VOLUME,
            max_pallets=self.FTL_MAX_PALLETS,
            total_weight=total_weight,
            total_volume=total_volume,
            total_pallets=total_pallets,
            shipment_count=count,
            has_hazmat_conflict=has_hazmat_conflict,
            has_temp_conflict=has_temp_conflict,
            has_destination_conflict=False,  # group is single-destination by key
            max_stops=self.MAX_STOPS,
            earliest_pickup=earliest,
            latest_pickup=latest,
            consolidation_window_hours=self.CONSOLIDATION_WINDOW_HOURS,
            ftl_rate=ftl_rate,
            ltl_rate_sum=round(ltl_rate_sum, 2),
            consolidation_savings=round(consolidation_savings, 2),
            stop_count=stop_count,
            stop_off_charge_per_stop=self.STOP_OFF_CHARGE,
            delivery_windows_compatible=delivery_compat,
            avg_weight_per_shipment=round(avg_weight, 2),
            ltl_class_rate_per_cwt=self.DEFAULT_LTL_PER_CWT,
            volume_ltl_rate=0.0,
        )

    @staticmethod
    def _temp_ranges_incompatible(reefers: List[TMSShipment]) -> bool:
        """True if the reefer-cluster's temperature ranges don't overlap."""
        if len(reefers) < 2:
            return False
        # Intersect all [temp_min, temp_max] ranges
        lo = max(float(s.temp_min) for s in reefers if s.temp_min is not None) \
            if any(s.temp_min is not None for s in reefers) else float("-inf")
        hi = min(float(s.temp_max) for s in reefers if s.temp_max is not None) \
            if any(s.temp_max is not None for s in reefers) else float("inf")
        return lo > hi

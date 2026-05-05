"""
EquipmentRepositionTRM — Empty-Vehicle Redistribution (REFLECT phase)

Eleventh and final TMS-native TRM. Closes the phase cycle
SENSE → ASSESS → ACQUIRE → PROTECT → BUILD → REFLECT.

Evaluates (source_site, target_site, equipment_type) candidate
repositioning pairs and decides: HOLD (no move) or REPOSITION (move N
units of equipment) based on the Powell/Crainic EVR framework with
ROI gating.

Trigger: an Equipment-balance snapshot across the network. Typically
called from the daily reposition scheduler (post-LoadBuild + post-tender
phases) — the scheduler pairs highest-surplus sites with
highest-deficit sites per equipment_type and this TRM says GO/NO-GO
per pair with a proposed quantity.

Heuristic signals (priority-ordered, from Core
`dispatch._compute_equipment_reposition`):

1. No surplus at source → HOLD
2. No deficit at target → HOLD
3. Fleet utilization > 90% + deficit → urgent REPOSITION (urg 0.8)
4. ROI > 1.5× → REPOSITION (urg 0.5)
5. ROI > 1.0× and reposition_miles < 200 → short REPOSITION (urg 0.3)
6. Otherwise → HOLD (ROI below threshold)

State-builder derives the equipment-balance inputs from canonical
state:

- source_equipment_count ← COUNT(Equipment) at source site, type=X,
    status='AVAILABLE'
- source_demand_next_7d  ← COUNT(TMSShipment) originating from source
    in next 7 days with required_equipment=X
- target_equipment_count ← same at target
- target_demand_next_7d  ← same at target
- network_surplus_locations / network_deficit_locations ← per-site
    (available - demand_7d) counts across the tenant's sites
- total_fleet_size      ← COUNT(Equipment) type=X for tenant
- fleet_utilization_pct ← IN_USE / (AVAILABLE + IN_USE)

Reposition economics (planner-supplied overrides, with best-effort
defaults):

- reposition_miles       ← LaneProfile.distance_miles for (from, to)
    if present; else override or 0
- reposition_cost        ← override; default $2.20/mi on reposition_miles
- cost_of_not_repositioning ← override; Sprint 2 will pull spot-rate
    premium from ingestion layer
- breakeven_loads        ← override; default 1

Observational v1 — no EquipmentMove rows created. REPOSITION decisions
log at WARNING with the proposed move; a write path lands alongside
PREPARE.3's dual-write to core.agent_decisions with
decision_type=EQUIPMENT_REPOSITION.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.tms_entities import Equipment, EquipmentType, TMSShipment
from app.models.transportation_config import LaneProfile
from app.services.powell.agent_decision_writer import record_trm_decision

logger = logging.getLogger(__name__)



_DEFAULT_COST_PER_MILE = 2.20  # Industry midpoint reposition cost
_DEFAULT_DEMAND_WINDOW_DAYS = 7


class EquipmentRepositionTRM:
    """
    Evaluates empty-equipment repositioning per (source, target, type) pair.

    Lifecycle:
        trm = EquipmentRepositionTRM(db_session, tenant_id, config_id)
        # Single pair
        decision = trm.evaluate_pair(
            source_site_id=1, target_site_id=2,
            equipment_type="DRY_VAN",
            overrides={"reposition_miles": 340, ...},
        )
        # Network sweep (greedy surplus→deficit pairing per equipment_type)
        decisions = trm.evaluate_network(equipment_type="DRY_VAN")

    No EquipmentMove / Equipment.current_site_id mutation in v1.
    """

    def __init__(self, db: Session, tenant_id: int, config_id: int):
        self.db = db
        self.tenant_id = tenant_id
        self.config_id = config_id
        self._model = None

        from autonomy_tms_heuristics.library.dispatch import (
            compute_tms_decision,
        )
        from autonomy_tms_heuristics.library.base import (
            EquipmentRepositionState,
        )
        self._compute_decision = compute_tms_decision
        self._StateClass = EquipmentRepositionState

    def load_checkpoint(self, checkpoint_path: str) -> bool:
        """Load a trained BC checkpoint. Returns True on success."""
        ckpt = load_bc_checkpoint(checkpoint_path, "equipment_reposition")
        if ckpt is None:
            return False
        self._model = ckpt
        return True

    # ── Single-pair evaluation ──────────────────────────────────────────

    def evaluate_pair(
        self,
        source_site_id: int,
        target_site_id: int,
        equipment_type: str,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate one (source, target, type) repositioning pair.

        Args:
            source_site_id: site with suspected surplus
            target_site_id: site with suspected deficit
            equipment_type: EquipmentType value (e.g. "DRY_VAN", "REEFER")
            overrides: planner-supplied reposition-economics overrides
                (reposition_miles, reposition_cost, cost_of_not_repositioning,
                 breakeven_loads). Any missing keys fall back to
                best-effort defaults.
        """
        if source_site_id == target_site_id:
            return None

        state = self._build_state(
            source_site_id, target_site_id, equipment_type, overrides or {}
        )

        # Action space for this TRM: HOLD=10, REPOSITION=9 (Core constants).
        action_name_map = {
            0: "ACCEPT", 1: "REJECT", 2: "DEFER", 3: "ESCALATE",
            4: "MODIFY", 9: "REPOSITION", 10: "HOLD",
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
                    "source_site_id": source_site_id,
                    "target_site_id": target_site_id,
                    "equipment_type": equipment_type,
                    "source_equipment_count": state.source_equipment_count,
                    "source_demand_next_7d": state.source_demand_next_7d,
                    "target_equipment_count": state.target_equipment_count,
                    "target_demand_next_7d": state.target_demand_next_7d,
                    "source_surplus": state.source_surplus(),
                    "target_deficit": state.target_deficit(),
                    "reposition_miles": state.reposition_miles,
                    "reposition_cost": state.reposition_cost,
                    "cost_of_not_repositioning": state.cost_of_not_repositioning,
                    "roi": state.reposition_roi(),
                    "fleet_utilization_pct": state.fleet_utilization_pct,
                    "proposed_quantity": 0,
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
                    "EquipmentReposition BC inference failed (falling back "
                    "to heuristic): %s", e,
                )

        decision = self._compute_decision("equipment_reposition", state)

        action_name = action_name_map.get(decision.action, "UNKNOWN")

        proposed_qty = int(decision.quantity or 0)

        return {
            "source_site_id": source_site_id,
            "target_site_id": target_site_id,
            "equipment_type": equipment_type,
            "source_equipment_count": state.source_equipment_count,
            "source_demand_next_7d": state.source_demand_next_7d,
            "target_equipment_count": state.target_equipment_count,
            "target_demand_next_7d": state.target_demand_next_7d,
            "source_surplus": state.source_surplus(),
            "target_deficit": state.target_deficit(),
            "reposition_miles": state.reposition_miles,
            "reposition_cost": state.reposition_cost,
            "cost_of_not_repositioning": state.cost_of_not_repositioning,
            "roi": state.reposition_roi(),
            "fleet_utilization_pct": state.fleet_utilization_pct,
            "proposed_quantity": proposed_qty,
            "action": decision.action,
            "action_name": action_name,
            "confidence": decision.confidence,
            "urgency": decision.urgency,
            "reasoning": decision.reasoning,
            "decision_method": "heuristic",
            "scoring_detail": decision.params_used,
        }

    def evaluate_and_log(
        self,
        source_site_id: int,
        target_site_id: int,
        equipment_type: str,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate + log. REPOSITION → WARNING, HOLD → INFO."""
        result = self.evaluate_pair(
            source_site_id, target_site_id, equipment_type, overrides=overrides
        )
        if not result:
            return result

        action_name = result["action_name"]
        if action_name == "REPOSITION":
            logger.warning(
                "EquipmentReposition REPOSITION: %s %s→%s qty=%d (ROI %.1fx, %.0fmi) — %s",
                equipment_type,
                source_site_id,
                target_site_id,
                result["proposed_quantity"],
                result["roi"],
                result["reposition_miles"],
                result["reasoning"],
            )
        else:
            logger.info(
                "EquipmentReposition %s: %s %s→%s (surplus=%d, deficit=%d) — %s",
                action_name,
                equipment_type,
                source_site_id,
                target_site_id,
                result["source_surplus"],
                result["target_deficit"],
                result["reasoning"],
            )

        # PREPARE.3 dual-write to core.agent_decisions
        record_trm_decision(
            self.db,
            tenant_id=self.tenant_id,
            config_id=self.config_id,
            trm_type="equipment_reposition",
            result=result,
            item_code=f"{equipment_type}-{source_site_id}-{target_site_id}",
            item_name=(
                f"{equipment_type} reposition "
                f"{source_site_id}→{target_site_id}"
            ),
            category="equipment_reposition",
            impact_description=result.get("reasoning") or None,
        )

        return result

    # ── Network-level sweep ─────────────────────────────────────────────

    def evaluate_network(
        self,
        equipment_type: str,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Find all surplus sites and all deficit sites for this equipment
        type, pair them greedily (largest-surplus ↔ largest-deficit), and
        emit one decision per pair.

        Emits exactly `min(len(surplus_sites), len(deficit_sites))`
        decisions. No EquipmentMove writes. Missing reposition economics
        default per-pair from LaneProfile + the service's $/mi default.
        """
        surplus, deficit = self._rank_sites_by_balance(equipment_type)
        if not surplus or not deficit:
            return []

        results: List[Dict[str, Any]] = []
        for src_id, tgt_id in zip(
            (s[0] for s in surplus), (d[0] for d in deficit)
        ):
            r = self.evaluate_and_log(src_id, tgt_id, equipment_type, overrides=overrides)
            if r:
                results.append(r)
        return results

    # ── State-builder helpers ────────────────────────────────────────────

    def _build_state(
        self,
        source_site_id: int,
        target_site_id: int,
        equipment_type: str,
        overrides: Dict[str, Any],
    ):
        """Derive full EquipmentRepositionState from canonical + overrides."""
        eq_type_enum = self._eq_type_enum(equipment_type)

        source_on_hand = self._count_available_equipment(source_site_id, eq_type_enum)
        target_on_hand = self._count_available_equipment(target_site_id, eq_type_enum)

        source_demand = self._count_demand_next_window(
            source_site_id, eq_type_enum, _DEFAULT_DEMAND_WINDOW_DAYS
        )
        target_demand = self._count_demand_next_window(
            target_site_id, eq_type_enum, _DEFAULT_DEMAND_WINDOW_DAYS
        )

        total_fleet = self._count_fleet(eq_type_enum)
        in_use = self._count_in_use(eq_type_enum)
        fleet_util = (in_use / (total_fleet or 1)) if total_fleet else 0.0

        network_surplus, network_deficit = self._count_network_imbalance(eq_type_enum)

        # Reposition economics — overrides > lane-derived defaults > zero
        reposition_miles = float(overrides.get(
            "reposition_miles",
            self._lane_distance(source_site_id, target_site_id) or 0.0
        ))
        reposition_cost = float(overrides.get(
            "reposition_cost",
            reposition_miles * _DEFAULT_COST_PER_MILE
        ))
        cost_of_not = float(overrides.get("cost_of_not_repositioning", 0.0))
        breakeven = int(overrides.get("breakeven_loads", 1))
        transit_hours = float(overrides.get("reposition_transit_hours", reposition_miles / 50.0))

        return self._StateClass(
            equipment_type=equipment_type,
            source_facility_id=source_site_id,
            source_equipment_count=source_on_hand,
            source_demand_next_7d=source_demand,
            target_facility_id=target_site_id,
            target_equipment_count=target_on_hand,
            target_demand_next_7d=target_demand,
            reposition_miles=reposition_miles,
            reposition_cost=reposition_cost,
            reposition_transit_hours=transit_hours,
            network_surplus_locations=network_surplus,
            network_deficit_locations=network_deficit,
            total_fleet_size=total_fleet,
            fleet_utilization_pct=fleet_util,
            cost_of_not_repositioning=cost_of_not,
            breakeven_loads=breakeven,
        )

    def _eq_type_enum(self, equipment_type: str) -> EquipmentType:
        """Resolve string → EquipmentType enum; fall back to DRY_VAN."""
        try:
            return EquipmentType[equipment_type]
        except KeyError:
            for member in EquipmentType:
                if member.value == equipment_type:
                    return member
            return EquipmentType.DRY_VAN

    def _count_available_equipment(
        self, site_id: int, eq_type: EquipmentType
    ) -> int:
        """COUNT(Equipment) at site with status='AVAILABLE'."""
        count = self.db.execute(
            select(func.count(Equipment.id)).where(
                Equipment.tenant_id == self.tenant_id,
                Equipment.current_site_id == site_id,
                Equipment.equipment_type == eq_type,
                Equipment.status == "AVAILABLE",
                Equipment.is_active.is_(True),
            )
        ).scalar_one_or_none()
        return int(count or 0)

    def _count_demand_next_window(
        self, site_id: int, eq_type: EquipmentType, days: int
    ) -> int:
        """COUNT(TMSShipment) originating from site with required_equipment=X
        in the next `days` days (relative to requested_pickup_date)."""
        now = datetime.utcnow()
        horizon = now + timedelta(days=days)
        count = self.db.execute(
            select(func.count(TMSShipment.id)).where(
                TMSShipment.tenant_id == self.tenant_id,
                TMSShipment.origin_site_id == site_id,
                TMSShipment.required_equipment == eq_type,
                TMSShipment.requested_pickup_date >= now,
                TMSShipment.requested_pickup_date < horizon,
            )
        ).scalar_one_or_none()
        return int(count or 0)

    def _count_fleet(self, eq_type: EquipmentType) -> int:
        count = self.db.execute(
            select(func.count(Equipment.id)).where(
                Equipment.tenant_id == self.tenant_id,
                Equipment.equipment_type == eq_type,
                Equipment.is_active.is_(True),
            )
        ).scalar_one_or_none()
        return int(count or 0)

    def _count_in_use(self, eq_type: EquipmentType) -> int:
        count = self.db.execute(
            select(func.count(Equipment.id)).where(
                Equipment.tenant_id == self.tenant_id,
                Equipment.equipment_type == eq_type,
                Equipment.status == "IN_USE",
                Equipment.is_active.is_(True),
            )
        ).scalar_one_or_none()
        return int(count or 0)

    def _count_network_imbalance(
        self, eq_type: EquipmentType
    ) -> tuple[int, int]:
        """Count sites across the tenant with surplus or deficit for this type."""
        _, per_site_net = self._per_site_net_balance(eq_type)
        surplus_count = sum(1 for v in per_site_net.values() if v > 0)
        deficit_count = sum(1 for v in per_site_net.values() if v < 0)
        return surplus_count, deficit_count

    def _per_site_net_balance(
        self, eq_type: EquipmentType
    ) -> tuple[List[int], Dict[int, int]]:
        """Return (site_ids, net_balance_by_site) where net = available - demand_7d.
        Positive = surplus, negative = deficit.
        """
        # On-hand per site
        on_hand_rows = self.db.execute(
            select(Equipment.current_site_id, func.count(Equipment.id))
            .where(
                Equipment.tenant_id == self.tenant_id,
                Equipment.equipment_type == eq_type,
                Equipment.status == "AVAILABLE",
                Equipment.is_active.is_(True),
                Equipment.current_site_id.isnot(None),
            )
            .group_by(Equipment.current_site_id)
        ).all()
        on_hand = {row[0]: int(row[1]) for row in on_hand_rows}

        # Demand per site over next 7d
        now = datetime.utcnow()
        horizon = now + timedelta(days=_DEFAULT_DEMAND_WINDOW_DAYS)
        demand_rows = self.db.execute(
            select(TMSShipment.origin_site_id, func.count(TMSShipment.id))
            .where(
                TMSShipment.tenant_id == self.tenant_id,
                TMSShipment.required_equipment == eq_type,
                TMSShipment.requested_pickup_date >= now,
                TMSShipment.requested_pickup_date < horizon,
                TMSShipment.origin_site_id.isnot(None),
            )
            .group_by(TMSShipment.origin_site_id)
        ).all()
        demand = {row[0]: int(row[1]) for row in demand_rows}

        all_site_ids = set(on_hand) | set(demand)
        net = {s: on_hand.get(s, 0) - demand.get(s, 0) for s in all_site_ids}
        return sorted(all_site_ids), net

    def _rank_sites_by_balance(
        self, equipment_type: str
    ) -> tuple[List[tuple[int, int]], List[tuple[int, int]]]:
        """Return (surplus_list, deficit_list), each sorted by magnitude
        descending. Each entry is `(site_id, abs_imbalance)`.
        """
        eq_type_enum = self._eq_type_enum(equipment_type)
        _, net = self._per_site_net_balance(eq_type_enum)
        surplus = sorted(
            ((s, v) for s, v in net.items() if v > 0),
            key=lambda p: p[1], reverse=True,
        )
        deficit = sorted(
            ((s, -v) for s, v in net.items() if v < 0),
            key=lambda p: p[1], reverse=True,
        )
        return surplus, deficit

    def _lane_distance(
        self, source_site_id: int, target_site_id: int
    ) -> Optional[float]:
        """Best-effort miles lookup: check LaneProfile for a lane joining
        these sites. Returns None when no lane profile exists.
        """
        from app.models.supply_chain_config import TransportationLane

        lane = self.db.execute(
            select(TransportationLane).where(
                TransportationLane.from_site_id == source_site_id,
                TransportationLane.to_site_id == target_site_id,
            )
        ).scalar_one_or_none()
        if not lane:
            return None

        lp = self.db.execute(
            select(LaneProfile).where(
                LaneProfile.lane_id == lane.id,
                LaneProfile.config_id == self.config_id,
            )
        ).scalar_one_or_none()
        if not lp or not lp.distance_miles:
            return None
        return float(lp.distance_miles)

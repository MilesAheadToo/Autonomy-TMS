"""TMS TRM curricula — behavior-cloning training-data generators.

One curriculum per TMS-native TRM. Each curriculum inherits from Core's
plane-agnostic :class:`TRMCurriculumBase` and emits
:class:`CurriculumData` samples sized to that TRM's ``input_dim`` from
:data:`app.models.trm.MODEL_REGISTRY`.

Phase 2 of the TMS TRM rework. Replaces the previous SCP-copied
curriculum file that imported from the deleted SCP-style TRM model
classes. TMS-native curricula here generate state vectors matching
the actual BC checkpoint input dimensions on disk.

Label generation is intentionally lightweight — random per-feature
state vectors per phase, threshold-driven heuristic labels restricted
to each TRM's ``active_actions`` subset (the small canonical action
set actually meaningful for that TRM, recorded in each BC checkpoint's
metadata). Phase-3 disruption scenarios pull from Core's shared
disruption pool. Realistic training quality comes from Phase 4's
synthetic-tenant corpus generation, not from the heuristic depth here.

Backwards-compat exports: ``CURRICULUM_REGISTRY``, ``SCConfigData``,
``CurriculumData``, ``PHASE_SAMPLE_WEIGHTS`` are re-exported so the
existing TMS callers (``trm_site_trainer``, ``powell_training_service``,
``train_powell_models``, ``train_trm``) keep working unchanged.
"""
from __future__ import annotations

import numpy as np

from azirella_data_model.ml import (
    CurriculumData,
    PHASE_SAMPLE_WEIGHTS,
    SCConfigData,
    TRMCurriculumBase,
    pick_disruption,
)

from app.models.trm import (
    ACTION_NAMES,
    MODEL_REGISTRY,
)


__all__ = [
    # Re-exports for backwards-compat with existing TMS callers
    "CurriculumData",
    "SCConfigData",
    "PHASE_SAMPLE_WEIGHTS",
    "TRMCurriculumBase",
    # TMS-native curricula
    "BrokerRoutingCurriculum",
    "CapacityBufferCurriculum",
    "CapacityPromiseCurriculum",
    "DockSchedulingCurriculum",
    "EquipmentRepositionCurriculum",
    "ExceptionManagementCurriculum",
    "FreightProcurementCurriculum",
    "IntermodalTransferCurriculum",
    "LoadBuildCurriculum",
    "ShipmentTrackingCurriculum",
    # Registry — the public surface every caller uses
    "CURRICULUM_REGISTRY",
    "TMS_TRM_CURRICULUM_REGISTRY",
]


# Reward map per phase — higher phase = lower reward (harder samples).
_PHASE_REWARD_DISCOUNT = {1: 1.0, 2: 0.9, 3: 0.8}


# ---------------------------------------------------------------------------
# Base class — TMS-flavored helpers over Core's plane-agnostic base
# ---------------------------------------------------------------------------


class _TMSTRMCurriculumBase(TRMCurriculumBase):
    """TMS-flavored base — wires the per-TRM ``state_dim`` + ``active_actions``
    onto the Core abstract class.

    Subclasses override:
        - :attr:`trm_type` → canonical TMS TRM name (matches MODEL_REGISTRY key)
        - :attr:`active_actions` → tuple of action indices from
          :data:`ACTION_NAMES` that this TRM may emit
        - :meth:`_simple_state` / :meth:`_mixed_state` → numpy state
          vectors of length ``self.state_dim``
        - :meth:`_compute_label` → ``(action_idx, reward)`` where
          ``action_idx`` is in ``self.active_actions``
    """

    trm_type: str = ""
    active_actions: tuple[int, ...] = ()

    @property
    def state_dim(self) -> int:
        """Pulled from MODEL_REGISTRY — single source of truth for the
        TRM's input contract (matches the BC checkpoint's
        ``input_dim`` field)."""
        return MODEL_REGISTRY[self.trm_type][1]

    def _reward(self, phase: int, scale: float = 0.8) -> float:
        return scale * _PHASE_REWARD_DISCOUNT.get(phase, 0.8)

    def _pick_active(self) -> int:
        """Random action from this TRM's active set — used by phase-3
        disruption fallback when no specific rule applies."""
        return int(np.random.choice(self.active_actions))

    def generate(self, phase: int, num_samples: int) -> CurriculumData:
        n = num_samples
        states = np.zeros((n, self.state_dim), dtype=np.float32)
        actions = np.zeros(n, dtype=np.int64)
        rewards = np.zeros(n, dtype=np.float32)

        for i in range(n):
            if phase == 1:
                states[i] = self._simple_state()
                actions[i], rewards[i] = self._compute_label(states[i], phase)
            elif phase == 2:
                states[i] = self._mixed_state()
                actions[i], rewards[i] = self._compute_label(states[i], phase)
            else:
                states[i], actions[i], rewards[i] = self._phase3()

        return CurriculumData(
            state_vectors=states,
            action_discrete=actions,
            action_continuous=np.zeros((n, 1), dtype=np.float32),
            rewards=rewards,
            next_state_vectors=states * 0.95,
            is_expert=np.ones(n, dtype=bool),
            dones=np.zeros(n, dtype=bool),
        )

    def _phase3(self) -> tuple[np.ndarray, int, float]:
        """Default phase-3: pick a Core disruption, mutate the
        mixed state, fall back to baseline heuristic. Subclasses may
        override for TRM-specific disruption shaping."""
        _ = pick_disruption()  # disruption tag picked but not used in default
        s = self._disruption_state()
        action, reward = self._compute_label(s, 3)
        return s, action, reward

    def _simple_state(self) -> np.ndarray:
        raise NotImplementedError

    def _mixed_state(self) -> np.ndarray:
        raise NotImplementedError

    def _disruption_state(self) -> np.ndarray:
        # Default: noisy mixed state. Subclasses may override for
        # state-specific disruption patterns.
        s = self._mixed_state()
        noise = np.random.uniform(-0.3, 0.3, size=self.state_dim).astype(np.float32)
        return np.clip(s + noise, 0.0, None)

    def _compute_label(self, s: np.ndarray, phase: int) -> tuple[int, float]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Per-TRM curricula
# ---------------------------------------------------------------------------


class BrokerRoutingCurriculum(_TMSTRMCurriculumBase):
    """Broker-routing decisions. Actions: ACCEPT (0), ESCALATE (3).

    Heuristic: ESCALATE when broker reliability < 0.6 (first state
    feature acts as the proxy)."""
    trm_type = "broker_routing"
    active_actions = (ACTION_NAMES["ACCEPT"], ACTION_NAMES["ESCALATE"])

    def _simple_state(self) -> np.ndarray:
        return np.random.uniform(0.6, 0.95, self.state_dim).astype(np.float32)

    def _mixed_state(self) -> np.ndarray:
        return np.random.uniform(0.3, 0.85, self.state_dim).astype(np.float32)

    def _compute_label(self, s: np.ndarray, phase: int) -> tuple[int, float]:
        reliability = s[0]
        if reliability < 0.6:
            return ACTION_NAMES["ESCALATE"], self._reward(phase, 0.85)
        return ACTION_NAMES["ACCEPT"], self._reward(phase, 0.80)


class CapacityBufferCurriculum(_TMSTRMCurriculumBase):
    """Capacity-buffer adjustment. Actions: ACCEPT (0), MODIFY (4).

    Heuristic: MODIFY when utilisation > 0.85 OR variance > 0.3."""
    trm_type = "capacity_buffer"
    active_actions = (ACTION_NAMES["ACCEPT"], ACTION_NAMES["MODIFY"])

    def _simple_state(self) -> np.ndarray:
        return np.random.uniform(0.4, 0.7, self.state_dim).astype(np.float32)

    def _mixed_state(self) -> np.ndarray:
        return np.random.uniform(0.3, 0.9, self.state_dim).astype(np.float32)

    def _compute_label(self, s: np.ndarray, phase: int) -> tuple[int, float]:
        utilisation = s[0]
        variance = s[1] if self.state_dim > 1 else 0.0
        if utilisation > 0.85 or variance > 0.3:
            return ACTION_NAMES["MODIFY"], self._reward(phase, 0.85)
        return ACTION_NAMES["ACCEPT"], self._reward(phase, 0.80)


class CapacityPromiseCurriculum(_TMSTRMCurriculumBase):
    """Available-capacity-to-promise. Actions: ACCEPT (0), DEFER (2).

    Heuristic: DEFER when available < requested."""
    trm_type = "capacity_promise"
    active_actions = (ACTION_NAMES["ACCEPT"], ACTION_NAMES["DEFER"])

    def _simple_state(self) -> np.ndarray:
        s = np.random.uniform(0.5, 0.95, self.state_dim).astype(np.float32)
        s[0] = np.random.uniform(0.7, 1.0)
        s[1] = np.random.uniform(0.3, 0.6)
        return s

    def _mixed_state(self) -> np.ndarray:
        return np.random.uniform(0.2, 0.95, self.state_dim).astype(np.float32)

    def _compute_label(self, s: np.ndarray, phase: int) -> tuple[int, float]:
        available = s[0]
        requested = s[1] if self.state_dim > 1 else 0.0
        if available < requested:
            return ACTION_NAMES["DEFER"], self._reward(phase, 0.85)
        return ACTION_NAMES["ACCEPT"], self._reward(phase, 0.80)


class DockSchedulingCurriculum(_TMSTRMCurriculumBase):
    """Dock-appointment scheduling. Actions: ACCEPT (0), DEFER (2), MODIFY (4).

    Heuristic: DEFER when door availability is zero, MODIFY when window
    misaligned, ACCEPT otherwise."""
    trm_type = "dock_scheduling"
    active_actions = (
        ACTION_NAMES["ACCEPT"],
        ACTION_NAMES["DEFER"],
        ACTION_NAMES["MODIFY"],
    )

    def _simple_state(self) -> np.ndarray:
        return np.random.uniform(0.5, 0.9, self.state_dim).astype(np.float32)

    def _mixed_state(self) -> np.ndarray:
        return np.random.uniform(0.1, 0.95, self.state_dim).astype(np.float32)

    def _compute_label(self, s: np.ndarray, phase: int) -> tuple[int, float]:
        door_availability = s[0]
        window_alignment = s[1] if self.state_dim > 1 else 1.0
        if door_availability < 0.1:
            return ACTION_NAMES["DEFER"], self._reward(phase, 0.85)
        if window_alignment < 0.4:
            return ACTION_NAMES["MODIFY"], self._reward(phase, 0.80)
        return ACTION_NAMES["ACCEPT"], self._reward(phase, 0.78)


class EquipmentRepositionCurriculum(_TMSTRMCurriculumBase):
    """Equipment repositioning. Actions: REPOSITION (9), HOLD (10).

    Heuristic: REPOSITION when cost-of-not-repositioning > reposition_cost."""
    trm_type = "equipment_reposition"
    active_actions = (ACTION_NAMES["REPOSITION"], ACTION_NAMES["HOLD"])

    def _simple_state(self) -> np.ndarray:
        return np.random.uniform(0.3, 0.7, self.state_dim).astype(np.float32)

    def _mixed_state(self) -> np.ndarray:
        return np.random.uniform(0.1, 0.9, self.state_dim).astype(np.float32)

    def _compute_label(self, s: np.ndarray, phase: int) -> tuple[int, float]:
        cost_inaction = s[1] if self.state_dim > 1 else 0.5
        cost_action = s[5] if self.state_dim > 5 else 0.5
        if cost_inaction > cost_action:
            return ACTION_NAMES["REPOSITION"], self._reward(phase, 0.85)
        return ACTION_NAMES["HOLD"], self._reward(phase, 0.78)


class ExceptionManagementCurriculum(_TMSTRMCurriculumBase):
    """In-transit exception handling. Actions: ACCEPT (0), ESCALATE (3),
    RETENDER (5), REROUTE (6).

    Heuristic: severity-driven escalation ladder. ACCEPT when within
    buffer; REROUTE when severity high + can_reroute; RETENDER when
    severity moderate + can_retender; ESCALATE otherwise."""
    trm_type = "exception_management"
    active_actions = (
        ACTION_NAMES["ACCEPT"],
        ACTION_NAMES["ESCALATE"],
        ACTION_NAMES["RETENDER"],
        ACTION_NAMES["REROUTE"],
    )

    def _simple_state(self) -> np.ndarray:
        return np.random.uniform(0.2, 0.6, self.state_dim).astype(np.float32)

    def _mixed_state(self) -> np.ndarray:
        return np.random.uniform(0.1, 0.85, self.state_dim).astype(np.float32)

    def _compute_label(self, s: np.ndarray, phase: int) -> tuple[int, float]:
        severity = s[2] if self.state_dim > 2 else 0.0
        within_buffer = s[4] if self.state_dim > 4 else 1.0
        can_reroute = s[8] if self.state_dim > 8 else 0.0
        can_retender = s[9] if self.state_dim > 9 else 0.0

        if within_buffer > 0.5:
            return ACTION_NAMES["ACCEPT"], self._reward(phase, 0.80)
        if severity > 0.7 and can_reroute > 0.5:
            return ACTION_NAMES["REROUTE"], self._reward(phase, 0.85)
        if severity > 0.5 and can_retender > 0.5:
            return ACTION_NAMES["RETENDER"], self._reward(phase, 0.82)
        return ACTION_NAMES["ESCALATE"], self._reward(phase, 0.78)


class FreightProcurementCurriculum(_TMSTRMCurriculumBase):
    """Freight tendering. Actions: ACCEPT (0), ESCALATE (3).

    Heuristic: ESCALATE when primary acceptance pct < 0.5 OR spot
    premium > benchmark."""
    trm_type = "freight_procurement"
    active_actions = (ACTION_NAMES["ACCEPT"], ACTION_NAMES["ESCALATE"])

    def _simple_state(self) -> np.ndarray:
        return np.random.uniform(0.6, 0.95, self.state_dim).astype(np.float32)

    def _mixed_state(self) -> np.ndarray:
        return np.random.uniform(0.2, 0.9, self.state_dim).astype(np.float32)

    def _compute_label(self, s: np.ndarray, phase: int) -> tuple[int, float]:
        primary_pct = s[17] if self.state_dim > 17 else 0.7
        spot_premium = s[3] if self.state_dim > 3 else 0.0
        if primary_pct < 0.5 or spot_premium > 0.5:
            return ACTION_NAMES["ESCALATE"], self._reward(phase, 0.85)
        return ACTION_NAMES["ACCEPT"], self._reward(phase, 0.80)


class IntermodalTransferCurriculum(_TMSTRMCurriculumBase):
    """Mode-shift decision (truck ↔ rail ↔ ocean). Actions: ACCEPT (0),
    REJECT (1).

    Heuristic: ACCEPT mode shift when intermodal rate + reliability
    beat truck."""
    trm_type = "intermodal_transfer"
    active_actions = (ACTION_NAMES["ACCEPT"], ACTION_NAMES["REJECT"])

    def _simple_state(self) -> np.ndarray:
        return np.random.uniform(0.5, 0.9, self.state_dim).astype(np.float32)

    def _mixed_state(self) -> np.ndarray:
        return np.random.uniform(0.2, 0.95, self.state_dim).astype(np.float32)

    def _compute_label(self, s: np.ndarray, phase: int) -> tuple[int, float]:
        intermodal_rate = s[5] if self.state_dim > 5 else 0.5
        truck_rate = s[18] if self.state_dim > 18 else 0.5
        intermodal_reliability = s[6] if self.state_dim > 6 else 0.5
        if intermodal_rate < truck_rate and intermodal_reliability > 0.6:
            return ACTION_NAMES["ACCEPT"], self._reward(phase, 0.85)
        return ACTION_NAMES["REJECT"], self._reward(phase, 0.78)


class LoadBuildCurriculum(_TMSTRMCurriculumBase):
    """Outbound load construction. Actions: ACCEPT (0), REJECT (1),
    DEFER (2), CONSOLIDATE (7), SPLIT (8).

    Heuristic: REJECT on conflict; SPLIT when overweight; CONSOLIDATE
    when savings high; ACCEPT otherwise."""
    trm_type = "load_build"
    active_actions = (
        ACTION_NAMES["ACCEPT"],
        ACTION_NAMES["REJECT"],
        ACTION_NAMES["DEFER"],
        ACTION_NAMES["CONSOLIDATE"],
        ACTION_NAMES["SPLIT"],
    )

    def _simple_state(self) -> np.ndarray:
        return np.random.uniform(0.4, 0.8, self.state_dim).astype(np.float32)

    def _mixed_state(self) -> np.ndarray:
        return np.random.uniform(0.2, 0.95, self.state_dim).astype(np.float32)

    def _compute_label(self, s: np.ndarray, phase: int) -> tuple[int, float]:
        consolidation_savings = s[1] if self.state_dim > 1 else 0.0
        weight = s[22] if self.state_dim > 22 else 0.5
        max_weight = s[14] if self.state_dim > 14 else 1.0
        any_conflict = (
            (s[6] if self.state_dim > 6 else 0.0)
            + (s[7] if self.state_dim > 7 else 0.0)
            + (s[8] if self.state_dim > 8 else 0.0)
        ) > 0.5

        if any_conflict:
            return ACTION_NAMES["REJECT"], self._reward(phase, 0.82)
        if weight > max_weight:
            return ACTION_NAMES["SPLIT"], self._reward(phase, 0.85)
        if consolidation_savings > 0.5:
            return ACTION_NAMES["CONSOLIDATE"], self._reward(phase, 0.85)
        return ACTION_NAMES["ACCEPT"], self._reward(phase, 0.78)


class ShipmentTrackingCurriculum(_TMSTRMCurriculumBase):
    """Shipment-tracking exception flagging. Actions: ACCEPT (0),
    ESCALATE (3), MODIFY (4).

    Heuristic: MODIFY on temperature breach; ESCALATE on late or
    silent; ACCEPT otherwise."""
    trm_type = "shipment_tracking"
    active_actions = (
        ACTION_NAMES["ACCEPT"],
        ACTION_NAMES["ESCALATE"],
        ACTION_NAMES["MODIFY"],
    )

    def _simple_state(self) -> np.ndarray:
        return np.random.uniform(0.3, 0.7, self.state_dim).astype(np.float32)

    def _mixed_state(self) -> np.ndarray:
        return np.random.uniform(0.1, 0.95, self.state_dim).astype(np.float32)

    def _compute_label(self, s: np.ndarray, phase: int) -> tuple[int, float]:
        is_late = (s[2] if self.state_dim > 2 else 0.0) > 0.5
        silence_ratio = s[4] if self.state_dim > 4 else 0.0
        temp = s[10] if self.state_dim > 10 else 0.5
        temp_min = s[15] if self.state_dim > 15 else 0.0
        temp_max = s[14] if self.state_dim > 14 else 1.0

        if temp < temp_min or temp > temp_max:
            return ACTION_NAMES["MODIFY"], self._reward(phase, 0.82)
        if is_late or silence_ratio > 0.5:
            return ACTION_NAMES["ESCALATE"], self._reward(phase, 0.85)
        return ACTION_NAMES["ACCEPT"], self._reward(phase, 0.80)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TMS_TRM_CURRICULUM_REGISTRY: dict[str, type[_TMSTRMCurriculumBase]] = {
    "broker_routing":       BrokerRoutingCurriculum,
    "capacity_buffer":      CapacityBufferCurriculum,
    "capacity_promise":     CapacityPromiseCurriculum,
    "dock_scheduling":      DockSchedulingCurriculum,
    "equipment_reposition": EquipmentRepositionCurriculum,
    "exception_management": ExceptionManagementCurriculum,
    "freight_procurement":  FreightProcurementCurriculum,
    "intermodal_transfer":  IntermodalTransferCurriculum,
    "load_build":           LoadBuildCurriculum,
    "shipment_tracking":    ShipmentTrackingCurriculum,
}

# Backwards-compat alias — existing TMS callers
# (trm_site_trainer.py, powell_training_service.py,
# train_powell_models.py, scripts/training/train_trm.py) all import
# ``CURRICULUM_REGISTRY``. Keep that name working.
CURRICULUM_REGISTRY = TMS_TRM_CURRICULUM_REGISTRY

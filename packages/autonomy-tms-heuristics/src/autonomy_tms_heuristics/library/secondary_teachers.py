"""Phase-2 secondary heuristic teachers for TMS TRM training corpora.

Closes Open Item #2 from
[TMS_TRM_TRAINING_DATA_SPECIFICATION.md §8](../../../../../../docs/TMS_TRM_TRAINING_DATA_SPECIFICATION.md):
introduces multi-teacher consensus where it adds legitimate signal.

The primary teachers (``dispatch.compute_tms_decision``) encode
industry best practice — composite scoring, financial gating,
priority weighting. The Phase-2 secondaries express **different
defensible perspectives** so that disagreement between teachers
itself becomes a training signal:

* ``compute_freight_procurement_dat`` — *DAT-pure*: ignores carrier
  composite scores; picks the cheapest viable carrier within the
  DAT spot/contract band. The "cost-only buyer" perspective.

* ``compute_exception_management_regulatory`` — *Regulatory-first*:
  certain conditions (hazmat, FDA cold-chain, FMCSA HOS pressure,
  DOT post-accident reporting) force ESCALATE regardless of the
  primary's cost-benefit math. The "compliance officer" perspective.

Why only these two: spec §10 (Differences From SCP) — transport
heuristics are discrete (waterfall, threshold-based) with less
legitimate disagreement across methods than inventory policies. We
add secondaries only where two industry-recognised approaches truly
exist and would label different actions.

For TRMs without a secondary, ``SECONDARY_TEACHERS`` returns an
empty list. The corpus generator then writes only primary-teacher
columns for those rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping

from .base import (
    ExceptionManagementState,
    FreightProcurementState,
    TMSHeuristicDecision,
)
from .dispatch import Actions, compute_tms_decision


# ─────────────────────────────────────────────────────────────────────
# Secondary 1: DAT-pure FreightProcurement
# ─────────────────────────────────────────────────────────────────────


def compute_freight_procurement_dat(state: FreightProcurementState) -> TMSHeuristicDecision:
    """DAT-benchmark-only freight-procurement teacher.

    Picks the cheapest carrier whose rate is within the DAT band.
    Escalates if every candidate is more than 20 % above the DAT
    benchmark (broker-routing territory). Ignores carrier OTP /
    acceptance / reliability — purely a rate-comparison teacher.
    """
    benchmark = state.dat_benchmark_rate
    if benchmark <= 0:
        # No benchmark — defer to primary (return the primary's call).
        primary = compute_tms_decision("freight_procurement", state)
        return TMSHeuristicDecision(
            trm_type="freight_procurement",
            action=primary.action,
            quantity=primary.quantity,
            reasoning=f"DAT unavailable; deferring to primary ({primary.reasoning})",
            confidence=primary.confidence,
            urgency=primary.urgency,
            params_used={**primary.params_used, "teacher": "dat_pure"},
        )

    # Collect all candidates: primary, backups, spot.
    candidates: List[tuple] = []
    if state.primary_carrier_id is not None:
        rate = state.primary_carrier_rate or state.contract_rate
        if rate > 0:
            candidates.append(("primary", state.primary_carrier_id, rate))
    for bk in state.backup_carriers:
        rate = bk.get("rate", 0.0)
        if rate > 0:
            candidates.append(("backup", bk.get("id"), rate))
    if state.spot_rate > 0:
        candidates.append(("spot", None, state.spot_rate))

    if not candidates:
        return TMSHeuristicDecision(
            trm_type="freight_procurement", action=Actions.ESCALATE,
            reasoning="No carriers offered — escalate",
            urgency=1.0,
            params_used={"teacher": "dat_pure", "reason": "no_candidates"},
        )

    candidates.sort(key=lambda c: c[2])
    cheapest_tier, cheapest_id, cheapest_rate = candidates[0]
    deviation = (cheapest_rate - benchmark) / benchmark

    if deviation > 0.20:
        return TMSHeuristicDecision(
            trm_type="freight_procurement", action=Actions.ESCALATE,
            reasoning=f"Cheapest available {deviation*100:+.0f}% vs DAT {benchmark:.0f} — escalate",
            urgency=min(1.0, 0.7 + deviation),
            params_used={
                "teacher": "dat_pure",
                "cheapest_tier": cheapest_tier,
                "cheapest_rate": cheapest_rate,
                "dat_benchmark": benchmark,
                "deviation_pct": round(deviation, 3),
            },
        )

    return TMSHeuristicDecision(
        trm_type="freight_procurement", action=Actions.ACCEPT,
        reasoning=(
            f"DAT-cheapest: {cheapest_tier} at {cheapest_rate:.0f} "
            f"({deviation*100:+.1f}% vs DAT {benchmark:.0f})"
        ),
        urgency=max(0.2, min(0.9, 0.4 + deviation)),
        params_used={
            "teacher": "dat_pure",
            "selection": cheapest_tier,
            "carrier_id": cheapest_id,
            "rate": cheapest_rate,
            "dat_benchmark": benchmark,
            "deviation_pct": round(deviation, 3),
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Secondary 2: Regulatory-first ExceptionManagement
# ─────────────────────────────────────────────────────────────────────


def compute_exception_management_regulatory(
    state: ExceptionManagementState,
) -> TMSHeuristicDecision:
    """Compliance-first exception-management teacher.

    Escalates regardless of cost-benefit math when any of these fire:

    * **FDA cold-chain** — ``TEMPERATURE_EXCURSION`` exception type
      OR (``is_temperature_sensitive`` AND severity ≥ MEDIUM).
    * **DOT 49 CFR hazmat** — ``is_hazmat`` AND severity ≥ MEDIUM.
    * **DOT post-accident reporting** — ``CARRIER_BREAKDOWN``
      exception type (always escalates for incident review).
    * **FMCSA 395 HOS** — ``DETENTION`` exception with delay > 4 h
      (driver hours-of-service pressure).

    Non-regulatory cases defer to severity: CRITICAL → ESCALATE,
    everything else → ACCEPT (monitor).
    """
    triggers: List[str] = []

    if state.exception_type == "TEMPERATURE_EXCURSION":
        triggers.append("FDA cold-chain — temperature excursion")
    elif state.is_temperature_sensitive and state.severity in ("MEDIUM", "HIGH", "CRITICAL"):
        triggers.append(f"FDA cold-chain — temp-sensitive shipment, severity {state.severity}")

    if state.is_hazmat and state.severity in ("MEDIUM", "HIGH", "CRITICAL"):
        triggers.append(f"DOT 49 CFR — hazmat exception, severity {state.severity}")

    if state.exception_type == "CARRIER_BREAKDOWN":
        triggers.append("DOT — possible post-accident reporting (carrier breakdown)")

    if state.exception_type == "DETENTION" and state.estimated_delay_hrs > 4:
        triggers.append(
            f"FMCSA 395 — HOS pressure ({state.estimated_delay_hrs:.1f}h detention)"
        )

    if triggers:
        return TMSHeuristicDecision(
            trm_type="exception_management", action=Actions.ESCALATE,
            reasoning="Regulatory escalation — " + "; ".join(triggers),
            urgency=1.0,
            params_used={
                "teacher": "regulatory_first",
                "regulatory_triggers": triggers,
                "severity": state.severity,
                "exception_type": state.exception_type,
            },
        )

    # No regulatory trigger — severity gate only.
    if state.severity == "CRITICAL":
        return TMSHeuristicDecision(
            trm_type="exception_management", action=Actions.ESCALATE,
            reasoning="Critical severity (no regulatory trigger)",
            urgency=0.9,
            params_used={"teacher": "regulatory_first", "reason": "critical_severity"},
        )

    return TMSHeuristicDecision(
        trm_type="exception_management", action=Actions.ACCEPT,
        reasoning=f"No regulatory trigger; severity {state.severity} — monitor",
        urgency=0.3,
        params_used={"teacher": "regulatory_first", "severity": state.severity},
    )


# ─────────────────────────────────────────────────────────────────────
# Registry + consensus dispatch
# ─────────────────────────────────────────────────────────────────────


SECONDARY_TEACHERS: Mapping[str, List[Callable[[Any], TMSHeuristicDecision]]] = {
    "freight_procurement": [compute_freight_procurement_dat],
    "exception_management": [compute_exception_management_regulatory],
}


@dataclass(frozen=True)
class ConsensusDecision:
    """Multi-teacher labeling outcome for a single corpus row."""

    primary: TMSHeuristicDecision
    secondaries: List[TMSHeuristicDecision]
    # Consensus action: primary if everyone agrees, else the modal vote;
    # primary breaks ties so the corpus stays anchored to industry default.
    consensus_action: int
    # ``True`` when at least one secondary's action differs from primary.
    disagreement: bool
    # Distinct action count across all teachers (1 = unanimous).
    distinct_actions: int


def _modal_action(actions: List[int], primary_action: int) -> int:
    counts: Dict[int, int] = {}
    for a in actions:
        counts[a] = counts.get(a, 0) + 1
    # Highest count wins; ties broken by preferring primary's action.
    best = primary_action
    best_count = counts.get(primary_action, 0)
    for action, count in counts.items():
        if count > best_count:
            best = action
            best_count = count
    return best


def compute_with_consensus(trm_type: str, state: Any) -> ConsensusDecision:
    """Run the primary teacher + any registered secondaries for ``trm_type``.

    Always returns a ``ConsensusDecision`` even when the TRM has no
    secondary; callers can treat ``disagreement=False`` as a single-
    teacher row.
    """
    primary = compute_tms_decision(trm_type, state)
    secondaries = [fn(state) for fn in SECONDARY_TEACHERS.get(trm_type, [])]

    all_actions = [primary.action] + [s.action for s in secondaries]
    distinct = len(set(all_actions))
    consensus = _modal_action(all_actions, primary.action)
    disagreement = distinct > 1

    return ConsensusDecision(
        primary=primary,
        secondaries=secondaries,
        consensus_action=consensus,
        disagreement=disagreement,
        distinct_actions=distinct,
    )


def has_secondary_teachers(trm_type: str) -> bool:
    """Whether ``trm_type`` has any registered secondary teachers."""
    return bool(SECONDARY_TEACHERS.get(trm_type))

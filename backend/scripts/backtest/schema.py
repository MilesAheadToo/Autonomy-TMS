"""Live-backtest schema — frozen historical TMS decisions for replay.

A "backtest row" is one decision a transportation planner made
historically (Oracle OTM / SAP TM / MercuryGate / Blue Yonder export),
captured with enough state for ``compute_tms_decision()`` to be
replayed and compared.

JSONL is the canonical wire format — one row per line, native JSON for
list / dict fields (no double-encoding), ISO 8601 strings for
datetimes. The reader hydrates datetime fields per the TRM's state
dataclass automatically.

See [docs/TMS_LIVE_BACKTEST_SCHEMA.md](../../../docs/TMS_LIVE_BACKTEST_SCHEMA.md)
for the consumer-side guide on producing an extract from each
supported source system.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

# Wire the heuristics package onto sys.path so the schema can be loaded
# in isolation (e.g. by an ERP extractor on a different machine that
# only has this file + the heuristics wheel).
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from autonomy_tms_heuristics.library.base import (  # noqa: E402
    BrokerRoutingState,
    CapacityBufferState,
    CapacityPromiseState,
    DemandSensingState,
    DockSchedulingState,
    EquipmentRepositionState,
    ExceptionManagementState,
    FreightProcurementState,
    IntermodalTransferState,
    LaneVolumeForecastState,
    LoadBuildState,
    ShipmentTrackingState,
)


# Map trm_type → state dataclass for hydration.
_STATE_CLASSES = {
    "broker_routing": BrokerRoutingState,
    "capacity_buffer": CapacityBufferState,
    "capacity_promise": CapacityPromiseState,
    "demand_sensing": DemandSensingState,
    "dock_scheduling": DockSchedulingState,
    "equipment_reposition": EquipmentRepositionState,
    "exception_management": ExceptionManagementState,
    "freight_procurement": FreightProcurementState,
    "intermodal_transfer": IntermodalTransferState,
    "lane_volume_forecast": LaneVolumeForecastState,
    "load_build": LoadBuildState,
    "shipment_tracking": ShipmentTrackingState,
}


@dataclass(frozen=True)
class PlannerDecision:
    """The action the historical planner actually took."""

    action_code: int
    action_name: str
    reasoning: str = ""
    # Optional: who took the action — planner_id / agent_id / system.
    actor_id: Optional[str] = None
    # Optional: was this an autonomous AI action or human override?
    actor_kind: str = "human"  # "human", "ai", "auto"


@dataclass(frozen=True)
class BacktestRow:
    """One frozen historical decision for replay.

    The ``state`` field is a flat dict whose keys match the named TRM's
    state dataclass; ``hydrate_state`` converts it back. Datetime
    fields stored as ISO 8601 strings are parsed by the hydrator.
    """

    row_id: str
    trm_type: str
    timestamp: str  # ISO 8601 of when the planner decision happened
    tenant_id: int
    source_system: str  # "oracle_otm", "sap_tm", "mercurygate", "synthetic"
    state: Dict[str, Any]
    planner_decision: PlannerDecision
    # Outcome at execution (optional — may not be known at extract time).
    outcome: Optional[Dict[str, Any]] = None
    # Free-form metadata: extract id, contract ref, etc.
    metadata: Dict[str, Any] = field(default_factory=dict)


def hydrate_state(trm_type: str, flat: Dict[str, Any]) -> Any:
    """Reconstruct a state dataclass instance from its flat dict.

    Performs:

    * Datetime parsing for any field whose dataclass annotation is
      ``datetime`` or ``Optional[datetime]``.
    * Drops keys not in the dataclass (so extra metadata in the row
      doesn't break hydration).
    """
    cls = _STATE_CLASSES.get(trm_type)
    if cls is None:
        raise ValueError(f"Unknown trm_type: {trm_type}")
    type_hints = {f.name: f.type for f in fields(cls)}
    accepted = set(type_hints)
    kwargs: Dict[str, Any] = {}
    for k, v in flat.items():
        if k not in accepted:
            continue
        if v is None:
            kwargs[k] = None
            continue
        ann = type_hints[k]
        ann_str = str(ann)
        if "datetime" in ann_str and isinstance(v, str):
            kwargs[k] = datetime.fromisoformat(v)
        else:
            kwargs[k] = v
    return cls(**kwargs)


def write_jsonl(rows: Iterable[BacktestRow], path: Path) -> int:
    """Serialise ``rows`` to JSONL at ``path``. Returns row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(asdict(row), default=str) + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> Iterator[BacktestRow]:
    """Stream ``BacktestRow`` objects from JSONL at ``path``."""
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            d = json.loads(raw)
            pd = d.pop("planner_decision")
            yield BacktestRow(
                planner_decision=PlannerDecision(**pd),
                **d,
            )


def load_rows(path: Path) -> List[BacktestRow]:
    """Materialise all rows. Use for moderate-size fixtures; for large
    extracts prefer ``read_jsonl`` for streaming."""
    return list(read_jsonl(path))

#!/usr/bin/env python3
"""Generate a small synthetic ``tender_history_sample.jsonl`` fixture.

The fixture's purpose is to exercise the backtest runner end-to-end
without requiring a real ERP extract. Construction:

1. Draw 30 ``freight_procurement`` + 20 ``capacity_promise`` states
   via the per-TRM samplers in ``generate_tms_corpus``.
2. Label each with the heuristic teacher to obtain a "planner-like"
   ground truth.
3. Intentionally flip ~20 % of the rows to a *different* valid action
   so the runner has both agreement and disagreement cases to count.

Run: ``python scripts/backtest/generate_synthetic_fixture.py
        --out tests/fixtures/tender_history_sample.jsonl``
"""
from __future__ import annotations

import argparse
import importlib.util
import random
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import List

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from autonomy_tms_heuristics.library.dispatch import (  # noqa: E402
    Actions, compute_tms_decision,
)

from scripts.backtest.schema import BacktestRow, PlannerDecision, write_jsonl  # noqa: E402


# Hot-load the per-TRM samplers from generate_tms_corpus.py without
# triggering its module-level imports of app.* (which need torch).
for _name in ("app", "app.services", "app.services.powell"):
    sys.modules.setdefault(_name, ModuleType(_name))
_CORPUS = BACKEND_ROOT / "scripts" / "pretraining" / "generate_tms_corpus.py"
_spec = importlib.util.spec_from_file_location("tms_corpus_gen_fixture", _CORPUS)
_corpus = importlib.util.module_from_spec(_spec)
sys.modules["tms_corpus_gen_fixture"] = _corpus
_spec.loader.exec_module(_corpus)


_ACTION_NAME = {
    0: "ACCEPT", 1: "REJECT", 2: "DEFER", 3: "ESCALATE",
    4: "MODIFY", 5: "RETENDER", 6: "REROUTE", 7: "CONSOLIDATE",
    8: "SPLIT", 9: "REPOSITION", 10: "HOLD",
}


def _flatten_state(state) -> dict:
    from dataclasses import asdict as _asdict
    return _asdict(state)


def _alternative_action(action: int) -> int:
    """Pick a different valid action to simulate planner disagreement."""
    alternatives = {
        Actions.ACCEPT: Actions.ESCALATE,
        Actions.REJECT: Actions.ACCEPT,
        Actions.DEFER: Actions.ACCEPT,
        Actions.ESCALATE: Actions.ACCEPT,
        Actions.MODIFY: Actions.ACCEPT,
        Actions.RETENDER: Actions.REROUTE,
        Actions.REROUTE: Actions.ESCALATE,
        Actions.CONSOLIDATE: Actions.ACCEPT,
        Actions.SPLIT: Actions.ACCEPT,
        Actions.REPOSITION: Actions.HOLD,
        Actions.HOLD: Actions.REPOSITION,
    }
    return alternatives.get(action, Actions.ESCALATE)


def build_fixture(
    seed: int = 42,
    n_freight: int = 30,
    n_capacity: int = 20,
    disagreement_rate: float = 0.20,
) -> List[BacktestRow]:
    rng = random.Random(seed)
    rows: List[BacktestRow] = []
    base_ts = datetime(2026, 4, 1, tzinfo=timezone.utc)

    def emit(trm: str, sampler, count: int, prefix: str) -> None:
        for i in range(count):
            state = sampler(rng, phase=2)
            decision = compute_tms_decision(trm, state)
            # Flip ~disagreement_rate of rows to a different valid action.
            planner_action = (
                _alternative_action(decision.action)
                if rng.random() < disagreement_rate
                else decision.action
            )
            agreement = planner_action == decision.action
            row = BacktestRow(
                row_id=f"{prefix}-{i:03d}",
                trm_type=trm,
                timestamp=base_ts.isoformat(),
                tenant_id=1,
                source_system="synthetic",
                state=_flatten_state(state),
                planner_decision=PlannerDecision(
                    action_code=planner_action,
                    action_name=_ACTION_NAME[planner_action],
                    reasoning=(
                        decision.reasoning if agreement
                        else f"Planner override: ground-truth disagreement injected"
                    ),
                    actor_id="planner-001" if agreement else "planner-override-007",
                    actor_kind="human",
                ),
                outcome=None,
                metadata={"fixture": True},
            )
            rows.append(row)

    emit("freight_procurement", _corpus._sample_freight_procurement, n_freight, "FP")
    emit("capacity_promise", _corpus._sample_capacity_promise, n_capacity, "CP")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True,
                    help="Output JSONL path (e.g. tests/fixtures/tender_history_sample.jsonl)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-freight", type=int, default=30)
    ap.add_argument("--n-capacity", type=int, default=20)
    ap.add_argument("--disagreement-rate", type=float, default=0.20)
    args = ap.parse_args()

    rows = build_fixture(
        seed=args.seed,
        n_freight=args.n_freight,
        n_capacity=args.n_capacity,
        disagreement_rate=args.disagreement_rate,
    )
    written = write_jsonl(rows, args.out)
    print(f"Wrote {written} rows to {args.out}")


if __name__ == "__main__":
    main()

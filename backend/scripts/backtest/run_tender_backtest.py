#!/usr/bin/env python3
"""Replay a frozen TMS decision history through ``compute_tms_decision``
and report agreement with the historical planner choice.

Closes TRM-spec Open Item #3 — the *runner* lands here; the *real data*
extract is deferred until an Oracle OTM / SAP TM / MercuryGate frozen
export is in hand. The runner is validated against a small synthetic
fixture in [`tests/fixtures/tender_history_sample.jsonl`](../../tests/fixtures/tender_history_sample.jsonl).

Usage:
    python scripts/backtest/run_tender_backtest.py path/to/history.jsonl
    python scripts/backtest/run_tender_backtest.py history.jsonl --trm freight_procurement
    python scripts/backtest/run_tender_backtest.py history.jsonl --report-path report.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from autonomy_tms_heuristics.library.dispatch import compute_tms_decision  # noqa: E402

from scripts.backtest.schema import BacktestRow, hydrate_state, read_jsonl  # noqa: E402


logger = logging.getLogger("run_tender_backtest")


@dataclass
class RowResult:
    row_id: str
    trm_type: str
    teacher_action: int
    teacher_reasoning: str
    planner_action: int
    planner_reasoning: str
    agreement: bool


@dataclass
class BacktestReport:
    rows_total: int
    rows_agreed: int
    per_trm_agreement: Dict[str, Dict[str, int]] = field(default_factory=dict)
    # Confusion matrix per TRM: per_trm_confusion[trm][planner_action][teacher_action] = count
    per_trm_confusion: Dict[str, Dict[int, Dict[int, int]]] = field(default_factory=dict)
    top_disagreements: List[RowResult] = field(default_factory=list)

    @property
    def agreement_pct(self) -> float:
        return 100.0 * self.rows_agreed / max(1, self.rows_total)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rows_total": self.rows_total,
            "rows_agreed": self.rows_agreed,
            "agreement_pct": round(self.agreement_pct, 2),
            "per_trm": {
                trm: {
                    "total": d["total"],
                    "agreed": d["agreed"],
                    "agreement_pct": round(100.0 * d["agreed"] / max(1, d["total"]), 2),
                }
                for trm, d in self.per_trm_agreement.items()
            },
            "per_trm_confusion": {
                trm: {
                    str(planner): {str(teacher): count for teacher, count in inner.items()}
                    for planner, inner in matrix.items()
                }
                for trm, matrix in self.per_trm_confusion.items()
            },
            "top_disagreements": [
                {
                    "row_id": r.row_id,
                    "trm_type": r.trm_type,
                    "teacher_action": r.teacher_action,
                    "planner_action": r.planner_action,
                    "teacher_reasoning": r.teacher_reasoning,
                    "planner_reasoning": r.planner_reasoning,
                }
                for r in self.top_disagreements
            ],
        }


def replay_row(row: BacktestRow) -> RowResult:
    state = hydrate_state(row.trm_type, row.state)
    teacher = compute_tms_decision(row.trm_type, state)
    planner_action = row.planner_decision.action_code
    return RowResult(
        row_id=row.row_id,
        trm_type=row.trm_type,
        teacher_action=teacher.action,
        teacher_reasoning=teacher.reasoning,
        planner_action=planner_action,
        planner_reasoning=row.planner_decision.reasoning,
        agreement=teacher.action == planner_action,
    )


def run_backtest(
    history_path: Path,
    trm_filter: Optional[str] = None,
    max_disagreement_examples: int = 10,
) -> BacktestReport:
    rows_total = 0
    rows_agreed = 0
    per_trm_total: Counter = Counter()
    per_trm_agreed: Counter = Counter()
    per_trm_confusion: Dict[str, Dict[int, Dict[int, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    disagreements: List[RowResult] = []

    for row in read_jsonl(history_path):
        if trm_filter and row.trm_type != trm_filter:
            continue
        result = replay_row(row)
        rows_total += 1
        per_trm_total[result.trm_type] += 1
        per_trm_confusion[result.trm_type][result.planner_action][result.teacher_action] += 1
        if result.agreement:
            rows_agreed += 1
            per_trm_agreed[result.trm_type] += 1
        elif len(disagreements) < max_disagreement_examples:
            disagreements.append(result)

    per_trm_agreement = {
        trm: {"total": per_trm_total[trm], "agreed": per_trm_agreed[trm]}
        for trm in per_trm_total
    }
    confusion_plain = {
        trm: {planner: dict(inner) for planner, inner in matrix.items()}
        for trm, matrix in per_trm_confusion.items()
    }

    return BacktestReport(
        rows_total=rows_total,
        rows_agreed=rows_agreed,
        per_trm_agreement=per_trm_agreement,
        per_trm_confusion=confusion_plain,
        top_disagreements=disagreements,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Replay TMS frozen history vs heuristic teacher")
    ap.add_argument("history", type=Path, help="Path to JSONL backtest extract")
    ap.add_argument("--trm", type=str, default=None,
                    help="Filter to one TRM type (e.g. freight_procurement)")
    ap.add_argument("--report-path", type=Path, default=None,
                    help="Write the JSON report to this path (optional)")
    ap.add_argument("--max-disagreements", type=int, default=10,
                    help="Number of disagreement examples to surface (default 10)")
    args = ap.parse_args()

    logger.info(f"Replaying {args.history}")
    report = run_backtest(args.history, args.trm, args.max_disagreements)
    payload = report.to_dict()

    logger.info(f"Rows: {report.rows_total}  agreement: {report.agreement_pct:.1f}%")
    for trm, d in payload["per_trm"].items():
        logger.info(f"  {trm}: {d['agreed']}/{d['total']} ({d['agreement_pct']}%)")
    if payload["top_disagreements"]:
        logger.info(f"Top {len(payload['top_disagreements'])} disagreements:")
        for d in payload["top_disagreements"]:
            logger.info(
                f"  [{d['trm_type']}] {d['row_id']} "
                f"planner={d['planner_action']} vs teacher={d['teacher_action']}"
            )

    if args.report_path:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(args.report_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        logger.info(f"Report written to {args.report_path}")


if __name__ == "__main__":
    main()

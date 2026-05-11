# TMS Live Backtest Schema

**Status:** scaffold shipped 2026-05-11; real-data ingestion deferred.
**Owner:** TMS team.
**Closes:** TRM-spec Open Item #3 (framework + schema + synthetic
fixture). The actual ERP extract from an Oracle OTM / SAP TM /
MercuryGate / Blue Yonder customer is a separate workstream — once
that lands, the runner here replays the extract through
`compute_tms_decision()` and reports agreement vs the planner choice.

The shape and conventions below are stable; ERP extractors should
target this schema.

---

## 1. What a backtest row captures

One row = one historical decision a planner made. The row carries
**enough state** for the TMS heuristic teacher to be replayed and
its action compared to what the planner did. Implementation:
[`backend/scripts/backtest/schema.py`](../backend/scripts/backtest/schema.py).

```python
@dataclass(frozen=True)
class BacktestRow:
    row_id: str                    # unique key per row (e.g. "OTM-2025Q4-12345")
    trm_type: str                  # one of the 12 TMS TRMs
    timestamp: str                 # ISO 8601 — when planner decided
    tenant_id: int                 # Azirella tenant scoping
    source_system: str             # "oracle_otm", "sap_tm", "mercurygate", ...
    state: Dict[str, Any]          # flat dict — keys match TRM state dataclass
    planner_decision: PlannerDecision
    outcome: Optional[Dict]        # execution outcome (optional, may not be known)
    metadata: Dict[str, Any]       # extract id, contract ref, free-form
```

```python
@dataclass(frozen=True)
class PlannerDecision:
    action_code: int               # 0..10 per TMSHeuristicDecision action enum
    action_name: str               # "ACCEPT", "REJECT", "DEFER", ...
    reasoning: str                 # free-form (often the planner's note)
    actor_id: Optional[str]        # planner login / user id
    actor_kind: str                # "human", "ai", "auto"
```

Action codes mirror `autonomy_tms_heuristics.library.Actions`:

| Code | Name |
|---|---|
| 0 | ACCEPT |
| 1 | REJECT |
| 2 | DEFER |
| 3 | ESCALATE |
| 4 | MODIFY |
| 5 | RETENDER |
| 6 | REROUTE |
| 7 | CONSOLIDATE |
| 8 | SPLIT |
| 9 | REPOSITION |
| 10 | HOLD |

---

## 2. Wire format — JSONL

One row per line, native JSON for list / dict fields (no
double-encoding), ISO 8601 strings for datetimes. Datetime fields in
the `state` dict are hydrated to `datetime` objects automatically
based on the named TRM's state-dataclass annotations.

Example (one row, pretty-printed for readability — the on-wire format
is one line):

```json
{
  "row_id": "OTM-2025Q4-tender-002831",
  "trm_type": "freight_procurement",
  "timestamp": "2025-11-14T14:22:00+00:00",
  "tenant_id": 1,
  "source_system": "oracle_otm",
  "state": {
    "load_id": 8273912,
    "lane_id": 4421,
    "mode": "FTL",
    "primary_carrier_id": 318,
    "primary_carrier_rate": 2150.00,
    "primary_carrier_acceptance_pct": 0.88,
    "backup_carriers": [
      {"id": 411, "rate": 2310, "acceptance_pct": 0.72, "otp_pct": 0.91, "priority": 2}
    ],
    "spot_rate": 2580.00,
    "contract_rate": 2150.00,
    "dat_benchmark_rate": 2230.00,
    "market_tightness": 0.42,
    "tender_attempt": 1,
    "max_tender_attempts": 3,
    "hours_to_tender_deadline": 18.5
  },
  "planner_decision": {
    "action_code": 0,
    "action_name": "ACCEPT",
    "reasoning": "Primary at contract rate, attempt 1 — standard waterfall",
    "actor_id": "ttran@acme.example",
    "actor_kind": "human"
  },
  "outcome": {
    "tender_accepted": true,
    "actual_pickup_at": "2025-11-15T08:00:00+00:00",
    "actual_delivery_at": "2025-11-17T15:30:00+00:00",
    "actual_rate_paid": 2150.00
  },
  "metadata": {
    "otm_release_id": "REL-9912831",
    "extract_batch": "2025Q4-week-46"
  }
}
```

---

## 3. State-field schema by TRM

The `state` dict's keys must match the named TRM's state dataclass in
[`packages/autonomy-tms-heuristics/src/autonomy_tms_heuristics/library/base.py`](../packages/autonomy-tms-heuristics/src/autonomy_tms_heuristics/library/base.py).
Extra keys are dropped silently; missing keys fall back to the
dataclass default.

For the two TRMs the synthetic fixture covers, the required-ish fields
are:

### `freight_procurement` — `FreightProcurementState`

Required to get a meaningful teacher decision:
- `contract_rate`, `spot_rate`, `dat_benchmark_rate`, `market_tightness`
- `primary_carrier_id`, `primary_carrier_rate`, `primary_carrier_acceptance_pct`
- `backup_carriers` (list of dicts)
- `tender_attempt`, `max_tender_attempts`, `hours_to_tender_deadline`

### `capacity_promise` — `CapacityPromiseState`

Required:
- `total_capacity`, `committed_capacity`, `requested_loads`
- `priority`, `spot_rate_premium_pct`, `market_tightness`
- `primary_carrier_available`, `backup_carriers_count`

The remaining 10 TRM types are supported by `hydrate_state` (full
dispatch in `_STATE_CLASSES` in `schema.py`); their required fields
follow the dataclass definition. Future ERP-extractor work should
target one TRM at a time.

---

## 4. Source-system extraction guidance

The frozen extract is the planner's decision **at the moment they
made it** — not what the system shows now. Key extraction concerns:

### Oracle OTM

- Pull from `OTM_TENDER_HISTORY` + `OTM_RELEASE` + `OTM_RATE_GEO` joins.
- `tender_attempt` available; `dat_benchmark_rate` requires a DAT
  Spot subscription or in-house benchmark table.
- Planner decision lives in `OTM_TENDER_RESPONSE.STATUS_CD` — map:
  `ACCEPTED → ACCEPT`, `DECLINED → REJECT`, `MANUAL_OVERRIDE → ESCALATE`,
  `WITHDRAWN → DEFER` (no out-of-the-box DEFER state; planner notes
  reveal the deferral reason).

### SAP TM

- `TM_TENDERING_DOCUMENT` is the dispatch unit; `TM_ASSIGN_RESPONSE`
  carries acceptance.
- `tender_attempt` derived from `TENDER_SEQUENCE_NUMBER`.
- Mapping like OTM; SAP TM's `IT_OUTCOME` reason codes are richer
  (15+ values) — the extractor folds them into the 11-code Action
  enum.

### MercuryGate

- `mglShipment` + `mglTenderHistory` joins.
- Planner reasoning often in the `Notes` field; preserve verbatim.

### Blue Yonder TMS

- `TENDER_LEG` + `TENDER_HISTORY` views from the platform.
- Two-stage tender (primary + backup) is implicit — extractor maps
  to `tender_attempt` 1/2 explicitly.

### MES / ELD / TMS-lite (homegrown)

- Schema is freeform; extractor implementer normalises directly to
  this doc's row shape.

---

## 5. Running the backtest

```bash
python backend/scripts/backtest/run_tender_backtest.py \
    path/to/frozen_extract.jsonl \
    --report-path /tmp/backtest_report.json
```

Filter to one TRM:

```bash
python backend/scripts/backtest/run_tender_backtest.py \
    extract.jsonl --trm freight_procurement
```

Surface more disagreement examples in the report:

```bash
python backend/scripts/backtest/run_tender_backtest.py \
    extract.jsonl --max-disagreements 50
```

The report's `to_dict()` shape:

```json
{
  "rows_total": 12453,
  "rows_agreed": 11982,
  "agreement_pct": 96.22,
  "per_trm": {
    "freight_procurement": {"total": 8731, "agreed": 8459, "agreement_pct": 96.88},
    "capacity_promise":   {"total": 3722, "agreed": 3523, "agreement_pct": 94.65}
  },
  "per_trm_confusion": {
    "freight_procurement": {
      "0": {"0": 7821, "3": 12},    // planner ACCEPT → teacher ACCEPT 7821, ESCALATE 12
      "3": {"3": 638, "0": 64}      // planner ESCALATE → teacher ESCALATE 638, ACCEPT 64
    }
  },
  "top_disagreements": [
    {"row_id": "OTM-...", "trm_type": "freight_procurement", "planner_action": 3, "teacher_action": 0, ...}
  ]
}
```

---

## 6. Regenerating the synthetic fixture

The committed fixture at
[`backend/tests/fixtures/tender_history_sample.jsonl`](../backend/tests/fixtures/tender_history_sample.jsonl)
is produced by:

```bash
python backend/scripts/backtest/generate_synthetic_fixture.py \
    --out backend/tests/fixtures/tender_history_sample.jsonl \
    --seed 42
```

30 freight_procurement + 20 capacity_promise rows, with ~20 % of
rows intentionally flipped to a different valid action so the runner
has both agreement and disagreement cases to count. The tests assert
≥70 % agreement per TRM against this fixture.

---

## 7. Reference target — SCP's PO-Creation backtest

SCP's live-data backtest reached **99.6 %** agreement on real SAP
PO-Creation decisions (single TRM, ~50K rows). The TMS goal is
similar: at least one TRM (most likely `freight_procurement`, which
has the cleanest mapping to Oracle OTM / SAP TM extract fields)
above 95 % agreement on a frozen carrier-tender history of 10K+
rows.

Until that data lands, the synthetic fixture is the only check
the runner has.

---

## 8. Cross-references

- [`TMS_TRM_TRAINING_DATA_SPECIFICATION.md`](TMS_TRM_TRAINING_DATA_SPECIFICATION.md) — TRM catalog (Open Item #3 closes here).
- [`backend/scripts/backtest/schema.py`](../backend/scripts/backtest/schema.py) — canonical types.
- [`backend/scripts/backtest/run_tender_backtest.py`](../backend/scripts/backtest/run_tender_backtest.py) — runner.
- [`backend/scripts/backtest/generate_synthetic_fixture.py`](../backend/scripts/backtest/generate_synthetic_fixture.py) — fixture generator.
- [`backend/tests/scripts/test_tender_backtest.py`](../backend/tests/scripts/test_tender_backtest.py) — 16 tests.

---

*Last updated: 2026-05-11.*

#!/usr/bin/env python3
"""
Generate all TMS TRM training corpora in one invocation.

Enumerates every key in ``generate_tms_corpus.SAMPLERS`` (currently 12:
the 11 execution TRMs plus the lane-volume forecast orchestrator) and
writes one parquet — or jsonl fallback when ``pyarrow`` is unavailable —
per TRM.

Usage:
    python scripts/pretraining/generate_all_tms_trms.py
    python scripts/pretraining/generate_all_tms_trms.py --samples 100000
    python scripts/pretraining/generate_all_tms_trms.py --output-dir /data/tms_corpus
"""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.pretraining.generate_tms_corpus import main

if __name__ == "__main__":
    # Only force --all if the user didn't already pick a TRM. Lets the
    # wrapper double as a single-TRM driver for ad-hoc smoke runs.
    forwarded = sys.argv[1:]
    if "--trm" not in forwarded and "--all" not in forwarded:
        forwarded = ["--all"] + forwarded
    sys.argv = [sys.argv[0]] + forwarded
    main()

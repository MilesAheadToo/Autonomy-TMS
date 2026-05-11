"""Tests for the curriculum-phase mechanism in
``backend/scripts/pretraining/generate_tms_corpus.py``.

Closes TMS_TRM_TRAINING_DATA_SPECIFICATION.md Open Item #5 by locking
in the DAT-anchored phase bands and proving the samplers actually
shift their KPI distributions across phases.

Loaded via importlib so the test stays runnable in a torch-less
sandbox (the corpus script's reward-weight import stubs are CPU-safe
but the script itself isn't a package import target).
"""
from __future__ import annotations

import importlib.util
import random
import statistics
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest


# Stub package parents and PYTHONPATH so the script can resolve its
# ``autonomy_tms_heuristics`` and ``app.services.powell.tms_reward_weights``
# imports when loaded from outside the corpus-pipeline runtime.
_BACKEND = Path(__file__).resolve().parents[3]
for p in (
    str(_BACKEND),
    str(_BACKEND.parent / "packages" / "autonomy-tms-heuristics" / "src"),
    "/home/trevor/Autonomy-Core/packages/azirella-heuristics-common/src",
):
    if p not in sys.path:
        sys.path.insert(0, p)

for _name in ("app", "app.services", "app.services.powell"):
    sys.modules.setdefault(_name, ModuleType(_name))

_GEN_PATH = _BACKEND / "scripts" / "pretraining" / "generate_tms_corpus.py"
_spec = importlib.util.spec_from_file_location("tms_corpus_gen", _GEN_PATH)
_gen = importlib.util.module_from_spec(_spec)
sys.modules["tms_corpus_gen"] = _gen
_spec.loader.exec_module(_gen)

PHASES = _gen.PHASES
DEFAULT_PHASE = _gen.DEFAULT_PHASE
_sample_capacity_promise = _gen._sample_capacity_promise
_sample_capacity_buffer = _gen._sample_capacity_buffer
_sample_exception_management = _gen._sample_exception_management
_sample_intermodal_transfer = _gen._sample_intermodal_transfer
_sample_freight_procurement = _gen._sample_freight_procurement


# ─────────────────────────────────────────────────────────────────────
# Phase config invariants
# ─────────────────────────────────────────────────────────────────────


def test_three_phases_defined() -> None:
    assert set(PHASES.keys()) == {1, 2, 3}


def test_default_phase_is_normal() -> None:
    assert DEFAULT_PHASE == 2


def test_phase_bands_monotonic_in_disruption() -> None:
    """Disruption knobs grow phase1 → phase3; reliability shrinks."""
    p1, p2, p3 = PHASES[1], PHASES[2], PHASES[3]
    assert p1.spot_premium_max < p2.spot_premium_max < p3.spot_premium_max
    assert p1.reject_rate_max < p2.reject_rate_max < p3.reject_rate_max
    assert p1.market_tightness_band[1] < p3.market_tightness_band[1]
    assert p1.ramp_congestion_band[1] < p3.ramp_congestion_band[1]
    assert p1.demand_cv_max < p3.demand_cv_max
    # Reliability runs the OTHER direction — high in phase 1, low in phase 3.
    assert p1.intermodal_reliability_band[0] > p3.intermodal_reliability_band[0]
    assert p1.primary_carrier_otp_band[0] > p3.primary_carrier_otp_band[0]


def test_dat_thresholds_straddled_by_phases() -> None:
    """DAT industry anchors should land between phases.

    * Spot premium acceptance threshold = 30 % — phase 1 stays well
      below; phase 3 covers above (where DEFER / ESCALATE dominates).
    * Reject rate buffer-expansion threshold = 15 % — phase 1 stays
      below; phase 3 covers above (buffer-policy MODIFY territory).
    * BNSF / CSX intermodal floor = 80 % — phase 1 stays above; phase 3
      covers below (where REJECT dominates).
    """
    p1, p3 = PHASES[1], PHASES[3]
    assert p1.spot_premium_max < 0.30 < p3.spot_premium_max
    assert p1.reject_rate_max < 0.15 < p3.reject_rate_max
    assert p1.intermodal_reliability_band[0] > 0.80
    assert p3.intermodal_reliability_band[0] < 0.80


def test_exception_severity_weights_invert_across_phases() -> None:
    """Phase 1 should make LOW > CRITICAL; phase 3 the reverse."""
    p1 = PHASES[1].exception_severity_weights  # (LOW, MED, HIGH, CRITICAL)
    p3 = PHASES[3].exception_severity_weights
    assert p1[0] > p1[3]  # LOW > CRITICAL in baseline
    assert p3[0] < p3[3]  # LOW < CRITICAL in disruption


# ─────────────────────────────────────────────────────────────────────
# Sampler distributional invariants
# ─────────────────────────────────────────────────────────────────────


def _seeded_rng() -> random.Random:
    return random.Random(42)


def test_capacity_promise_phase_shifts_spot_premium() -> None:
    n = 2000
    p1 = [_sample_capacity_promise(_seeded_rng(), phase=1).spot_rate_premium_pct for _ in range(n)]
    p3 = [_sample_capacity_promise(_seeded_rng(), phase=3).spot_rate_premium_pct for _ in range(n)]
    # phase 3 mean should be at least 3× phase 1 mean.
    assert statistics.mean(p3) > 3 * statistics.mean(p1)
    assert max(p1) <= PHASES[1].spot_premium_max + 1e-6
    assert max(p3) <= PHASES[3].spot_premium_max + 1e-6


def test_capacity_buffer_phase_shifts_reject_rate() -> None:
    n = 2000
    p1 = [_sample_capacity_buffer(_seeded_rng(), phase=1).recent_tender_reject_rate for _ in range(n)]
    p3 = [_sample_capacity_buffer(_seeded_rng(), phase=3).recent_tender_reject_rate for _ in range(n)]
    assert statistics.mean(p3) > 3 * statistics.mean(p1)


def test_exception_severity_distribution_shifts() -> None:
    n = 1500
    rng = random.Random(7)
    p1 = Counter(_sample_exception_management(rng, phase=1).severity for _ in range(n))
    rng = random.Random(7)
    p3 = Counter(_sample_exception_management(rng, phase=3).severity for _ in range(n))
    # Phase 1 has more LOW than CRITICAL.
    assert p1["LOW"] > p1["CRITICAL"]
    # Phase 3 has more CRITICAL than LOW.
    assert p3["CRITICAL"] > p3["LOW"]


def test_intermodal_reliability_shifts() -> None:
    n = 1000
    p1 = [_sample_intermodal_transfer(_seeded_rng(), phase=1).intermodal_reliability_pct for _ in range(n)]
    p3 = [_sample_intermodal_transfer(_seeded_rng(), phase=3).intermodal_reliability_pct for _ in range(n)]
    # Phase 1 mean comfortably above BNSF/CSX 0.80 floor; phase 3 mean comfortably below.
    assert statistics.mean(p1) > 0.88
    assert statistics.mean(p3) < 0.80


def test_freight_procurement_market_tightness_shifts() -> None:
    n = 1000
    p1 = [_sample_freight_procurement(_seeded_rng(), phase=1).market_tightness for _ in range(n)]
    p3 = [_sample_freight_procurement(_seeded_rng(), phase=3).market_tightness for _ in range(n)]
    assert statistics.mean(p3) > 2 * statistics.mean(p1)


# ─────────────────────────────────────────────────────────────────────
# Backwards compatibility — default phase preserves the legacy default.
# ─────────────────────────────────────────────────────────────────────


def test_default_phase_call_no_kwarg() -> None:
    """Samplers must accept the no-phase call for compatibility."""
    rng = random.Random(0)
    s1 = _sample_capacity_promise(rng)
    s2 = _sample_capacity_buffer(rng)
    s3 = _sample_exception_management(rng)
    s4 = _sample_intermodal_transfer(rng)
    s5 = _sample_freight_procurement(rng)
    for s in (s1, s2, s3, s4, s5):
        assert s is not None


# ─────────────────────────────────────────────────────────────────────
# Error handling
# ─────────────────────────────────────────────────────────────────────


def test_generate_corpus_rejects_unknown_phase() -> None:
    with pytest.raises(ValueError, match="phase must be one of"):
        _gen.generate_corpus("capacity_promise", n_samples=1, phase=99)

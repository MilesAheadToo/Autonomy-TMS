"""Tests for LiveExceptionService — §3.29 Group A Phase 2 pure-logic
coverage.

The DB-backed ``detect_exceptions`` path is exercised by integration
tests gated on ``TMS_RUN_INTEGRATION_TESTS=1`` (same env-gate pattern
as ProductLaneAggregator); this suite covers the classifier math /
AIIO-mode mapping / edge cases without a live DB.

Loaded via ``importlib`` to bypass the heavy
``app.services.powell.__init__`` side effects (the package
imports SQLAlchemy ORM modules at import time, which the lightweight
unit-test environment doesn't carry).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest


_LES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "app", "services", "powell", "live_exception_service.py",
)


def _load_live_exception_module():
    import types

    for parent in ("app", "app.services", "app.services.powell"):
        if parent not in sys.modules:
            pkg = types.ModuleType(parent)
            pkg.__path__ = []
            sys.modules[parent] = pkg

    spec = importlib.util.spec_from_file_location(
        "live_exception_service_test_loaded", _LES_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_les = _load_live_exception_module()
LiveExceptionService = _les.LiveExceptionService
ExceptionResult = _les.ExceptionResult
ExceptionType = _les.ExceptionType
AIIOMode = _les.AIIOMode
LATE_ARRIVAL_BAND_RISK_THRESHOLD_MIN = _les.LATE_ARRIVAL_BAND_RISK_THRESHOLD_MIN
_build_decision_payload = _les._build_decision_payload


def _now() -> datetime:
    return datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


class TestServiceConstruction:
    def test_default_init(self):
        svc = LiveExceptionService()
        assert svc.late_arrival_horizon_min == 8 * 60
        assert svc.dwell_breach_horizon_sec == 4 * 3600
        assert svc.automate_threshold == 0.30
        assert svc.inform_threshold == 0.65

    def test_custom_horizons(self):
        svc = LiveExceptionService(
            late_arrival_horizon_min=240,
            dwell_breach_horizon_sec=7200,
            automate_threshold=0.2,
            inform_threshold=0.5,
        )
        assert svc.late_arrival_horizon_min == 240
        assert svc.dwell_breach_horizon_sec == 7200
        assert svc.automate_threshold == 0.2
        assert svc.inform_threshold == 0.5

    def test_invalid_late_arrival_horizon(self):
        with pytest.raises(ValueError, match="late_arrival_horizon_min"):
            LiveExceptionService(late_arrival_horizon_min=0)
        with pytest.raises(ValueError, match="late_arrival_horizon_min"):
            LiveExceptionService(late_arrival_horizon_min=-1)

    def test_invalid_dwell_breach_horizon(self):
        with pytest.raises(ValueError, match="dwell_breach_horizon_sec"):
            LiveExceptionService(dwell_breach_horizon_sec=0)

    def test_invalid_threshold_ordering(self):
        with pytest.raises(ValueError, match="thresholds"):
            LiveExceptionService(automate_threshold=0.7, inform_threshold=0.5)
        with pytest.raises(ValueError, match="thresholds"):
            LiveExceptionService(automate_threshold=0.0)
        with pytest.raises(ValueError, match="thresholds"):
            LiveExceptionService(inform_threshold=1.5)


# ---------------------------------------------------------------------------
# classify_late_arrival — committed-late branch (P50 > promised)
# ---------------------------------------------------------------------------


class TestLateArrivalCommittedLate:
    def test_on_time_returns_none(self):
        svc = LiveExceptionService()
        promised = _now() + timedelta(hours=2)
        # P50 ≤ promised AND P10 ≥ promised → on-track, no exception
        result = svc.classify_late_arrival(
            eta_id=1, tracked_entity_id=10, tenant_id=100,
            promised_at=promised,
            predicted_p50_at=promised - timedelta(minutes=30),
            predicted_p10_at=promised - timedelta(minutes=60),
            predicted_p90_at=promised + timedelta(minutes=15),
        )
        assert result is None

    def test_one_hour_slip_low_urgency(self):
        svc = LiveExceptionService()
        promised = _now() + timedelta(hours=2)
        result = svc.classify_late_arrival(
            eta_id=2, tracked_entity_id=11, tenant_id=100,
            promised_at=promised,
            predicted_p50_at=promised + timedelta(minutes=60),
            predicted_p10_at=promised + timedelta(minutes=20),
            predicted_p90_at=promised + timedelta(minutes=120),
        )
        assert result is not None
        assert result.exception_type == ExceptionType.LATE_ARRIVAL_DETECTED
        # 60 min / 480 min horizon = 0.125
        assert result.urgency == pytest.approx(0.125, abs=0.01)
        assert result.aiio_mode == AIIOMode.AUTOMATE
        assert result.source_eta_id == 2
        assert result.tracked_entity_id == 11
        assert result.metadata["slip_minutes"] == pytest.approx(60.0)

    def test_four_hour_slip_inform_band(self):
        svc = LiveExceptionService()
        promised = _now() + timedelta(hours=2)
        result = svc.classify_late_arrival(
            eta_id=3, tracked_entity_id=12, tenant_id=100,
            promised_at=promised,
            predicted_p50_at=promised + timedelta(hours=4),
            predicted_p10_at=promised + timedelta(hours=2),
            predicted_p90_at=promised + timedelta(hours=6),
        )
        assert result is not None
        # 240 min / 480 min horizon = 0.5 → INFORM band [0.30, 0.65)
        assert result.urgency == pytest.approx(0.5, abs=0.01)
        assert result.aiio_mode == AIIOMode.INFORM

    def test_eight_hour_slip_caps_at_one(self):
        svc = LiveExceptionService()
        promised = _now() + timedelta(hours=2)
        result = svc.classify_late_arrival(
            eta_id=4, tracked_entity_id=13, tenant_id=100,
            promised_at=promised,
            predicted_p50_at=promised + timedelta(hours=12),
            predicted_p10_at=promised + timedelta(hours=10),
            predicted_p90_at=promised + timedelta(hours=14),
        )
        assert result is not None
        # 720 min slip > 480 min horizon → urgency capped at 1.0 → INSPECT
        assert result.urgency == 1.0
        assert result.aiio_mode == AIIOMode.INSPECT


# ---------------------------------------------------------------------------
# classify_late_arrival — at-risk branch (promised inside [P10, P50])
# ---------------------------------------------------------------------------


class TestLateArrivalAtRisk:
    def test_promised_inside_lower_band_surfaces_at_risk(self):
        svc = LiveExceptionService()
        promised = _now() + timedelta(hours=2)
        # P10 < promised <= P50 with band slack ≥ threshold (30 min default)
        result = svc.classify_late_arrival(
            eta_id=5, tracked_entity_id=14, tenant_id=100,
            promised_at=promised,
            predicted_p50_at=promised,  # P50 == promised — at-risk edge
            predicted_p10_at=promised - timedelta(minutes=60),
            predicted_p90_at=promised + timedelta(minutes=60),
        )
        assert result is not None
        assert result.exception_type == ExceptionType.LATE_ARRIVAL_DETECTED
        # At-risk is always AUTOMATE band
        assert result.aiio_mode == AIIOMode.AUTOMATE
        assert result.urgency <= svc.automate_threshold
        assert "at risk" in result.reason_text.lower()
        assert "band_slack_min" in result.metadata

    def test_band_slack_below_threshold_returns_none(self):
        svc = LiveExceptionService()
        promised = _now() + timedelta(hours=2)
        # band_slack = 10 min, below the 30-min default threshold
        result = svc.classify_late_arrival(
            eta_id=6, tracked_entity_id=15, tenant_id=100,
            promised_at=promised,
            predicted_p50_at=promised + timedelta(seconds=0),  # P50 == promised
            predicted_p10_at=promised - timedelta(minutes=10),
            predicted_p90_at=promised + timedelta(minutes=30),
        )
        # P50 == promised → slip == 0 → not committed-late.
        # band_slack = 10 min < threshold → no at-risk either.
        assert result is None

    def test_at_risk_urgency_capped_at_automate_threshold(self):
        svc = LiveExceptionService(automate_threshold=0.3)
        promised = _now() + timedelta(hours=2)
        # band fully consumed: promised == P50, P10 well below
        result = svc.classify_late_arrival(
            eta_id=7, tracked_entity_id=16, tenant_id=100,
            promised_at=promised,
            predicted_p50_at=promised,
            predicted_p10_at=promised - timedelta(hours=2),
            predicted_p90_at=promised + timedelta(hours=2),
        )
        assert result is not None
        # consumed_frac == 1.0 → urgency capped at automate_threshold (0.3)
        assert result.urgency == pytest.approx(0.3, abs=0.001)
        assert result.aiio_mode == AIIOMode.AUTOMATE


# ---------------------------------------------------------------------------
# classify_late_arrival — edge cases
# ---------------------------------------------------------------------------


class TestLateArrivalEdgeCases:
    def test_missing_promised_at_returns_none(self):
        svc = LiveExceptionService()
        result = svc.classify_late_arrival(
            eta_id=8, tracked_entity_id=17, tenant_id=100,
            promised_at=None,
            predicted_p50_at=_now() + timedelta(hours=2),
        )
        assert result is None

    def test_missing_p50_returns_none(self):
        svc = LiveExceptionService()
        result = svc.classify_late_arrival(
            eta_id=9, tracked_entity_id=18, tenant_id=100,
            promised_at=_now() + timedelta(hours=2),
            predicted_p50_at=None,
        )
        assert result is None

    def test_p10_above_promised_no_exception(self):
        svc = LiveExceptionService()
        promised = _now() + timedelta(hours=2)
        # whole band sits before promised → on-track
        result = svc.classify_late_arrival(
            eta_id=10, tracked_entity_id=19, tenant_id=100,
            promised_at=promised,
            predicted_p50_at=promised - timedelta(minutes=15),
            predicted_p10_at=promised - timedelta(minutes=45),
            predicted_p90_at=promised - timedelta(minutes=5),
        )
        assert result is None


# ---------------------------------------------------------------------------
# classify_dwell_breach
# ---------------------------------------------------------------------------


class TestDwellBreach:
    def test_dwell_breach_event_low_urgency(self):
        svc = LiveExceptionService()
        # 30 min over a 60 min threshold → urgency = 1800/14400 = 0.125
        result = svc.classify_dwell_breach(
            event_id=20, tracked_entity_id=30, tenant_id=100,
            event_type="DWELL_BREACH",
            dwell_duration_seconds=5400,
            dwell_threshold_seconds=3600,
            occurred_at=_now(),
        )
        assert result is not None
        assert result.exception_type == ExceptionType.DWELL_BREACH_ALERT
        assert result.urgency == pytest.approx(1800 / (4 * 3600), abs=0.001)
        assert result.aiio_mode == AIIOMode.AUTOMATE
        assert result.source_event_id == 20
        assert result.metadata["breach_seconds"] == 1800

    def test_exit_event_with_breach_classifies(self):
        svc = LiveExceptionService()
        result = svc.classify_dwell_breach(
            event_id=21, tracked_entity_id=31, tenant_id=100,
            event_type="EXIT",
            dwell_duration_seconds=14400,  # 4h
            dwell_threshold_seconds=3600,  # 1h threshold → 3h breach
            occurred_at=_now(),
        )
        assert result is not None
        # 10800 sec / 14400 sec horizon = 0.75 → INSPECT
        assert result.urgency == pytest.approx(0.75, abs=0.001)
        assert result.aiio_mode == AIIOMode.INSPECT
        assert result.metadata["event_type"] == "EXIT"

    def test_exit_event_no_breach_returns_none(self):
        svc = LiveExceptionService()
        result = svc.classify_dwell_breach(
            event_id=22, tracked_entity_id=32, tenant_id=100,
            event_type="EXIT",
            dwell_duration_seconds=1800,  # 30 min
            dwell_threshold_seconds=3600,  # 60 min threshold → no breach
            occurred_at=_now(),
        )
        assert result is None

    def test_entry_event_returns_none(self):
        svc = LiveExceptionService()
        result = svc.classify_dwell_breach(
            event_id=23, tracked_entity_id=33, tenant_id=100,
            event_type="ENTRY",
            dwell_duration_seconds=5400,
            dwell_threshold_seconds=3600,
            occurred_at=_now(),
        )
        assert result is None

    def test_missing_dwell_duration_returns_none(self):
        svc = LiveExceptionService()
        result = svc.classify_dwell_breach(
            event_id=24, tracked_entity_id=34, tenant_id=100,
            event_type="DWELL_BREACH",
            dwell_duration_seconds=None,
            dwell_threshold_seconds=3600,
            occurred_at=_now(),
        )
        assert result is None

    def test_missing_threshold_returns_none(self):
        svc = LiveExceptionService()
        result = svc.classify_dwell_breach(
            event_id=25, tracked_entity_id=35, tenant_id=100,
            event_type="DWELL_BREACH",
            dwell_duration_seconds=5400,
            dwell_threshold_seconds=None,
            occurred_at=_now(),
        )
        assert result is None

    def test_long_breach_caps_at_one(self):
        svc = LiveExceptionService()
        # 24 hours over a 1h threshold → urgency capped at 1.0
        result = svc.classify_dwell_breach(
            event_id=26, tracked_entity_id=36, tenant_id=100,
            event_type="DWELL_BREACH",
            dwell_duration_seconds=86400,
            dwell_threshold_seconds=3600,
            occurred_at=_now(),
        )
        assert result is not None
        assert result.urgency == 1.0
        assert result.aiio_mode == AIIOMode.INSPECT


# ---------------------------------------------------------------------------
# AIIO band classification
# ---------------------------------------------------------------------------


class TestAIIOBands:
    def test_low_urgency_maps_to_automate(self):
        svc = LiveExceptionService()
        assert svc._classify_aiio(0.0) == AIIOMode.AUTOMATE
        assert svc._classify_aiio(0.1) == AIIOMode.AUTOMATE
        assert svc._classify_aiio(0.299) == AIIOMode.AUTOMATE

    def test_mid_urgency_maps_to_inform(self):
        svc = LiveExceptionService()
        assert svc._classify_aiio(0.3) == AIIOMode.INFORM
        assert svc._classify_aiio(0.5) == AIIOMode.INFORM
        assert svc._classify_aiio(0.649) == AIIOMode.INFORM

    def test_high_urgency_maps_to_inspect(self):
        svc = LiveExceptionService()
        assert svc._classify_aiio(0.65) == AIIOMode.INSPECT
        assert svc._classify_aiio(0.9) == AIIOMode.INSPECT
        assert svc._classify_aiio(1.0) == AIIOMode.INSPECT


# ---------------------------------------------------------------------------
# _build_decision_payload — Slice 2 Decision Stream payload builder
# ---------------------------------------------------------------------------


class TestBuildDecisionPayload:
    def test_late_arrival_payload_has_reroute_action(self):
        exc = ExceptionResult(
            exception_type=ExceptionType.LATE_ARRIVAL_DETECTED,
            tracked_entity_id=10,
            tenant_id=100,
            detected_at=_now(),
            urgency=0.5,
            aiio_mode=AIIOMode.INFORM,
            reason_text="ETA P50 slipped past promised by 240 min.",
            source_eta_id=42,
            metadata={"slip_minutes": 240.0, "model_name": "tabnet_v2"},
        )
        payload = _build_decision_payload(exc)
        assert payload["action_name"] == "LATE_ARRIVAL_REROUTE"
        assert payload["urgency"] == 0.5
        assert payload["reasoning"].startswith("ETA P50")
        assert payload["confidence"] == 1.0
        assert payload["exception_type"] == "LATE_ARRIVAL_DETECTED"
        assert payload["aiio_mode"] == "INFORM"
        assert payload["source_eta_id"] == 42
        assert "source_event_id" not in payload
        assert payload["scoring_detail"]["slip_minutes"] == 240.0
        assert payload["scoring_detail"]["model_name"] == "tabnet_v2"

    def test_dwell_breach_payload_has_dispatch_action(self):
        exc = ExceptionResult(
            exception_type=ExceptionType.DWELL_BREACH_ALERT,
            tracked_entity_id=20,
            tenant_id=200,
            detected_at=_now(),
            urgency=0.85,
            aiio_mode=AIIOMode.INSPECT,
            reason_text="Dwell breach: tracked entity over threshold.",
            source_event_id=99,
            metadata={"breach_seconds": 12000, "event_type": "EXIT"},
        )
        payload = _build_decision_payload(exc)
        assert payload["action_name"] == "DWELL_BREACH_DISPATCH"
        assert payload["urgency"] == 0.85
        assert payload["aiio_mode"] == "INSPECT"
        assert payload["source_event_id"] == 99
        assert "source_eta_id" not in payload
        assert payload["scoring_detail"]["breach_seconds"] == 12000
        assert payload["scoring_detail"]["event_type"] == "EXIT"

    def test_payload_metadata_isolated_from_source(self):
        # Mutating the returned scoring_detail must not affect the
        # original ExceptionResult.metadata (defensive copy).
        original_metadata = {"slip_minutes": 30.0}
        exc = ExceptionResult(
            exception_type=ExceptionType.LATE_ARRIVAL_DETECTED,
            tracked_entity_id=11,
            tenant_id=100,
            detected_at=_now(),
            urgency=0.1,
            aiio_mode=AIIOMode.AUTOMATE,
            reason_text="...",
            source_eta_id=1,
            metadata=original_metadata,
        )
        payload = _build_decision_payload(exc)
        payload["scoring_detail"]["slip_minutes"] = 999.0
        assert exc.metadata["slip_minutes"] == 30.0


# ---------------------------------------------------------------------------
# apply_to_decision_stream — wire-up to record_trm_decision
# ---------------------------------------------------------------------------


class TestApplyToDecisionStream:
    """The method wraps record_trm_decision; we monkeypatch the writer
    and verify the per-exception arguments. The downstream SQL path is
    covered by record_trm_decision's own integration tests.
    """

    def test_records_each_exception(self, monkeypatch):
        captured = []

        def fake_record(db, **kwargs):
            captured.append(kwargs)
            return 1000 + len(captured)

        # Stub agent_decision_writer module before the method imports it.
        import sys
        import types
        adw_stub = types.ModuleType(
            "app.services.powell.agent_decision_writer"
        )
        adw_stub.record_trm_decision = fake_record
        monkeypatch.setitem(
            sys.modules, "app.services.powell.agent_decision_writer", adw_stub
        )

        svc = LiveExceptionService()
        excs = [
            ExceptionResult(
                exception_type=ExceptionType.LATE_ARRIVAL_DETECTED,
                tracked_entity_id=1,
                tenant_id=100,
                detected_at=_now(),
                urgency=0.4,
                aiio_mode=AIIOMode.INFORM,
                reason_text="Late arrival",
                source_eta_id=10,
                metadata={"slip_minutes": 192.0},
            ),
            ExceptionResult(
                exception_type=ExceptionType.DWELL_BREACH_ALERT,
                tracked_entity_id=2,
                tenant_id=100,
                detected_at=_now(),
                urgency=0.8,
                aiio_mode=AIIOMode.INSPECT,
                reason_text="Dwell breach",
                source_event_id=20,
                metadata={"breach_seconds": 11520},
            ),
        ]
        ids = svc.apply_to_decision_stream(db=None, exceptions=excs)
        assert ids == [1001, 1002]
        assert len(captured) == 2
        # Late-arrival call
        assert captured[0]["trm_type"] == "exception_management"
        assert captured[0]["tenant_id"] == 100
        assert captured[0]["item_code"] == "entity-1"
        assert captured[0]["category"] == "LATE_ARRIVAL_DETECTED"
        assert captured[0]["impact_value"] == 0.4
        assert captured[0]["result"]["action_name"] == "LATE_ARRIVAL_REROUTE"
        assert captured[0]["result"]["source_eta_id"] == 10
        # Dwell-breach call
        assert captured[1]["item_code"] == "entity-2"
        assert captured[1]["category"] == "DWELL_BREACH_ALERT"
        assert captured[1]["result"]["action_name"] == "DWELL_BREACH_DISPATCH"
        assert captured[1]["result"]["source_event_id"] == 20

    def test_skips_writer_failures(self, monkeypatch):
        # record_trm_decision returns None on failure; apply_to_decision_stream
        # filters those out instead of including null IDs.
        def fake_record(db, **kwargs):
            return None if kwargs["item_code"] == "entity-1" else 42

        import sys
        import types
        adw_stub = types.ModuleType(
            "app.services.powell.agent_decision_writer"
        )
        adw_stub.record_trm_decision = fake_record
        monkeypatch.setitem(
            sys.modules, "app.services.powell.agent_decision_writer", adw_stub
        )

        svc = LiveExceptionService()
        excs = [
            ExceptionResult(
                exception_type=ExceptionType.LATE_ARRIVAL_DETECTED,
                tracked_entity_id=1, tenant_id=100,
                detected_at=_now(), urgency=0.4, aiio_mode=AIIOMode.INFORM,
                reason_text="...", source_eta_id=10, metadata={},
            ),
            ExceptionResult(
                exception_type=ExceptionType.DWELL_BREACH_ALERT,
                tracked_entity_id=2, tenant_id=100,
                detected_at=_now(), urgency=0.5, aiio_mode=AIIOMode.INFORM,
                reason_text="...", source_event_id=20, metadata={},
            ),
        ]
        ids = svc.apply_to_decision_stream(db=None, exceptions=excs)
        assert ids == [42]

    def test_empty_list_is_no_op(self, monkeypatch):
        called = []

        def fake_record(db, **kwargs):
            called.append(kwargs)
            return 1

        import types
        adw_stub = types.ModuleType(
            "app.services.powell.agent_decision_writer"
        )
        adw_stub.record_trm_decision = fake_record
        monkeypatch.setitem(
            sys.modules, "app.services.powell.agent_decision_writer", adw_stub
        )

        svc = LiveExceptionService()
        ids = svc.apply_to_decision_stream(db=None, exceptions=[])
        assert ids == []
        assert called == []

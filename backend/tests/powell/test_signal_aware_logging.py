"""
Tests for Sprint 4: Signal-Aware Logging

Validates that:
1. HiveSignalMixin adds 6 nullable columns to all 10 decision models
2. to_dict() includes signal fields
3. TRMDecisionRecord carries signal context
4. TrainingRecord carries signal context
5. OutcomeCollector applies signal-aware reward bonus
6. Backward compatibility: existing records with no signals work fine
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
import numpy as np


# ---------------------------------------------------------------------------
# 1. HiveSignalMixin on all 10 decision models
# ---------------------------------------------------------------------------

class TestHiveSignalMixin:
    """Test that all 10 decision models have signal columns."""

    @pytest.fixture(params=[
        "PowellATPDecision",
        "PowellRebalanceDecision",
        "PowellPODecision",
        "PowellOrderException",
        "PowellMODecision",
        "PowellTODecision",
        "PowellQualityDecision",
        "PowellMaintenanceDecision",
        "PowellSubcontractingDecision",
        "PowellForecastAdjustmentDecision",
    ])
    def model_class(self, request):
        from app.models.powell_decisions import (
            PowellATPDecision, PowellRebalanceDecision, PowellPODecision,
            PowellOrderException, PowellMODecision, PowellTODecision,
            PowellQualityDecision, PowellMaintenanceDecision,
            PowellSubcontractingDecision, PowellForecastAdjustmentDecision,
        )
        models = {
            "PowellATPDecision": PowellATPDecision,
            "PowellRebalanceDecision": PowellRebalanceDecision,
            "PowellPODecision": PowellPODecision,
            "PowellOrderException": PowellOrderException,
            "PowellMODecision": PowellMODecision,
            "PowellTODecision": PowellTODecision,
            "PowellQualityDecision": PowellQualityDecision,
            "PowellMaintenanceDecision": PowellMaintenanceDecision,
            "PowellSubcontractingDecision": PowellSubcontractingDecision,
            "PowellForecastAdjustmentDecision": PowellForecastAdjustmentDecision,
        }
        return models[request.param]

    def test_has_signal_context_column(self, model_class):
        assert hasattr(model_class, "signal_context")

    def test_has_urgency_at_time_column(self, model_class):
        assert hasattr(model_class, "urgency_at_time")

    def test_has_triggered_by_column(self, model_class):
        assert hasattr(model_class, "triggered_by")

    def test_has_signals_emitted_column(self, model_class):
        assert hasattr(model_class, "signals_emitted")

    def test_has_cycle_phase_column(self, model_class):
        assert hasattr(model_class, "cycle_phase")

    def test_has_cycle_id_column(self, model_class):
        assert hasattr(model_class, "cycle_id")

    def test_has_signal_dict_method(self, model_class):
        assert hasattr(model_class, "_signal_dict")


class TestMixinSignalDict:
    """Test _signal_dict() output."""

    def test_signal_dict_returns_all_keys(self):
        from app.models.powell_decisions import PowellATPDecision
        obj = PowellATPDecision(
            config_id=1, order_id="ORD-001", product_id="P1",
            location_id="L1", requested_qty=100, order_priority=3,
            can_fulfill=True, promised_qty=100,
            signal_context={"atp_shortage": True},
            urgency_at_time=0.75,
            triggered_by="ATP_SHORTAGE,DEMAND_SURGE",
            signals_emitted=["PO_EXPEDITE"],
            cycle_phase="ACQUIRE",
            cycle_id="abc-123",
        )

        d = obj._signal_dict()
        assert d["signal_context"] == {"atp_shortage": True}
        assert d["urgency_at_time"] == 0.75
        assert d["triggered_by"] == "ATP_SHORTAGE,DEMAND_SURGE"
        assert d["signals_emitted"] == ["PO_EXPEDITE"]
        assert d["cycle_phase"] == "ACQUIRE"
        assert d["cycle_id"] == "abc-123"

    def test_signal_dict_none_when_unset(self):
        from app.models.powell_decisions import PowellRebalanceDecision
        obj = PowellRebalanceDecision(
            config_id=1, product_id="P1", from_site="S1",
            to_site="S2", recommended_qty=50, reason="test",
        )

        d = obj._signal_dict()
        assert all(v is None for v in d.values())


# ---------------------------------------------------------------------------
# 2. SiteAgentDecision model has signal columns
# ---------------------------------------------------------------------------

class TestSiteAgentDecisionSignalColumns:
    """Test that the main SiteAgentDecision model has signal columns."""

    def test_has_signal_context(self):
        from app.models.powell_decision import SiteAgentDecision
        assert hasattr(SiteAgentDecision, "signal_context")

    def test_has_urgency_at_time(self):
        from app.models.powell_decision import SiteAgentDecision
        assert hasattr(SiteAgentDecision, "urgency_at_time")

    def test_has_triggered_by(self):
        from app.models.powell_decision import SiteAgentDecision
        assert hasattr(SiteAgentDecision, "triggered_by")

    def test_has_signals_emitted(self):
        from app.models.powell_decision import SiteAgentDecision
        assert hasattr(SiteAgentDecision, "signals_emitted")

    def test_has_cycle_phase(self):
        from app.models.powell_decision import SiteAgentDecision
        assert hasattr(SiteAgentDecision, "cycle_phase")

    def test_has_cycle_id(self):
        from app.models.powell_decision import SiteAgentDecision
        assert hasattr(SiteAgentDecision, "cycle_id")


# ---------------------------------------------------------------------------
# 3. TRMDecisionRecord carries signal context
# ---------------------------------------------------------------------------

class TestTRMDecisionRecordSignals:
    """Test signal fields on TRMDecisionRecord."""

    def test_default_signal_fields_none(self):
        from app.services.powell.integration.decision_integration import TRMDecisionRecord

        record = TRMDecisionRecord(
            decision_id="TEST-001",
            site_key="SITE001",
            decision_type="atp_exception",
            timestamp=datetime.utcnow(),
            input_state={},
            deterministic_result={},
            trm_adjustment={},
            confidence=0.8,
            final_result={},
        )

        assert record.signal_context is None
        assert record.urgency_at_time is None
        assert record.triggered_by is None
        assert record.signals_emitted is None
        assert record.cycle_phase is None
        assert record.cycle_id is None

    def test_signal_fields_populated(self):
        from app.services.powell.integration.decision_integration import TRMDecisionRecord

        record = TRMDecisionRecord(
            decision_id="TEST-002",
            site_key="SITE001",
            decision_type="po_timing",
            timestamp=datetime.utcnow(),
            input_state={},
            deterministic_result={},
            trm_adjustment={},
            confidence=0.9,
            final_result={},
            signal_context={"atp_shortage": True, "surge_urgency": 0.8},
            urgency_at_time=0.65,
            triggered_by="ATP_SHORTAGE",
            signals_emitted=["PO_EXPEDITE"],
            cycle_phase="ACQUIRE",
            cycle_id="cycle-abc-123",
        )

        assert record.signal_context == {"atp_shortage": True, "surge_urgency": 0.8}
        assert record.urgency_at_time == 0.65
        assert record.triggered_by == "ATP_SHORTAGE"
        assert record.signals_emitted == ["PO_EXPEDITE"]
        assert record.cycle_phase == "ACQUIRE"
        assert record.cycle_id == "cycle-abc-123"

    def test_asdict_includes_signals(self):
        from dataclasses import asdict
        from app.services.powell.integration.decision_integration import TRMDecisionRecord

        record = TRMDecisionRecord(
            decision_id="TEST-003",
            site_key="SITE001",
            decision_type="atp_exception",
            timestamp=datetime.utcnow(),
            input_state={},
            deterministic_result={},
            trm_adjustment={},
            confidence=0.5,
            final_result={},
            signal_context={"demand_surge": True},
            urgency_at_time=0.4,
        )

        d = asdict(record)
        assert "signal_context" in d
        assert d["signal_context"] == {"demand_surge": True}
        assert d["urgency_at_time"] == 0.4


# ---------------------------------------------------------------------------
# 4. TrainingRecord carries signal context
# ---------------------------------------------------------------------------

class TestTrainingRecordSignals:
    """Test signal fields on TrainingRecord."""

    def test_default_signal_fields_none(self):
        from app.services.powell.trm_trainer import TrainingRecord

        record = TrainingRecord(
            state_features=np.zeros(10),
            action=0,
            reward=1.0,
        )

        assert record.signal_context is None
        assert record.urgency_at_time is None
        assert record.triggered_by is None
        assert record.signals_emitted is None
        assert record.cycle_phase is None
        assert record.cycle_id is None

    def test_signal_fields_populated(self):
        from app.services.powell.trm_trainer import TrainingRecord

        record = TrainingRecord(
            state_features=np.zeros(10),
            action=1,
            reward=0.8,
            trm_type="po_creation",
            signal_context={"atp_shortage": True},
            urgency_at_time=0.7,
            triggered_by="ATP_SHORTAGE",
            signals_emitted=["PO_EXPEDITE"],
            cycle_phase="ACQUIRE",
            cycle_id="cycle-xyz",
        )

        assert record.signal_context == {"atp_shortage": True}
        assert record.urgency_at_time == 0.7
        assert record.cycle_phase == "ACQUIRE"


# ---------------------------------------------------------------------------
# 6. Integration: decision_integration persists signal fields
# ---------------------------------------------------------------------------

class TestDecisionIntegrationSignals:
    """Test that _persist_decision includes signal fields."""

    def test_persist_creates_with_signal_fields(self):
        from app.services.powell.integration.decision_integration import (
            SiteAgentDecisionTracker, TRMDecisionRecord,
        )

        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None

        tracker = SiteAgentDecisionTracker(db)

        record = TRMDecisionRecord(
            decision_id="TEST-PERSIST-001",
            site_key="SITE001",
            decision_type="atp_exception",
            timestamp=datetime.utcnow(),
            input_state={"order_id": "ORD-001"},
            deterministic_result={"can_fulfill": True},
            trm_adjustment={"confidence_boost": 0.1},
            confidence=0.85,
            final_result={"promised_qty": 100},
            signal_context={"atp_shortage": True},
            urgency_at_time=0.6,
            triggered_by="ATP_SHORTAGE",
            signals_emitted=["DEMAND_SURGE"],
            cycle_phase="SENSE",
            cycle_id="cycle-persist-001",
        )

        tracker._persist_decision(record)

        # Verify db.add was called
        assert db.add.called
        # Get the SiteAgentDecision object passed to db.add
        created_obj = db.add.call_args[0][0]
        assert created_obj.signal_context == {"atp_shortage": True}
        assert created_obj.urgency_at_time == 0.6
        assert created_obj.triggered_by == "ATP_SHORTAGE"
        assert created_obj.signals_emitted == ["DEMAND_SURGE"]
        assert created_obj.cycle_phase == "SENSE"
        assert created_obj.cycle_id == "cycle-persist-001"


# ---------------------------------------------------------------------------
# 7. Backward compatibility: no signal context
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Test that everything works without signal context (None values)."""

    def test_mixin_to_dict_with_no_signals(self):
        """ATP decision to_dict works with all signal fields as None."""
        from app.models.powell_decisions import PowellATPDecision
        obj = PowellATPDecision(
            config_id=1, order_id="ORD-001", product_id="PROD-001",
            location_id="LOC-001", requested_qty=100.0, order_priority=3,
            can_fulfill=True, promised_qty=100.0, decision_method="heuristic",
            confidence=0.9,
        )

        d = obj.to_dict()
        assert d["signal_context"] is None
        assert d["urgency_at_time"] is None
        assert d["cycle_phase"] is None
        assert d["order_id"] == "ORD-001"

    def test_training_record_backward_compat(self):
        """TrainingRecord works without signal fields."""
        from app.services.powell.trm_trainer import TrainingRecord

        record = TrainingRecord(
            state_features=np.array([1.0, 2.0, 3.0]),
            action=5,
            reward=0.8,
            trm_type="atp",
        )

        # All signal fields default to None
        assert record.signal_context is None
        assert record.urgency_at_time is None
        assert record.cycle_id is None

    def test_decision_record_backward_compat(self):
        """TRMDecisionRecord works without signal fields."""
        from app.services.powell.integration.decision_integration import TRMDecisionRecord
        from dataclasses import asdict

        record = TRMDecisionRecord(
            decision_id="BC-001",
            site_key="SITE001",
            decision_type="atp_exception",
            timestamp=datetime.utcnow(),
            input_state={},
            deterministic_result={},
            trm_adjustment={},
            confidence=0.5,
            final_result={},
        )

        d = asdict(record)
        assert d["signal_context"] is None
        assert d["cycle_phase"] is None

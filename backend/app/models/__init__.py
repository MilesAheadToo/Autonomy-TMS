"""Models package with all SQLAlchemy models.


"""
import logging
from typing import List, Type, Any

# Configure logger
logger = logging.getLogger(__name__)

# Import base first - this will also import all models
from .base import Base

# Capability manifest — declares (and eagerly loads) the Core capabilities
# TMS consumes: tenant, master, governance, powell, context_engine. See
# Autonomy-Core/docs/CAPABILITY_MANIFEST.md for the pattern.
from . import capabilities  # noqa: F401

# Import all models here to ensure they are registered with SQLAlchemy
from sqlalchemy import inspect

# relationships.py removed (dead code referencing non-existent scenario.py)

# Import models in dependency order to avoid circular imports
# 1. RBAC models first (User depends on these)
from .rbac import Permission, Role, user_roles, role_permissions, RolePermissionGrant, UserRoleAssignment

# 1b. Risk models (User depends on these)
from .risk import RiskAlert, Watchlist

# 2. Core models with no dependencies
from .user import RefreshToken  # Must be imported before User to avoid circular import
from .user import User, user_scenarios
from .tenant import Tenant, TenantMode, ClockMode
from .customer import Customer, CustomerStatus

# 3. Models that depend on User
from .participant import (
    ScenarioUser, ScenarioUserRole, ScenarioUserType, ScenarioUserStrategy, AgentMode,
    FunctionCategory, ScenarioUserFunction,
)

# 3b. Function Assignments (Feb 2026 - expanded role architecture)
from .function_assignment import FunctionAssignment

# 3. Scenario-related models
from .supervisor_action import SupervisorAction
from .scenario import Scenario, ScenarioStatus, Period, ScenarioUserAction
from .agent_config import AgentConfig
from .auth_models import PasswordHistory, PasswordResetToken
from .session import TokenBlacklist, UserSession
# Supply chain models
from .supply_chain import (
    ScenarioUserInventory, Order, ScenarioPeriod, ScenarioUserPeriod, PeriodPhase, UpstreamOrderType,
)
from .period_metric import PeriodMetric
from app.core.time_buckets import TimeBucket

# 4. Supply chain configuration models (must be imported before MonteCarloRun)
from .supply_chain_config import (
    NodeType,
    SupplyChainConfig,
    # AWS SC DM compliant names
    Site,
    TransportationLane,
    Lane,  # DEPRECATED: Use TransportationLane
    Market,
    MarketDemand,
    SupplyChainTrainingArtifact,
)

# 4b. Monte Carlo Simulation models (depends on SupplyChainConfig)
from .monte_carlo import (
    MonteCarloRun, MonteCarloScenario, MonteCarloTimeSeries,
    MonteCarloRiskAlert, SimulationStatus
)

# 4c. Master Production Scheduling models
from .mps import MPSPlan, MPSPlanItem, MPSCapacityCheck, MPSStatus, MPSKeyMaterialRequirement

# 4d. Production Order models (Phase 2)
from .production_order import ProductionOrder, ProductionOrderComponent

# 4d. Capacity Planning models (Phase 2)
from .capacity_plan import (
    CapacityPlan, CapacityResource, CapacityRequirement,
    CapacityPlanStatus, ResourceType
)

# 4e. MRP models (Phase 3)
from .mrp import MRPRun, MRPRequirement, MRPException

# 4e2. Pegging models (Full-Level Pegging & Multi-Stage CTP)
from .pegging import SupplyDemandPegging, AATPConsumptionRecord

# 4f. Purchase Order models (Phase 3)
from .purchase_order import PurchaseOrder, PurchaseOrderLineItem

# 4f2. Goods Receipt models
from .goods_receipt import GoodsReceipt, GoodsReceiptLineItem

# 4f3. Invoice and 3-Way Matching models
from .invoice import Invoice, InvoiceLineItem, InvoiceMatchResult

# 4g. Transfer Order models (Phase 3)
from .transfer_order import TransferOrder, TransferOrderLineItem

# 4h. Project Order models (Sprint 6 - Phase 3)
from .project_order import ProjectOrder, ProjectOrderLineItem

# 4i. Maintenance Order models (Sprint 6 - Phase 3)
from .maintenance_order import MaintenanceOrder, MaintenanceOrderSpare

# 4j. Turnaround Order models (Sprint 6 - Phase 3)
from .turnaround_order import TurnaroundOrder, TurnaroundOrderLineItem

# 4k. Quality Order models (Sprint 7 - Powell TRM Framework)
from .quality_order import QualityOrder, QualityOrderLineItem

# 4l. Subcontracting Order models (Sprint 7 - Powell TRM Framework)
from .subcontracting_order import SubcontractingOrder, SubcontractingOrderLineItem

# 4e. SC Entities (Phase 2) - SC Canonical Models
# Import Product and other SC entities
from .sc_entities import TradingPartner, Product, ProductBom

# 4e-ext. Site Planning Config (Extension — ERP-specific heuristic dispatch)
from .site_planning_config import SitePlanningConfig

# 4e-ext2. Experiential Knowledge (Alicke's "Planner Was the System" — Powell Bₜ)
from .experiential_knowledge import ExperientialKnowledge

# 4e. Supplier models (Phase 2) - SC Compliant
# VendorProduct must be imported after TradingPartner to resolve relationships
from .supplier import (
    VendorProduct, VendorLeadTime, SupplierPerformance
)

# 4f. Inventory Projection models (Phase 2) - SC Compliant ATP/CTP
from .inventory_projection import (
    InvProjection, AtpProjection, CtpProjection, OrderPromise
)

# 5. A2A Collaboration models (Phase 7 Sprint 2)
from .chat import ChatMessage, AgentSuggestion, WhatIfAnalysis, MessageType, SenderType

# 6. Simulation/Gamification models (Phase 7 Sprint 5)
from .achievement import (
    Achievement, ScenarioUserStats, ScenarioUserAchievement, Leaderboard,
    LeaderboardEntry, ScenarioUserBadge, AchievementNotification,
)

# 7. Enterprise Features - Option 1
from .sso_provider import SSOProvider, UserSSOMapping
# RBAC models already imported at top
from .audit_log import AuditLog

# 8. Mobile Push Notifications - Option 2
from .notification import PushToken, NotificationPreference, NotificationLog, PlatformType

# 9. AWS SC Planning - Recommendations (Sprint 4)
from .recommendations import Recommendation, RecommendationDecision

# 9b. Supply Plan Requests (used by User relationship)
from .supply_plan import (
    SupplyPlanRequest, SupplyPlanResult, SupplyPlanComparison, SupplyPlanExport
)

# 10. Inline Comments for Orders/Plans
from .comment import Comment, CommentMention, CommentAttachment

# 11. Team Messaging for Collaboration
from .team_message import (
    TeamChannel, TeamMessage, TeamMessageMention,
    TeamMessageAttachment, TeamMessageRead, channel_members
)

# 12. Forecast Exception Alerts
from .forecast_exception import (
    ForecastException, ForecastExceptionRule, ForecastExceptionComment,
    ExceptionWorkflowTemplate, ExceptionEscalationLog
)

# 13. Approval Workflow Templates
from .approval_template import (
    ApprovalTemplate, ApprovalRequest, ApprovalAction
)

# 14. Forecast Adjustments
from .forecast_adjustment import (
    ForecastAdjustment, ForecastVersion, BulkAdjustmentTemplate
)

# 14b. Forecast Pipeline Runs (clustering/prediction/metrics)
from .forecast_pipeline import (
    ForecastPipelineConfig,
    ForecastPipelineRun,
    ForecastPipelineCluster,
    ForecastPipelinePrediction,
    ForecastPipelineMetric,
    ForecastPipelineFeatureImportance,
    ForecastPipelinePublishLog,
)

# 15. Consensus Planning
from .consensus_plan import (
    ConsensusPlan, ConsensusPlanVersion, ConsensusPlanVote,
    ConsensusPlanComment, ConsensusPlanStatus
)

# 16. SAP Data Import Cadence System
from .sync_job import (
    SyncJobConfig, SyncJobExecution, SyncTableResult, APSchedulerJob,
    SyncDataType, SyncStatus, DEFAULT_SYNC_CADENCES
)

# 17. Workflow System
from .workflow import (
    WorkflowTemplate, WorkflowExecution, WorkflowStepExecution,
    WorkflowStatus, WorkflowStepType, WorkflowTriggerType,
    DEFAULT_WORKFLOW_TEMPLATES
)

# 18. Planning Cycle & Snapshot Management
from .planning_cycle import (
    PlanningCycle, PlanningSnapshot, SnapshotDelta, SnapshotLineage,
    CycleType, CycleStatus, SnapshotType, SnapshotTier,
    DeltaOperation, DeltaEntityType
)

# 19. Planning Decision Tracking
from .planning_decision import (
    PlanningDecision, DecisionHistory, DecisionComment,
    DecisionAction, DecisionCategory, DecisionStatus, DecisionPriority,
    DECISION_REASON_CODES, DEFAULT_APPROVAL_THRESHOLDS
)

# 20. AIIO Framework - Agent Actions (Insights Landing Page)
from .agent_action import (
    AgentAction, ActionMode, ActionCategory, ExecutionResult
)

# 21. Powell Framework - Sequential Decision Analytics & Modeling
from .powell import (
    PowellBeliefState, PowellPolicyParameters, PowellValueFunction,
    PowellCalibrationLog, ConformalMethod, EntityType, PolicyType
)

# 21c. Powell Allocations (tGNN-generated priority allocations)
from .powell_allocation import PowellAllocation

# 21d. Powell Execution Decision Records
from .powell_decisions import (
    PowellATPDecision, PowellRebalanceDecision,
    PowellPODecision, PowellOrderException,
    PowellMODecision, PowellTODecision,
    PowellQualityDecision, PowellMaintenanceDecision,
    PowellSubcontractingDecision, PowellForecastAdjustmentDecision,
)

# 21b. Powell Training Configuration and TRM Training Data
from .powell_training_config import (
    PowellTrainingConfig, TRMTrainingConfig, TrainingRun,
    TRMType, TrainingStatus, DEFAULT_TRM_REWARD_WEIGHTS
)
from .trm_training_data import (
    ATPDecisionLog, ATPOutcome,
    RebalancingDecisionLog, RebalancingOutcome,
    PODecisionLog, POOutcome,
    OrderTrackingDecisionLog, OrderTrackingOutcome,
    TRMReplayBuffer, DecisionSource, OutcomeStatus
)

# 22. Condition Monitoring & Scenario Evaluation
from .condition_alert import (
    ConditionAlert, ScenarioEvaluation, SupplyRequest,
    ConditionType as ConditionAlertType, ConditionSeverity as ConditionAlertSeverity,
    ConditionResolution
)

# 23. Planning Cascade (S&OP → MPS → Supply Agent → Allocation Agent)
from .planning_cascade import (
    PolicyEnvelope, SupplyBaselinePack, SupplyCommit, SolverBaselinePack,
    AllocationCommit, FeedBackSignal, AgentDecisionMetrics,
    PolicySource, CandidateMethod, AllocationMethod, CommitStatus,
    IntegrityViolationType, RiskFlagType,
    LayerLicense, LayerName, LayerMode,
)

# 24. Decision Tracking - Agent Performance Metrics for Powell Framework Dashboards
from .decision_tracking import (
    AgentDecision, PerformanceMetric, SOPWorklistItem,
    DecisionType, DecisionStatus as DQSDecisionStatus, DecisionUrgency
)

# 24b. Override Effectiveness — Bayesian Posteriors & Causal Match Pairs
from .override_effectiveness import (
    OverrideEffectivenessPosterior, CausalMatchPair,
)

# 24c. GNN Directive Review — Human Override at Network Level
from .gnn_directive_review import (
    GNNDirectiveReview, PolicyEnvelopeOverride,
)

# 24d. Decision Governance — AIIO Impact-Based Gating & Executive Directives
from .decision_governance import (
    DecisionGovernancePolicy, GuardrailDirective,
)

# 24e. Escalation Arbiter — Vertical Escalation Audit Log
from .escalation_log import PowellEscalationLog

# 25. Collaboration Scenarios (Agentic Authorization Protocol demo data)
from .collaboration_scenario import CollaborationScenario

# 26. SAP User Import (SC-filtered user provisioning)
from .sap_user_import import SAPUserImportLog, SAPRoleMapping

# 26b. SAP Connection Persistence
from .sap_connection import SAPConnection

# 26c. SAP Data Staging (intermediate layer between SAP extraction and AWS SC entities)
from .sap_staging import SAPExtractionRun, SAPStagingRow, SAPTableSchema

# 26d. ERP Vendor/Variant Registry
from .erp_registry import ERPVendor, ERPVariant, ERP_VENDOR_VARIANTS, ERP_VARIANT_PROFILES

# 26e. Generalized ERP Connection (supports SAP, Odoo, D365, etc.)
from .erp_connection import ERPConnection

# 26f. D365 Data Staging (intermediate layer between D365 extraction and AWS SC entities)
from .d365_staging import D365ExtractionRun, D365StagingRow, D365TableSchema

# 26g. Odoo Data Staging (intermediate layer between Odoo extraction and AWS SC entities)
from .odoo_staging import OdooExtractionRun, OdooStagingRow, OdooTableSchema

# 26h. Model Checkpoints & Training Datasets (SOC II compliant, tenant-scoped)
from .model_checkpoint import ModelCheckpoint, TrainingDataset

# 27. Autonomy Customer Registry
from .autonomy_customer import AutonomyCustomer

# 27. Executive Briefing — LLM-Synthesized Strategy Briefings
from .executive_briefing import (
    ExecutiveBriefing, BriefingFollowup, BriefingSchedule,
    BriefingType, BriefingStatus,
)

# 28. User Directives — "Azirella" context capture & Powell routing
from .user_directive import UserDirective, ConfigProvisioningStatus

# 29b. Scenario Events — What-if event injection for CDC cascade analysis
from .scenario_event import ScenarioEvent

# 29. Email Signals — GDPR-safe email ingestion for SC intelligence
from .email_signal import EmailSignal, EmailConnection

# 29c. External Signals — Outside-in planning intelligence (FRED, weather, energy, GDELT, etc.)
from .external_signal import ExternalSignal, ExternalSignalSource

# 30. RCCP — Rough-Cut Capacity Planning (Bill of Resources + Validation Runs)
from .rccp import BillOfResources, RCCPRun, RCCPMethod, ProductionPhase, RCCPRunStatus

# 31. Agent Stochastic Parameters — Per-agent distribution values with default tracking
from .agent_stochastic_param import AgentStochasticParam

# 32. Scenario Engine — Machine-speed what-if planning (agent scenario branches)
from .agent_scenario import AgentScenario, AgentScenarioAction, ScenarioTemplate

# 33. SC Data Model Extensions — SAP VBEP, MARM, CRHD, MBEW, KAKO, VBUK, VBUP, CDHDR, CDPOS
from .sc_extensions import (
    OutboundOrderLineSchedule, ProductUomConversion, WorkCenterMaster,
    MaterialValuation, CapacityResourceDetail, OutboundOrderStatus,
    OutboundOrderLineStatus, SAPChangeLog, SAPChangeLogDetail,
)

# 34. Unified Training Corpus — single source of truth for all agent training data
from .training_corpus import TrainingCorpusSample, TrainingCorpusCheckpoint

# 35. TMS Entities — Transportation Management System domain models
from .tms_entities import (
    # Enums
    TransportMode, EquipmentType, ShipmentStatus, LoadStatus,
    ExceptionType, ExceptionSeverity, ExceptionResolutionStatus,
    CarrierType, TenderStatus, AppointmentType, AppointmentStatus, RateType,
    # Commodity
    CommodityHierarchy, Commodity,
    # Carrier & Equipment
    Carrier, CarrierLane, Equipment, CarrierScorecard,
    # Shipment & Load
    Shipment, ShipmentLeg, Load, LoadItem,
    # Freight Rates & Tender
    FreightRate, FreightTender,
    # Appointments & Dock
    DockDoor, Appointment,
    # Exceptions
    ShipmentException, ExceptionResolution,
    # Documents
    BillOfLading, ProofOfDelivery,
    # Tracking (p44-aligned)
    TrackingEventType, TrackingEvent, ShipmentIdentifier,
)

# 36. TMS Network Configuration — Facility, Lane Profile, Carrier Contract
from .transportation_config import (
    FacilityType, ContractStatus, LaneDirection,
    FacilityConfig, OperatingSchedule, YardLocation,
    LaneProfile, CarrierContract,
)

# 37. TMS Shipment Route Cache — OSRM-derived polyline cache
from .shipment_route_cache import ShipmentRouteCache  # noqa: F401

# Core Plane Registry — register the canonical PlaneRegistration ORM
# against TMS's shared Base metadata at module-load time. A lazy import
# in tenant_service.py is too late for Base.metadata.create_all() /
# test bootstrap paths. (MIGRATION_REGISTER 1.9 — Plane Registry.)
from azirella_data_model.planes import PlaneRegistration, Plane, PlaneRegistry  # noqa: F401

# 38. Analytics — InventoryOptimization, CapacityOptimization,
#     NetworkOptimization, KPIConfiguration. Imported here so
#     Base.metadata.create_all() picks them up at startup.
from . import analytics  # noqa: F401

# 37. TMS Planning — Forecast, Capacity Targets, Transportation Plan
# `TransportationPlan` + `TransportationPlanItem` migrated to Core in
# §3.39 (PR #14). PlanStatus + PlanItemStatus are re-exported from Core
# via tms_planning.py's noqa import shim.
from .tms_planning import (
    ForecastMethod, PlanStatus, PlanItemStatus,
    ShippingForecast, CapacityTarget,
)
from azirella_data_model.transport_plan import (  # noqa: E402
    TransportationPlan, TransportationPlanItem,
)

from .intermodal_network import (
    IntermodalRamp, IntermodalRate,
    RampCongestionSnapshot, SpotRateSnapshot,
    RampType, IntermodalRateSource, SpotRateSource,
)

from .policy_parameters import PolicyParameters, PolicySource

from .terminal_coordinator import (
    TerminalUrgencyOverride, LaneWaterfallOverride, TerminalHealthSignal,
    L2TrendDirection,
)

from .hub_hour_snapshot import HubHourSnapshot

# Verify all models are properly registered
registered_tables = set(Base.metadata.tables.keys())
# Updated terminology: scenarios, scenario_users, scenario_user_actions.
# Round → Period rename completed; the canonical table is `periods`,
# not `rounds`. This self-check string drift was responsible for the
# "Missing tables in metadata: {'periods'}" warning at TMS startup.
expected_tables = {
    'users', 'refresh_tokens', 'scenario_users', 'password_history',
    'password_reset_tokens', 'token_blacklist', 'user_sessions',
    'scenarios', 'periods', 'scenario_user_actions', 'user_scenarios', 'tenants'
}

missing_tables = expected_tables - registered_tables
if missing_tables:
    logger.warning(f"Missing tables in metadata: {missing_tables}")

logger.info(f"Registered tables in metadata: {registered_tables}")

# Explicitly import all models to ensure they are registered with SQLAlchemy metadata
# This helps SQLAlchemy discover all models and their relationships
__all__ = [
    'Base',
    'User',
    'Tenant',
    'Customer',
    'CustomerStatus',
    'RefreshToken',
    'NodeType',
    'SupplyChainConfig',
    # 'Item', 'ItemNodeConfig', - REMOVED: use Product, ProductBom instead
    'Product',  # SC compliant product table
    'ProductBom',  # SC compliant BOM table
    # AWS SC DM compliant names
    'Site',
    'TransportationLane',
    'Lane',  # DEPRECATED: Use TransportationLane
    'Market',
    'MarketDemand',
    'SupplyChainTrainingArtifact',
    'MPSPlan',
    'MPSPlanItem',
    'MPSCapacityCheck',
    'MPSStatus',
    'MonteCarloRun',
    'MonteCarloScenario',
    'MonteCarloTimeSeries',
    'MonteCarloRiskAlert',
    'SimulationStatus',
    'ProductionOrder',
    'ProductionOrderComponent',
    'CapacityPlan',
    'CapacityResource',
    'CapacityRequirement',
    'CapacityPlanStatus',
    'ResourceType',
    'MRPRun',
    'MRPRequirement',
    'MRPException',
    'PurchaseOrder',
    'PurchaseOrderLineItem',
    'GoodsReceipt',
    'GoodsReceiptLineItem',
    'Invoice',
    'InvoiceLineItem',
    'InvoiceMatchResult',
    'TransferOrder',
    'TransferOrderLineItem',
    'TradingPartner',
    'VendorProduct',
    'VendorLeadTime',
    'SupplierPerformance',
    # Scenario terminology
    'Scenario',
    'ScenarioStatus',
    'ScenarioPeriod',
    # ScenarioUser terminology (was Participant)
    'ScenarioUser',
    'ScenarioUserRole',
    'ScenarioUserType',
    'ScenarioUserStrategy',
    'ScenarioUserAction',
    'ScenarioUserInventory',
    'ScenarioUserPeriod',
    'FunctionCategory',
    'ScenarioUserFunction',
    'FunctionAssignment',
    'AgentConfig',
    'PasswordHistory',
    'PasswordResetToken',
    'TokenBlacklist',
    'UserSession',
    'user_scenarios',
    'AgentMode',
    'PeriodPhase',
    'UpstreamOrderType',
    'Period',
    'Order',
    'TimeBucket',
    'ChatMessage',
    'AgentSuggestion',
    'WhatIfAnalysis',
    'MessageType',
    'SenderType',
    'Achievement',
    'ScenarioUserStats',
    'ScenarioUserAchievement',
    'ScenarioUserBadge',
    'Leaderboard',
    'LeaderboardEntry',
    'AchievementNotification',
    # Option 1: Enterprise Features
    'Tenant',
    'SSOProvider',
    'UserSSOMapping',
    'Permission',
    'Role',
    'RolePermissionGrant',
    'UserRoleAssignment',
    'AuditLog',
    # Option 2: Mobile Push Notifications
    'PushToken',
    'NotificationPreference',
    'NotificationLog',
    'PlatformType',
    # Sprint 4: Recommendations
    'Recommendation',
    'RecommendationDecision',
    # Inline Comments
    'Comment',
    'CommentMention',
    'CommentAttachment',
    # Team Messaging
    'TeamChannel',
    'TeamMessage',
    'TeamMessageMention',
    'TeamMessageAttachment',
    'TeamMessageRead',
    'channel_members',
    # Forecast Exception Alerts
    'ForecastException',
    'ForecastExceptionRule',
    'ForecastExceptionComment',
    'ExceptionWorkflowTemplate',
    'ExceptionEscalationLog',
    # Approval Workflow Templates
    'ApprovalTemplate',
    'ApprovalRequest',
    'ApprovalAction',
    # Forecast Adjustments
    'ForecastAdjustment',
    'ForecastVersion',
    'BulkAdjustmentTemplate',
    # Consensus Planning
    'ConsensusPlan',
    'ConsensusPlanVersion',
    'ConsensusPlanVote',
    'ConsensusPlanComment',
    'ConsensusPlanStatus',
    # SAP Data Import Cadence
    'SyncJobConfig',
    'SyncJobExecution',
    'SyncTableResult',
    'APSchedulerJob',
    'SyncDataType',
    'SyncStatus',
    'DEFAULT_SYNC_CADENCES',
    # Workflow System
    'WorkflowTemplate',
    'WorkflowExecution',
    'WorkflowStepExecution',
    'WorkflowStatus',
    'WorkflowStepType',
    'WorkflowTriggerType',
    'DEFAULT_WORKFLOW_TEMPLATES',
    # Planning Cycle & Snapshots
    'PlanningCycle',
    'PlanningSnapshot',
    'SnapshotDelta',
    'SnapshotLineage',
    'CycleType',
    'CycleStatus',
    'SnapshotType',
    'SnapshotTier',
    'DeltaOperation',
    'DeltaEntityType',
    # Planning Decisions
    'PlanningDecision',
    'DecisionHistory',
    'DecisionComment',
    'DecisionAction',
    'DecisionCategory',
    'DecisionStatus',
    'DecisionPriority',
    'DECISION_REASON_CODES',
    'DEFAULT_APPROVAL_THRESHOLDS',
    # AIIO Framework - Agent Actions
    'AgentAction',
    'ActionMode',
    'ActionCategory',
    'ExecutionResult',
    # Powell Framework - Sequential Decision Analytics
    'PowellBeliefState',
    'PowellPolicyParameters',
    'PowellValueFunction',
    'PowellCalibrationLog',
    'ConformalMethod',
    'EntityType',
    'PolicyType',
    # Quality Order models
    'QualityOrder',
    'QualityOrderLineItem',
    # Subcontracting Order models
    'SubcontractingOrder',
    'SubcontractingOrderLineItem',
    # Powell Execution Decision Records (new TRMs)
    'PowellMODecision',
    'PowellTODecision',
    'PowellQualityDecision',
    'PowellMaintenanceDecision',
    'PowellSubcontractingDecision',
    'PowellForecastAdjustmentDecision',
    # Condition Monitoring & Scenario Evaluation
    'ConditionAlert',
    'ScenarioEvaluation',
    'SupplyRequest',
    'ConditionAlertType',
    'ConditionAlertSeverity',
    'ConditionResolution',
    # Planning Cascade (S&OP → MPS → Supply Agent → Allocation Agent)
    'PolicyEnvelope',
    'SupplyBaselinePack',
    'SupplyCommit',
    'SolverBaselinePack',
    'AllocationCommit',
    'FeedBackSignal',
    'AgentDecisionMetrics',
    'PolicySource',
    'CandidateMethod',
    'AllocationMethod',
    'CommitStatus',
    'IntegrityViolationType',
    'RiskFlagType',
    'LayerLicense',
    'LayerName',
    'LayerMode',
    # Decision Tracking - Agent Performance Metrics
    'AgentDecision',
    'PerformanceMetric',
    'SOPWorklistItem',
    'DecisionType',
    'DQSDecisionStatus',
    'DecisionUrgency',
    # SAP User Import
    'SAPUserImportLog',
    'SAPRoleMapping',
    # SAP Connection Persistence
    'SAPConnection',
    'ERPConnection',
    'SAPExtractionRun', 'SAPStagingRow', 'SAPTableSchema',
    'ERPVendor', 'ERPVariant', 'ERP_VENDOR_VARIANTS', 'ERP_VARIANT_PROFILES',
    # Override Effectiveness — Bayesian Posteriors
    'OverrideEffectivenessPosterior',
    'CausalMatchPair',
    # GNN Directive Review — Human Override at Network Level
    'GNNDirectiveReview',
    'PolicyEnvelopeOverride',
    # Decision Governance — AIIO Impact-Based Gating & Executive Directives
    'DecisionGovernancePolicy',
    'GuardrailDirective',
    # Escalation Arbiter — Vertical Escalation Audit Log
    'PowellEscalationLog',
    # Executive Briefing — LLM-Synthesized Strategy Briefings
    'ExecutiveBriefing',
    'BriefingFollowup',
    'BriefingSchedule',
    'BriefingType',
    'BriefingStatus',
    # Email Signals — GDPR-safe email ingestion
    'EmailSignal',
    'EmailConnection',
    # RCCP — Rough-Cut Capacity Planning
    'BillOfResources',
    'RCCPRun',
    'RCCPMethod',
    'ProductionPhase',
    'RCCPRunStatus',
    # Agent Stochastic Parameters
    'AgentStochasticParam',
    # SC Data Model Extensions (SAP unprocessed tables)
    'OutboundOrderLineSchedule',
    'ProductUomConversion',
    'WorkCenterMaster',
    'MaterialValuation',
    'CapacityResourceDetail',
    'OutboundOrderStatus',
    'OutboundOrderLineStatus',
    'SAPChangeLog',
    'SAPChangeLogDetail',
    # Unified Training Corpus
    'TrainingCorpusSample',
    # TMS Entities
    'TransportMode', 'EquipmentType', 'ShipmentStatus', 'LoadStatus',
    'ExceptionType', 'ExceptionSeverity', 'ExceptionResolutionStatus',
    'CarrierType', 'TenderStatus', 'AppointmentType', 'AppointmentStatus', 'RateType',
    'CommodityHierarchy', 'Commodity',
    'Carrier', 'CarrierLane', 'Equipment', 'CarrierScorecard',
    'Shipment', 'ShipmentLeg', 'Load', 'LoadItem',
    'FreightRate', 'FreightTender',
    'DockDoor', 'Appointment',
    'ShipmentException', 'ExceptionResolution',
    'BillOfLading', 'ProofOfDelivery',
    'TrackingEventType', 'TrackingEvent', 'ShipmentIdentifier',
    # TMS Network Config
    'FacilityType', 'ContractStatus', 'LaneDirection',
    'FacilityConfig', 'OperatingSchedule', 'YardLocation',
    'LaneProfile', 'CarrierContract',
    # TMS Planning
    'ForecastMethod', 'PlanStatus', 'PlanItemStatus',
    'ShippingForecast', 'CapacityTarget',
    'TransportationPlan', 'TransportationPlanItem',
    # Intermodal network + spot-rate substrate
    'IntermodalRamp', 'IntermodalRate',
    'RampCongestionSnapshot', 'SpotRateSnapshot',
    'RampType', 'IntermodalRateSource', 'SpotRateSource',
    # L4 Strategic policy
    'PolicyParameters', 'PolicySource',
    # L2 Terminal Coordinator data plane
    'TerminalUrgencyOverride', 'LaneWaterfallOverride', 'TerminalHealthSignal',
    'L2TrendDirection',
    # L2 Phase-2 hub-hour snapshot (GATv2 training substrate)
    'HubHourSnapshot',
]

# Note: SQLAlchemy will configure mappers lazily when first used.
# Calling configure_mappers() explicitly here can cause issues with
# back_populates relationships that may not be fully defined yet.

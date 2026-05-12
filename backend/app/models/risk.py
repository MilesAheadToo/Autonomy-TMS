"""Risk Analysis Models — TMS re-export shim + watchlist / prediction add-ons.

§3.62 Phase 3 (Risk Engine to Core): TMS's RiskAlert class was a
copy of SCP's pre-§3.62 class against the same ``risk_alerts`` table.
Both have been folded into Core's unified ``Alert`` ORM
(``azirella_data_model.risk_engine.models``). TMS code that imports
``RiskAlert`` from this module gets the Core class verbatim — table,
columns, AIIO state machine, relationships are all the canonical
Core versions.

Watchlist and RiskPrediction stay in this module — they're tenant-
config and ML-output respectively, not the alert surface itself
(same scope decision as SCP took in commit ``f9e6a05f``).

Historical note: TMS's RiskAlert was never produced (zero
``db.add(RiskAlert(...))`` in the TMS codebase per the §3.62 Phase 3
audit); the table existed via baseline_schema.sql and was only read
by the ``risk_analysis`` endpoint stubs. The fold-down is therefore
a clean "replace class with re-export" with no data implications.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from datetime import datetime

from azirella_data_model.risk_engine import Alert as RiskAlert  # noqa: F401

from .base import Base


class Watchlist(Base):
    """
    Watchlist Model
    User-defined monitoring lists for products/sites with custom thresholds
    """
    __tablename__ = "watchlists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Ownership
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)

    # Monitoring configuration
    config_id = Column(Integer, ForeignKey("supply_chain_configs.id"), nullable=True)

    # Filters (JSON)
    product_filter = Column(JSON, nullable=True)  # List of product IDs or patterns
    site_filter = Column(JSON, nullable=True)  # List of site IDs or patterns

    # Alert thresholds (override defaults)
    stockout_threshold = Column(Float, nullable=True)  # Probability threshold (0-100)
    overstock_threshold_days = Column(Float, nullable=True)  # Days of supply threshold
    leadtime_variance_threshold = Column(Float, nullable=True)  # CV% threshold

    # Notification settings
    enable_notifications = Column(Boolean, default=True)
    notification_frequency = Column(String(20), default="DAILY")  # REALTIME, HOURLY, DAILY, WEEKLY
    notification_channels = Column(JSON, nullable=True)  # ["email", "sms", "slack"]
    notification_recipients = Column(JSON, nullable=True)  # List of user IDs

    # Status
    is_active = Column(Boolean, default=True, index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_checked_at = Column(DateTime, nullable=True)

    # Relationships
    creator = relationship("User", foreign_keys=[created_by])
    tenant = relationship("Tenant")
    config = relationship("SupplyChainConfig")

    def __repr__(self):
        return f"<Watchlist {self.name} by User {self.created_by}>"


class RiskPrediction(Base):
    """
    Risk Prediction Model
    Historical predictions for ML model tracking and validation
    """
    __tablename__ = "risk_predictions"

    id = Column(Integer, primary_key=True, index=True)

    # Prediction metadata
    model_name = Column(String(100), nullable=False)  # trm_agent, gnn_agent, statistical
    model_version = Column(String(50), nullable=False)
    prediction_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Entity references
    product_id = Column(String(255), nullable=False, index=True)
    site_id = Column(String(255), nullable=False, index=True)

    # Prediction type
    prediction_type = Column(String(50), nullable=False)  # DEMAND, STOCKOUT, OVERSTOCK, LEADTIME

    # Forecast horizon
    horizon_days = Column(Integer, nullable=False)  # Number of days ahead
    target_date = Column(DateTime, nullable=False, index=True)  # Date of predicted event

    # Predicted values
    predicted_value = Column(Float, nullable=False)  # Predicted demand, probability, etc.
    confidence = Column(Float, nullable=True)  # Model confidence (0-100)
    prediction_interval_lower = Column(Float, nullable=True)  # P10
    prediction_interval_upper = Column(Float, nullable=True)  # P90

    # Actual outcome (for validation)
    actual_value = Column(Float, nullable=True)
    actual_recorded_at = Column(DateTime, nullable=True)
    prediction_error = Column(Float, nullable=True)  # actual - predicted

    # Model features (JSON)
    features = Column(JSON, nullable=True)  # Feature values used for prediction

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_pred_product_site_date', 'product_id', 'site_id', 'target_date'),
        Index('idx_pred_model_type', 'model_name', 'prediction_type'),
    )

    def __repr__(self):
        return f"<RiskPrediction {self.prediction_type} {self.product_id} @ {self.target_date}>"

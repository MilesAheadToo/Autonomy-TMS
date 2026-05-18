"""
Inventory Projection Models - SC Compliant

SC Entities:
- InvLevel: Current inventory snapshots (already exists)
- InvProjection: Time-phased inventory projection (NEW)
- AtpProjection: Available-to-Promise calculation (NEW)
- CtpProjection: Capable-to-Promise calculation (NEW)

Reference: Supply Chain Data Model
"""

from sqlalchemy import (
    Integer,
    String,
    Float,
    Double,
    ForeignKey,
    DateTime,
    Date,
    Boolean,
    JSON,
    Index,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, date
from typing import Optional
from decimal import Decimal

from .base import Base


class InvProjection(Base):
    """
    SC Entity: inv_projection (Extension)

    Time-phased inventory projection showing future inventory levels
    based on planned supply and demand.

    SC Core Fields:
    - company_id, product_id, site_id, projection_date (PK components)
    - on_hand_qty, available_qty, allocated_qty
    - source tracking (source, source_event_id, source_update_dttm)

    Extensions:
    - ATP/CTP quantities
    - Scenario tracking for what-if analysis
    - Probabilistic projections (P10/P50/P90)
    """
    __tablename__ = "inv_projection"

    # SC Primary Key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # SC Core Fields
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[str] = mapped_column(String(100), ForeignKey("product.id"), nullable=False)  # SC Product table
    site_id: Mapped[int] = mapped_column(Integer, ForeignKey("site.id"), nullable=False)
    projection_date: Mapped[date] = mapped_column(Date, nullable=False)

    # SC Inventory Quantities
    on_hand_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Physical inventory on hand")
    in_transit_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Inbound shipments")
    on_order_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Purchase orders not yet shipped")
    allocated_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Reserved for customer orders")
    available_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Available for new orders")
    reserved_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Reserved for production/internal use")

    # SC Supply/Demand Quantities
    supply_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Planned supply receipts")
    demand_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Planned demand/consumption")

    # Opening/Closing Balance
    opening_inventory: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)
    closing_inventory: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)

    # Extension: ATP/CTP Quantities
    atp_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Available-to-Promise")
    ctp_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Capable-to-Promise")

    # Extension: Stochastic Projections (P10/P50/P90)
    closing_inventory_p10: Mapped[Optional[float]] = mapped_column(Double, comment="10th percentile (optimistic)")
    closing_inventory_p50: Mapped[Optional[float]] = mapped_column(Double, comment="50th percentile (median)")
    closing_inventory_p90: Mapped[Optional[float]] = mapped_column(Double, comment="90th percentile (pessimistic)")
    closing_inventory_std_dev: Mapped[Optional[float]] = mapped_column(Double, comment="Standard deviation")

    # Extension: Stockout Risk
    stockout_probability: Mapped[Optional[float]] = mapped_column(Double, comment="Probability of stockout (0-1)")
    days_of_supply: Mapped[Optional[float]] = mapped_column(Double, comment="Inventory coverage in days")

    # Legacy scenario tracking removed 2026-05-18 (§3.80 Category 2,
    # migration 20260518_soc2_cat2_drop_inv_projection_scope_strings).
    # ``config_id`` (below) is now the canonical scenario-branching
    # mechanism — the prior free-text ``scenario_id`` / ``scenario_name``
    # columns were dead code (no readers, no writers).

    # SC Source Tracking
    source: Mapped[Optional[str]] = mapped_column(String(100), comment="Source system")
    source_event_id: Mapped[Optional[str]] = mapped_column(String(100), comment="Source event identifier")
    source_update_dttm: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="Source update timestamp")

    # Extension: Simulation Integration
    # config_id is the canonical scenario-branching mechanism after the
    # 2026-05-18 §3.80 Cat 2 cleanup. NOT NULL at the DB level per
    # migration 20260518_inv_projection_config_id_not_null.
    config_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("supply_chain_configs.id"), nullable=False
    )
    period_number: Mapped[Optional[int]] = mapped_column(Integer, comment="Simulation round")

    # Audit Fields
    created_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=datetime.utcnow)

    # Relationships
    product = relationship("Product", foreign_keys=[product_id])
    site = relationship("Site", foreign_keys=[site_id])
    config = relationship("SupplyChainConfig", foreign_keys=[config_id])

    __table_args__ = (
        Index('idx_inventory_projection_lookup', 'product_id', 'site_id', 'projection_date'),
        # idx_inv_projection_scenario + idx_inv_projection_scenario_round
        # dropped with the scenario_id column 2026-05-18 — see comment above.
    )

    def calculate_atp(self) -> float:
        """
        Calculate Available-to-Promise (ATP)

        ATP = On-Hand + In-Transit - Allocated - Reserved

        This is the quantity available to promise to new customer orders.
        """
        return max(0.0, self.on_hand_qty + self.in_transit_qty - self.allocated_qty - self.reserved_qty)

    def calculate_ctp(self, production_capacity: float = 0.0) -> float:
        """
        Calculate Capable-to-Promise (CTP)

        CTP = ATP + Planned Production Capacity

        This includes future production capability in addition to ATP.

        Args:
            production_capacity: Available production capacity for this period
        """
        return self.calculate_atp() + production_capacity

    def calculate_dos(self, average_daily_demand: float) -> Optional[float]:
        """
        Calculate Days of Supply (DOS)

        DOS = Available Inventory / Average Daily Demand

        Args:
            average_daily_demand: Average demand per day

        Returns:
            Days of supply, or None if demand is zero
        """
        if average_daily_demand <= 0:
            return None
        return self.available_qty / average_daily_demand


class AtpProjection(Base):
    """
    SC Entity: atp_projection (Extension)

    Available-to-Promise (ATP) projection with cumulative ATP calculation.

    ATP Logic:
    - Period 1 ATP = On-Hand - Allocated + Supply - Demand
    - Period N ATP = Previous ATP + Supply - Demand
    - Cumulative ATP = Sum of all ATP through time

    Extensions:
    - Multi-period ATP rules
    - Customer allocation percentages
    - Priority-based ATP allocation
    """
    __tablename__ = "atp_projection"

    # Primary Key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # SC Core Fields
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[str] = mapped_column(String(100), ForeignKey("product.id"), nullable=False)  # SC Product table
    site_id: Mapped[int] = mapped_column(Integer, ForeignKey("site.id"), nullable=False)
    atp_date: Mapped[date] = mapped_column(Date, nullable=False)

    # ATP Quantities
    atp_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Available-to-Promise for this period")
    cumulative_atp_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Cumulative ATP through this date")

    # Probabilistic ATP (Phase 5: Stochastic Lead Times)
    atp_p10: Mapped[Optional[int]] = mapped_column(Integer, comment="10th percentile ATP (pessimistic)")
    atp_p90: Mapped[Optional[int]] = mapped_column(Integer, comment="90th percentile ATP (optimistic)")
    lead_time_mean: Mapped[Optional[float]] = mapped_column(Float, comment="Mean lead time used in calculation")
    lead_time_stddev: Mapped[Optional[float]] = mapped_column(Float, comment="Lead time standard deviation")

    # Components
    opening_balance: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)
    supply_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)
    demand_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)
    allocated_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)

    # Extension: Customer Allocation
    customer_id: Mapped[Optional[str]] = mapped_column(String(100), comment="Specific customer allocation")
    allocation_percentage: Mapped[Optional[float]] = mapped_column(Double, comment="Percentage of ATP allocated to customer")
    allocation_priority: Mapped[Optional[int]] = mapped_column(Integer, comment="Allocation priority (1=highest)")

    # Extension: ATP Rules
    atp_rule: Mapped[Optional[str]] = mapped_column(String(50), comment="discrete, cumulative, rolling")
    time_fence_days: Mapped[Optional[int]] = mapped_column(Integer, comment="Planning time fence in days")

    # Source Tracking
    source: Mapped[Optional[str]] = mapped_column(String(100))
    source_event_id: Mapped[Optional[str]] = mapped_column(String(100))
    source_update_dttm: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Simulation Integration
    config_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("supply_chain_configs.id"))
    scenario_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("scenarios.id"))

    # Audit
    created_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    # Relationships
    product = relationship("Product", foreign_keys=[product_id])
    site = relationship("Site", foreign_keys=[site_id])

    __table_args__ = (
        Index('idx_atp_projection_lookup', 'product_id', 'site_id', 'atp_date'),
        Index('idx_atp_projection_customer', 'customer_id', 'atp_date'),
    )


class CtpProjection(Base):
    """
    SC Entity: ctp_projection (Extension)

    Capable-to-Promise (CTP) projection including production capacity.

    CTP Logic:
    - CTP = ATP + Available Production Capacity
    - Considers production lead times
    - Accounts for component availability
    - Respects capacity constraints

    Extensions:
    - Multi-level BOM component checks
    - Resource capacity validation
    - Lead time consideration
    """
    __tablename__ = "ctp_projection"

    # Primary Key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # SC Core Fields
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[str] = mapped_column(String(100), ForeignKey("product.id"), nullable=False)  # SC Product table
    site_id: Mapped[int] = mapped_column(Integer, ForeignKey("site.id"), nullable=False)
    ctp_date: Mapped[date] = mapped_column(Date, nullable=False)

    # CTP Quantities
    ctp_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Capable-to-Promise")
    atp_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="ATP component of CTP")
    production_capacity_qty: Mapped[float] = mapped_column(Double, default=0.0, nullable=False, comment="Available production capacity")

    # Probabilistic CTP (Phase 5: Stochastic Lead Times)
    ctp_p10: Mapped[Optional[int]] = mapped_column(Integer, comment="10th percentile CTP (pessimistic)")
    ctp_p90: Mapped[Optional[int]] = mapped_column(Integer, comment="90th percentile CTP (optimistic)")
    production_lead_time_mean: Mapped[Optional[float]] = mapped_column(Float, comment="Mean production lead time")
    production_lead_time_stddev: Mapped[Optional[float]] = mapped_column(Float, comment="Production lead time standard deviation")

    # Capacity Components
    total_capacity: Mapped[Optional[float]] = mapped_column(Double, comment="Total production capacity")
    committed_capacity: Mapped[Optional[float]] = mapped_column(Double, comment="Already committed capacity")
    available_capacity: Mapped[Optional[float]] = mapped_column(Double, comment="Remaining available capacity")

    # Extension: Component Availability Check
    component_constrained: Mapped[bool] = mapped_column(Boolean, default=False, comment="Limited by component availability")
    constraining_component_id: Mapped[Optional[str]] = mapped_column(String(100), ForeignKey("product.id"), comment="Component limiting CTP")

    # Extension: Resource Capacity Check
    resource_constrained: Mapped[bool] = mapped_column(Boolean, default=False, comment="Limited by resource capacity")
    constraining_resource: Mapped[Optional[str]] = mapped_column(String(255), comment="Resource limiting CTP")

    # Extension: Lead Time
    production_lead_time: Mapped[Optional[int]] = mapped_column(Integer, comment="Production lead time in days")
    earliest_ship_date: Mapped[Optional[date]] = mapped_column(Date, comment="Earliest possible ship date")

    # Source Tracking
    source: Mapped[Optional[str]] = mapped_column(String(100))
    source_event_id: Mapped[Optional[str]] = mapped_column(String(100))
    source_update_dttm: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Simulation Integration
    config_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("supply_chain_configs.id"))
    scenario_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("scenarios.id"))

    # Audit
    created_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    # Relationships
    product = relationship("Product", foreign_keys=[product_id])
    site = relationship("Site", foreign_keys=[site_id])
    constraining_component = relationship("Product", foreign_keys=[constraining_component_id])

    __table_args__ = (
        Index('idx_ctp_projection_lookup', 'product_id', 'site_id', 'ctp_date'),
        Index('idx_ctp_projection_constraint', 'component_constrained', 'resource_constrained'),
    )


class OrderPromise(Base):
    """
    SC Entity: order_promise (Extension)

    Order promising decisions with ATP/CTP allocation.

    Tracks customer order promising decisions including:
    - Quantity available
    - Promised delivery date
    - ATP/CTP source
    - Alternative options

    Extensions:
    - Multi-source fulfillment
    - Partial promise support
    - Alternative date/quantity suggestions
    """
    __tablename__ = "order_promise"

    # Primary Key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Order Reference
    order_id: Mapped[str] = mapped_column(String(100), nullable=False, comment="Customer order ID")
    order_line_number: Mapped[int] = mapped_column(Integer, nullable=False, comment="Order line number")

    # SC Core Fields
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[str] = mapped_column(String(100), ForeignKey("product.id"), nullable=False)  # SC Product table
    site_id: Mapped[int] = mapped_column(Integer, ForeignKey("site.id"), nullable=False)
    customer_id: Mapped[Optional[str]] = mapped_column(String(100), comment="Customer ID")

    # Order Details
    requested_quantity: Mapped[float] = mapped_column(Double, nullable=False)
    requested_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Promise Details
    promised_quantity: Mapped[float] = mapped_column(Double, nullable=False, comment="Quantity promised")
    promised_date: Mapped[date] = mapped_column(Date, nullable=False, comment="Promised delivery date")
    promise_source: Mapped[str] = mapped_column(String(50), nullable=False, comment="ATP, CTP, or BACKORDER")

    # Extension: Fulfillment Strategy
    fulfillment_type: Mapped[str] = mapped_column(String(50), default="single", comment="single, partial, split, substitute")
    partial_promise: Mapped[bool] = mapped_column(Boolean, default=False, comment="Partial quantity promise")
    backorder_quantity: Mapped[Optional[float]] = mapped_column(Double, comment="Quantity on backorder")
    backorder_date: Mapped[Optional[date]] = mapped_column(Date, comment="Expected backorder delivery")

    # Extension: Alternative Options
    alternative_quantity: Mapped[Optional[float]] = mapped_column(Double, comment="Alternative quantity available")
    alternative_date: Mapped[Optional[date]] = mapped_column(Date, comment="Alternative delivery date")
    alternative_product_id: Mapped[Optional[str]] = mapped_column(String(100), ForeignKey("product.id"), comment="Substitute product")

    # Promise Status
    promise_status: Mapped[str] = mapped_column(String(50), default="PROPOSED", comment="PROPOSED, CONFIRMED, FULFILLED, CANCELLED")
    promise_confidence: Mapped[Optional[float]] = mapped_column(Double, comment="Confidence level (0-1)")

    # Source Tracking
    source: Mapped[Optional[str]] = mapped_column(String(100))
    source_event_id: Mapped[Optional[str]] = mapped_column(String(100))
    source_update_dttm: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Audit
    created_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=datetime.utcnow)

    # Relationships
    product = relationship("Product", foreign_keys=[product_id])
    site = relationship("Site", foreign_keys=[site_id])
    alternative_product = relationship("Product", foreign_keys=[alternative_product_id])

    __table_args__ = (
        Index('idx_order_promise_order', 'order_id', 'order_line_number'),
        Index('idx_order_promise_product', 'product_id', 'site_id', 'requested_date'),
        Index('idx_order_promise_customer', 'customer_id', 'promise_status'),
    )

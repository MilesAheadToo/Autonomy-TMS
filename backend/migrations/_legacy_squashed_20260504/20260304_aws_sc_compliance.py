"""AWS Supply Chain Data Model compliance — full audit fixes.

Addresses all compliance gaps identified in the 2026-03-04 audit:

1. outbound_order_line.product_id  INTEGER FK→items  → VARCHAR(100) FK→product
   + add all extension columns defined in ORM (promised_qty, shipped_qty, etc.)
2. inbound_order_line.product_id   INTEGER FK→items  → VARCHAR(100) FK→product
3. sourcing_rules.transportation_lane_id  VARCHAR(100) → INTEGER FK→transportation_lane
4. shipment.transportation_lane_id        VARCHAR(100) → INTEGER FK→transportation_lane
5. shipment.product_id: add FK → product(id)
6. CREATE TABLE inbound_order         (AWS SC entity, parent of inbound_order_line)
7. CREATE TABLE inbound_order_line_schedule  (AWS SC entity, split deliveries)
8. CREATE TABLE shipment_stop              (AWS SC entity, multi-leg visibility)
9. CREATE TABLE shipment_lot               (AWS SC entity, lot-level traceability)
10. CREATE TABLE fulfillment_order          (AWS SC entity, pick→pack→ship lifecycle)

Revision ID: 20260304_aws_sc_compliance
Revises: 20260304_belief_state_tenant_id
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260304_aws_sc_compliance"
down_revision = "20260304_belief_state_tenant_id"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drop_fk_if_exists(table: str, name: str) -> None:
    """Drop FK constraint; ignore if it doesn't exist."""
    try:
        op.drop_constraint(name, table, type_="foreignkey")
    except Exception:
        pass


def _drop_index_if_exists(name: str, table: str) -> None:
    try:
        op.drop_index(name, table_name=table)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:

    # -----------------------------------------------------------------------
    # 1. outbound_order_line — product_id INTEGER→VARCHAR + extension columns
    # -----------------------------------------------------------------------

    # Drop legacy FK to items table (no data, safe)
    _drop_fk_if_exists("outbound_order_line", "outbound_order_line_product_id_fkey")

    op.alter_column(
        "outbound_order_line",
        "product_id",
        existing_type=sa.Integer(),
        type_=sa.String(100),
        existing_nullable=False,
        postgresql_using="product_id::text",
    )

    op.create_foreign_key(
        "outbound_order_line_product_id_fkey",
        "outbound_order_line",
        "product",
        ["product_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Extension columns (all defined in ORM but missing in DB)
    op.add_column("outbound_order_line", sa.Column("promised_quantity", sa.Float(), nullable=True))
    op.add_column("outbound_order_line", sa.Column(
        "shipped_quantity", sa.Float(),
        nullable=False, server_default=sa.text("0.0")
    ))
    op.add_column("outbound_order_line", sa.Column(
        "backlog_quantity", sa.Float(),
        nullable=False, server_default=sa.text("0.0")
    ))
    op.add_column("outbound_order_line", sa.Column(
        "status", sa.String(20),
        nullable=False, server_default=sa.text("'DRAFT'")
    ))
    op.add_column("outbound_order_line", sa.Column(
        "priority_code", sa.String(20),
        nullable=False, server_default=sa.text("'STANDARD'")
    ))
    op.add_column("outbound_order_line", sa.Column("promised_delivery_date", sa.Date(), nullable=True))
    op.add_column("outbound_order_line", sa.Column("first_ship_date", sa.Date(), nullable=True))
    op.add_column("outbound_order_line", sa.Column("last_ship_date", sa.Date(), nullable=True))
    op.add_column("outbound_order_line", sa.Column(
        "market_demand_site_id", sa.Integer(), nullable=True
    ))
    op.create_foreign_key(
        "outbound_order_line_market_demand_site_id_fkey",
        "outbound_order_line",
        "site",
        ["market_demand_site_id"],
        ["id"],
    )

    # Missing indexes from ORM definition
    op.create_index("idx_outbound_status",   "outbound_order_line", ["status"])
    op.create_index("idx_outbound_priority", "outbound_order_line", ["priority_code"])
    op.create_index("idx_outbound_backlog",  "outbound_order_line", ["backlog_quantity"])
    op.create_index("idx_outbound_order_date_priority", "outbound_order_line",
                    ["order_date", "priority_code"])

    # -----------------------------------------------------------------------
    # 2. inbound_order_line — product_id INTEGER→VARCHAR
    # -----------------------------------------------------------------------

    _drop_fk_if_exists("inbound_order_line", "inbound_order_line_product_id_fkey")

    op.alter_column(
        "inbound_order_line",
        "product_id",
        existing_type=sa.Integer(),
        type_=sa.String(100),
        existing_nullable=False,
        postgresql_using="product_id::text",
    )

    op.create_foreign_key(
        "inbound_order_line_product_id_fkey",
        "inbound_order_line",
        "product",
        ["product_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # -----------------------------------------------------------------------
    # 3. sourcing_rules.transportation_lane_id  VARCHAR→INTEGER FK
    # -----------------------------------------------------------------------

    # Existing data: integer strings like "255", "252". All valid lane IDs.
    op.alter_column(
        "sourcing_rules",
        "transportation_lane_id",
        existing_type=sa.String(100),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="NULLIF(transportation_lane_id, '')::INTEGER",
    )

    op.create_foreign_key(
        "sourcing_rules_transportation_lane_id_fkey",
        "sourcing_rules",
        "transportation_lane",
        ["transportation_lane_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # -----------------------------------------------------------------------
    # 4. shipment.transportation_lane_id  VARCHAR→INTEGER FK  (all NULL)
    # -----------------------------------------------------------------------

    op.alter_column(
        "shipment",
        "transportation_lane_id",
        existing_type=sa.String(100),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="NULLIF(transportation_lane_id, '')::INTEGER",
    )

    op.create_foreign_key(
        "shipment_transportation_lane_id_fkey",
        "shipment",
        "transportation_lane",
        ["transportation_lane_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # -----------------------------------------------------------------------
    # 5. shipment.product_id — add missing FK (already VARCHAR, data valid)
    # -----------------------------------------------------------------------

    op.create_foreign_key(
        "shipment_product_id_fkey",
        "shipment",
        "product",
        ["product_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # -----------------------------------------------------------------------
    # 6. CREATE TABLE inbound_order  (AWS SC entity — PO/TO header)
    # -----------------------------------------------------------------------

    op.create_table(
        "inbound_order",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("company_id", sa.String(100), sa.ForeignKey("company.id"), nullable=True),
        sa.Column("order_type", sa.String(50), nullable=False),  # PURCHASE, TRANSFER, RETURN
        sa.Column("supplier_id", sa.String(100), nullable=True),
        sa.Column("supplier_name", sa.String(200), nullable=True),
        sa.Column("ship_from_site_id", sa.Integer(), sa.ForeignKey("site.id"), nullable=True),
        sa.Column("ship_to_site_id", sa.Integer(), sa.ForeignKey("site.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False,
                  server_default=sa.text("'DRAFT'")),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        sa.Column("promised_delivery_date", sa.Date(), nullable=True),
        sa.Column("actual_delivery_date", sa.Date(), nullable=True),
        sa.Column("total_ordered_qty", sa.Float(), server_default=sa.text("0.0")),
        sa.Column("total_received_qty", sa.Float(), server_default=sa.text("0.0")),
        sa.Column("total_value", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(10), server_default=sa.text("'USD'")),
        sa.Column("reference_number", sa.String(100), nullable=True),
        sa.Column("contract_id", sa.String(100), nullable=True),
        sa.Column("config_id", sa.Integer(),
                  sa.ForeignKey("supply_chain_configs.id"), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("source_event_id", sa.String(100), nullable=True),
        sa.Column("source_update_dttm", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), onupdate=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_inbound_order_hdr_status",   "inbound_order", ["status", "order_type"])
    op.create_index("idx_inbound_order_hdr_supplier", "inbound_order", ["supplier_id"])
    op.create_index("idx_inbound_order_hdr_site",     "inbound_order",
                    ["ship_to_site_id", "requested_delivery_date"])

    # -----------------------------------------------------------------------
    # 7. CREATE TABLE inbound_order_line_schedule  (AWS SC — split deliveries)
    # -----------------------------------------------------------------------

    op.create_table(
        "inbound_order_line_schedule",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "order_line_id", sa.Integer(),
            sa.ForeignKey("inbound_order_line.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schedule_number", sa.Integer(), nullable=False),
        sa.Column("scheduled_quantity", sa.Float(), nullable=False),
        sa.Column("received_quantity", sa.Float(), server_default=sa.text("0.0")),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("actual_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(30),
                  server_default=sa.text("'SCHEDULED'")),  # SCHEDULED, IN_TRANSIT, RECEIVED, DELAYED, CANCELLED
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_inbound_schedule_line", "inbound_order_line_schedule", ["order_line_id"])
    op.create_index("idx_inbound_schedule_date", "inbound_order_line_schedule",
                    ["scheduled_date", "status"])

    # -----------------------------------------------------------------------
    # 8. CREATE TABLE shipment_stop  (AWS SC — multi-leg visibility)
    # -----------------------------------------------------------------------

    op.create_table(
        "shipment_stop",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "shipment_id", sa.String(100),
            sa.ForeignKey("shipment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stop_number", sa.Integer(), nullable=False),
        sa.Column("stop_type", sa.String(30), nullable=False),  # PICKUP, DELIVERY, CROSS_DOCK, CUSTOMS
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("site.id"), nullable=True),
        sa.Column("location_name", sa.String(200), nullable=True),
        sa.Column("location_lat", sa.Float(), nullable=True),
        sa.Column("location_lon", sa.Float(), nullable=True),
        sa.Column("planned_arrival", sa.DateTime(), nullable=True),
        sa.Column("actual_arrival", sa.DateTime(), nullable=True),
        sa.Column("planned_departure", sa.DateTime(), nullable=True),
        sa.Column("actual_departure", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), server_default=sa.text("'PLANNED'")),
        sa.Column("dwell_time_hours", sa.Float(), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_shipment_stop_shipment", "shipment_stop",
                    ["shipment_id", "stop_number"])

    # -----------------------------------------------------------------------
    # 9. CREATE TABLE shipment_lot  (AWS SC — lot-level traceability)
    # -----------------------------------------------------------------------

    op.create_table(
        "shipment_lot",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "shipment_id", sa.String(100),
            sa.ForeignKey("shipment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", sa.String(100),
                  sa.ForeignKey("product.id"), nullable=False),
        sa.Column("lot_number", sa.String(100), nullable=False),
        sa.Column("batch_id", sa.String(100), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("uom", sa.String(20), nullable=True),
        sa.Column("manufacture_date", sa.Date(), nullable=True),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("shelf_life_days", sa.Integer(), nullable=True),
        sa.Column("quality_status", sa.String(30),
                  server_default=sa.text("'RELEASED'")),  # RELEASED, QUARANTINE, REJECTED, RECALL
        sa.Column("certificate_of_analysis", sa.String(200), nullable=True),
        sa.Column("origin_site_id", sa.Integer(),
                  sa.ForeignKey("site.id"), nullable=True),
        sa.Column("country_of_origin", sa.String(10), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_shipment_lot_shipment",  "shipment_lot", ["shipment_id"])
    op.create_index("idx_shipment_lot_product",   "shipment_lot", ["product_id", "lot_number"])
    op.create_index("idx_shipment_lot_expiry",    "shipment_lot", ["expiration_date", "quality_status"])

    # -----------------------------------------------------------------------
    # 10. CREATE TABLE fulfillment_order  (AWS SC — pick→pack→ship lifecycle)
    # -----------------------------------------------------------------------

    op.create_table(
        "fulfillment_order",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.String(100), sa.ForeignKey("company.id"), nullable=True),
        sa.Column("fulfillment_order_id", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("order_id", sa.String(100), nullable=False, index=True),
        sa.Column("order_line_id", sa.String(100), nullable=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("site.id"), nullable=False),
        sa.Column("product_id", sa.String(100),
                  sa.ForeignKey("product.id"), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("uom", sa.String(20), server_default=sa.text("'EA'")),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'CREATED'")),
        # Dates
        sa.Column("created_date", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("promised_date", sa.DateTime(), nullable=True),
        sa.Column("allocated_date", sa.DateTime(), nullable=True),
        sa.Column("pick_date", sa.DateTime(), nullable=True),
        sa.Column("pack_date", sa.DateTime(), nullable=True),
        sa.Column("ship_date", sa.DateTime(), nullable=True),
        sa.Column("delivery_date", sa.DateTime(), nullable=True),
        # Quantities
        sa.Column("allocated_quantity", sa.Float(), server_default=sa.text("0")),
        sa.Column("picked_quantity",    sa.Float(), server_default=sa.text("0")),
        sa.Column("shipped_quantity",   sa.Float(), server_default=sa.text("0")),
        sa.Column("delivered_quantity", sa.Float(), server_default=sa.text("0")),
        sa.Column("short_quantity",     sa.Float(), server_default=sa.text("0")),
        # Warehouse (extension)
        sa.Column("wave_id", sa.String(100), nullable=True),
        sa.Column("pick_location", sa.String(100), nullable=True),
        sa.Column("pack_station", sa.String(50), nullable=True),
        # Shipment tracking (extension)
        sa.Column("carrier", sa.String(100), nullable=True),
        sa.Column("tracking_number", sa.String(200), nullable=True),
        sa.Column("ship_method", sa.String(50), nullable=True),
        # Priority and customer (extension)
        sa.Column("priority", sa.Integer(), server_default=sa.text("3")),
        sa.Column("customer_id", sa.String(100), nullable=True),  # FK to trading_partner
        # Source tracking
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_fulfillment_order_lookup",   "fulfillment_order",
                    ["order_id", "status"])
    op.create_index("idx_fulfillment_site_product",   "fulfillment_order",
                    ["site_id", "product_id", "status"])


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    # Drop new tables
    op.drop_table("fulfillment_order")
    op.drop_table("shipment_lot")
    op.drop_table("shipment_stop")
    op.drop_table("inbound_order_line_schedule")
    op.drop_table("inbound_order")

    # Revert shipment FKs
    _drop_fk_if_exists("shipment", "shipment_product_id_fkey")
    _drop_fk_if_exists("shipment", "shipment_transportation_lane_id_fkey")
    op.alter_column("shipment", "transportation_lane_id",
                    existing_type=sa.Integer(), type_=sa.String(100),
                    existing_nullable=True,
                    postgresql_using="transportation_lane_id::text")

    # Revert sourcing_rules
    _drop_fk_if_exists("sourcing_rules", "sourcing_rules_transportation_lane_id_fkey")
    op.alter_column("sourcing_rules", "transportation_lane_id",
                    existing_type=sa.Integer(), type_=sa.String(100),
                    existing_nullable=True,
                    postgresql_using="transportation_lane_id::text")

    # Revert inbound_order_line
    _drop_fk_if_exists("inbound_order_line", "inbound_order_line_product_id_fkey")
    op.alter_column("inbound_order_line", "product_id",
                    existing_type=sa.String(100), type_=sa.Integer(),
                    existing_nullable=False,
                    postgresql_using="product_id::integer")
    op.create_foreign_key(
        "inbound_order_line_product_id_fkey",
        "inbound_order_line", "items", ["product_id"], ["id"],
    )

    # Revert outbound_order_line
    for idx in ["idx_outbound_order_date_priority", "idx_outbound_backlog",
                "idx_outbound_priority", "idx_outbound_status"]:
        _drop_index_if_exists(idx, "outbound_order_line")

    for col in ["market_demand_site_id", "last_ship_date", "first_ship_date",
                "promised_delivery_date", "priority_code", "status",
                "backlog_quantity", "shipped_quantity", "promised_quantity"]:
        op.drop_column("outbound_order_line", col)

    _drop_fk_if_exists("outbound_order_line", "outbound_order_line_product_id_fkey")
    op.alter_column("outbound_order_line", "product_id",
                    existing_type=sa.String(100), type_=sa.Integer(),
                    existing_nullable=False,
                    postgresql_using="product_id::integer")
    op.create_foreign_key(
        "outbound_order_line_product_id_fkey",
        "outbound_order_line", "items", ["product_id"], ["id"],
    )

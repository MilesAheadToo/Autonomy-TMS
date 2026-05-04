"""Site / TradingPartner refactor — Phases 1-4

Revision ID: 20260311_site_tp_refactor
Revises: (see down_revision below — set to the latest existing migration)
Create Date: 2026-03-11

Changes:
  Phase 1 — Site model:
    * site.is_external  (Boolean, NOT NULL, default FALSE)
    * site.trading_partner_id  (Integer FK → trading_partners._id, nullable)
    * site.tpartner_type  (String(50), nullable) — "vendor" or "customer"
    * Backfill: existing MARKET_SUPPLY rows → is_external=True, tpartner_type='vendor'
    * Backfill: existing MARKET_DEMAND rows → is_external=True, tpartner_type='customer'
    * Backfill: create TradingPartner rows for external sites that lack one

  Phase 3 — TransportationLane:
    * transportation_lane.from_partner_id  (Integer FK → trading_partners._id, nullable)
    * transportation_lane.to_partner_id    (Integer FK → trading_partners._id, nullable)
    * transportation_lane.from_site_id  → ALTER to nullable (was NOT NULL)
    * transportation_lane.to_site_id    → ALTER to nullable (was NOT NULL)
    * Drop old unique constraint _site_connection_uc
    * Add new unique constraint _lane_endpoints_uc
    * Backfill: lanes whose from/to_site points to an is_external site
      → populate from/to_partner_id from that site's trading_partner_id

  Phase 4 — MarketDemand:
    * market_demands.trading_partner_id  (Integer FK → trading_partners._id, nullable)
    * Backfill: for each market row, create TradingPartner(customer) if not already present,
      then set trading_partner_id on all market_demands rows for that market.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------------
revision = "20260311_site_tp_refactor"
down_revision = "ddfb5f63890a"   # rename_index_use_sc_planning — last known migration before merge
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # -----------------------------------------------------------------------
    # Phase 1: Site — add external-party columns
    # -----------------------------------------------------------------------
    op.add_column("site", sa.Column("is_external", sa.Boolean(), nullable=False,
                                    server_default=sa.text("FALSE")))
    op.add_column("site", sa.Column("trading_partner_id", sa.Integer(),
                                    sa.ForeignKey("trading_partners._id"), nullable=True))
    op.add_column("site", sa.Column("tpartner_type", sa.String(50), nullable=True))

    # Backfill: mark existing MARKET_SUPPLY / MARKET_DEMAND sites as external
    conn.execute(sa.text("""
        UPDATE site
        SET is_external = TRUE,
            tpartner_type = 'vendor'
        WHERE UPPER(master_type) = 'MARKET_SUPPLY'
    """))
    conn.execute(sa.text("""
        UPDATE site
        SET is_external = TRUE,
            tpartner_type = 'customer'
        WHERE UPPER(master_type) = 'MARKET_DEMAND'
    """))

    # Backfill: create TradingPartner records for external sites that lack one,
    # then link them via trading_partner_id.
    # We use a unique business key of the form "SITE_<site.id>" so we can
    # re-run safely (ON CONFLICT DO NOTHING).
    conn.execute(sa.text("""
        INSERT INTO trading_partners (id, tpartner_type, description, is_active)
        SELECT
            'SITE_' || s.id,
            s.tpartner_type,
            s.name,
            'true'
        FROM site s
        WHERE s.is_external = TRUE
          AND s.trading_partner_id IS NULL
        ON CONFLICT (id) DO NOTHING
    """))
    conn.execute(sa.text("""
        UPDATE site s
        SET trading_partner_id = tp._id
        FROM trading_partners tp
        WHERE s.is_external = TRUE
          AND s.trading_partner_id IS NULL
          AND tp.id = 'SITE_' || s.id
    """))

    # -----------------------------------------------------------------------
    # Phase 3: TransportationLane — partner endpoint columns + nullable FKs
    # -----------------------------------------------------------------------
    op.add_column("transportation_lane",
                  sa.Column("from_partner_id", sa.Integer(),
                            sa.ForeignKey("trading_partners._id"), nullable=True))
    op.add_column("transportation_lane",
                  sa.Column("to_partner_id", sa.Integer(),
                            sa.ForeignKey("trading_partners._id"), nullable=True))

    # Make site FK columns nullable
    op.alter_column("transportation_lane", "from_site_id", nullable=True)
    op.alter_column("transportation_lane", "to_site_id", nullable=True)

    # Drop old unique constraint (covers only site FKs)
    op.drop_constraint("_site_connection_uc", "transportation_lane", type_="unique")
    # Add new constraint covering all four endpoint columns
    op.create_unique_constraint(
        "_lane_endpoints_uc",
        "transportation_lane",
        ["from_site_id", "to_site_id", "from_partner_id", "to_partner_id"],
    )

    # Backfill: lanes whose endpoint is an external (proxy) site → populate partner FK
    conn.execute(sa.text("""
        UPDATE transportation_lane tl
        SET from_partner_id = s.trading_partner_id,
            from_site_id    = NULL
        FROM site s
        WHERE tl.from_site_id = s.id
          AND s.is_external = TRUE
          AND s.trading_partner_id IS NOT NULL
    """))
    conn.execute(sa.text("""
        UPDATE transportation_lane tl
        SET to_partner_id = s.trading_partner_id,
            to_site_id    = NULL
        FROM site s
        WHERE tl.to_site_id = s.id
          AND s.is_external = TRUE
          AND s.trading_partner_id IS NOT NULL
    """))

    # -----------------------------------------------------------------------
    # Phase 4: MarketDemand — add trading_partner_id FK; make market_id nullable
    # -----------------------------------------------------------------------
    op.add_column("market_demands",
                  sa.Column("trading_partner_id", sa.Integer(),
                            sa.ForeignKey("trading_partners._id"), nullable=True))
    op.alter_column("market_demands", "market_id", nullable=True)

    # Backfill: for each Market row, ensure a TradingPartner(customer) exists,
    # then link all matching MarketDemand rows.
    conn.execute(sa.text("""
        INSERT INTO trading_partners (id, tpartner_type, description, is_active, company_id)
        SELECT
            'MARKET_' || m.id,
            'customer',
            m.name,
            'true',
            NULL
        FROM markets m
        ON CONFLICT (id) DO NOTHING
    """))
    conn.execute(sa.text("""
        UPDATE market_demands md
        SET trading_partner_id = tp._id
        FROM markets m
        JOIN trading_partners tp ON tp.id = 'MARKET_' || m.id
        WHERE md.market_id = m.id
          AND md.trading_partner_id IS NULL
    """))


def downgrade() -> None:
    conn = op.get_bind()

    # Phase 4 rollback
    op.drop_column("market_demands", "trading_partner_id")
    op.alter_column("market_demands", "market_id", nullable=False)

    # Phase 3 rollback
    # Restore site FKs from partner FKs before making them NOT NULL again
    conn.execute(sa.text("""
        UPDATE transportation_lane tl
        SET from_site_id = s.id,
            from_partner_id = NULL
        FROM site s
        JOIN trading_partners tp ON tp._id = tl.from_partner_id
        WHERE s.trading_partner_id = tp._id
    """))
    conn.execute(sa.text("""
        UPDATE transportation_lane tl
        SET to_site_id = s.id,
            to_partner_id = NULL
        FROM site s
        JOIN trading_partners tp ON tp._id = tl.to_partner_id
        WHERE s.trading_partner_id = tp._id
    """))

    op.drop_constraint("_lane_endpoints_uc", "transportation_lane", type_="unique")
    op.create_unique_constraint("_site_connection_uc", "transportation_lane",
                                ["from_site_id", "to_site_id"])
    op.alter_column("transportation_lane", "from_site_id", nullable=False)
    op.alter_column("transportation_lane", "to_site_id", nullable=False)
    op.drop_column("transportation_lane", "from_partner_id")
    op.drop_column("transportation_lane", "to_partner_id")

    # Phase 1 rollback
    op.drop_column("site", "is_external")
    op.drop_column("site", "trading_partner_id")
    op.drop_column("site", "tpartner_type")

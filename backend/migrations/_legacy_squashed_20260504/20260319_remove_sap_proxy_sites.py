"""Remove SUPPLY_/DEMAND_ proxy sites from SAP-imported configs.

Creates partner-endpoint lanes from TradingPartner records, then deletes
the old proxy sites and their site-to-site lanes.

Only affects configs that have SUPPLY_*/DEMAND_* sites (SAP-imported).
Beer Game and Food Dist configs are untouched.

Revision ID: 20260319_tp
Revises: None (data migration, no schema changes)
Create Date: 2026-03-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)

# revision identifiers
revision = "20260319_tp"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Migrate SAP configs from proxy sites to partner-endpoint lanes."""
    bind = op.get_bind()
    session = Session(bind=bind)

    try:
        # Find configs with SUPPLY_/DEMAND_ proxy sites
        proxy_configs = session.execute(sa.text("""
            SELECT DISTINCT config_id FROM site
            WHERE (name LIKE 'SUPPLY_%%' OR name LIKE 'DEMAND_%%')
              AND is_external = true
        """)).fetchall()

        if not proxy_configs:
            logger.info("No SAP proxy sites found — nothing to migrate")
            return

        for (config_id,) in proxy_configs:
            logger.info(f"Migrating config {config_id}: proxy sites → partner-endpoint lanes")

            # 1. Find vendor proxy sites and their existing lanes
            vendor_lanes = session.execute(sa.text("""
                SELECT tl.id, tl.from_site_id, tl.to_site_id, tl.capacity,
                       tl.lead_time_days, tl.supply_lead_time, tl.demand_lead_time,
                       s.name as from_site_name
                FROM transportation_lane tl
                JOIN site s ON s.id = tl.from_site_id
                WHERE tl.config_id = :cid
                  AND s.name LIKE 'SUPPLY_%%'
                  AND s.is_external = true
            """), {"cid": config_id}).fetchall()

            # 2. Find customer proxy sites and their existing lanes
            customer_lanes = session.execute(sa.text("""
                SELECT tl.id, tl.from_site_id, tl.to_site_id, tl.capacity,
                       tl.lead_time_days, tl.supply_lead_time, tl.demand_lead_time,
                       s.name as to_site_name
                FROM transportation_lane tl
                JOIN site s ON s.id = tl.to_site_id
                WHERE tl.config_id = :cid
                  AND s.name LIKE 'DEMAND_%%'
                  AND s.is_external = true
            """), {"cid": config_id}).fetchall()

            # 3. Find active vendors from sourcing_rules for this config
            active_vendors = session.execute(sa.text("""
                SELECT DISTINCT sr.tpartner_id, tp._id
                FROM sourcing_rules sr
                JOIN trading_partners tp ON tp.id = sr.tpartner_id
                WHERE sr.config_id = :cid
                  AND sr.sourcing_rule_type = 'buy'
                  AND sr.tpartner_id IS NOT NULL
            """), {"cid": config_id}).fetchall()

            # 4. Get internal plant sites for this config
            plant_sites = session.execute(sa.text("""
                SELECT id, name FROM site
                WHERE config_id = :cid
                  AND is_external = false
                  AND master_type IN ('MANUFACTURER', 'INVENTORY')
            """), {"cid": config_id}).fetchall()

            # 5. Create vendor → plant lanes using partner endpoints
            new_lane_count = 0
            for vendor_biz_key, vendor_int_id in active_vendors:
                for plant_id, plant_name in plant_sites:
                    # Check if this lane already exists
                    existing = session.execute(sa.text("""
                        SELECT 1 FROM transportation_lane
                        WHERE config_id = :cid
                          AND from_partner_id = :pid
                          AND to_site_id = :sid
                        LIMIT 1
                    """), {"cid": config_id, "pid": vendor_int_id, "sid": plant_id}).first()

                    if not existing:
                        # Use lead time from existing proxy lanes if available
                        session.execute(sa.text("""
                            INSERT INTO transportation_lane
                                (config_id, from_partner_id, to_site_id, capacity,
                                 lead_time_days, supply_lead_time, demand_lead_time)
                            VALUES (:cid, :pid, :sid, 10000,
                                    '{"min": 5, "max": 10}'::jsonb,
                                    '{"type": "deterministic", "value": 7}'::jsonb,
                                    '{"type": "deterministic", "value": 1}'::jsonb)
                        """), {"cid": config_id, "pid": vendor_int_id, "sid": plant_id})
                        new_lane_count += 1

            # 6. Delete old proxy lanes (from SUPPLY_/DEMAND_ sites)
            deleted_lanes = 0
            for lane in vendor_lanes:
                session.execute(sa.text(
                    "DELETE FROM transportation_lane WHERE id = :lid"
                ), {"lid": lane[0]})
                deleted_lanes += 1
            for lane in customer_lanes:
                session.execute(sa.text(
                    "DELETE FROM transportation_lane WHERE id = :lid"
                ), {"lid": lane[0]})
                deleted_lanes += 1

            # 7. Delete the proxy site records
            deleted_sites = session.execute(sa.text("""
                DELETE FROM site
                WHERE config_id = :cid
                  AND (name LIKE 'SUPPLY_%%' OR name LIKE 'DEMAND_%%')
                  AND is_external = true
            """), {"cid": config_id}).rowcount

            logger.info(
                f"Config {config_id}: created {new_lane_count} partner lanes, "
                f"deleted {deleted_lanes} proxy lanes, deleted {deleted_sites} proxy sites"
            )

        session.commit()
        logger.info(f"Migration complete: {len(proxy_configs)} configs migrated")

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def downgrade():
    """Revert is not supported — proxy sites cannot be reconstructed without
    the original SAP CSV data. Use reprovisioning instead."""
    pass

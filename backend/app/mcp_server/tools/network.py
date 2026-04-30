"""
MCP Tool: Network Status — DAG Topology Health.

Returns the supply chain network topology (sites, transportation lanes,
master types) with health indicators, active alerts, and bottleneck status.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def register(mcp):
    """Register network status tools on the MCP server."""

    @mcp.tool()
    async def get_network_status(
        tenant_id: int,
        config_id: int,
    ) -> dict:
        """Get the supply chain network topology and health status.

        Returns the DAG structure (sites + transportation lanes) with:
        - Site details: name, type (Manufacturer/DC/Retailer), master_type, capabilities
        - Transportation lanes: connections between sites with lead times
        - Active alerts: CDC triggers, condition monitor breaches
        - Bottleneck indicators: sites with capacity constraints

        Args:
            tenant_id: Organization ID
            config_id: Supply chain config ID

        Returns:
            Network topology with health indicators.
        """
        from sqlalchemy import text as sql_text
        from .db import get_db

        async with get_db() as db:
            # Tenant-isolation gate. site / transportation_lane have no
            # tenant_id column; they scope through supply_chain_configs.
            # Verify the config belongs to the requesting tenant before
            # returning rows. Without this check, a request with
            # mismatched (tenant_id, config_id) would leak the config
            # owner's topology. Fixed 2026-04-30 typed-empty audit.
            tenant_check = await db.execute(
                sql_text(
                    "SELECT 1 FROM supply_chain_configs "
                    "WHERE id = :config_id AND tenant_id = :tenant_id"
                ),
                {"config_id": config_id, "tenant_id": tenant_id},
            )
            if tenant_check.scalar() is None:
                return {
                    "site_count": 0,
                    "lane_count": 0,
                    "alert_count": 0,
                    "sites": [],
                    "lanes": [],
                    "alerts": [],
                    "echoed": {
                        "tenant_id": tenant_id,
                        "config_id": config_id,
                        "tenant_isolation_check": "failed",
                    },
                }

            # Sites — column names match canonical Core Site (azirella_data_model
            # .master.config.Site): name, type, master_type. Earlier draft of
            # this query used `description` and `site_type` which do not exist
            # in the canonical schema; fixed 2026-04-30 typed-empty audit.
            sites_result = await db.execute(
                sql_text("""
                    SELECT id, name, type, master_type,
                           latitude, longitude, geo_id
                    FROM site
                    WHERE config_id = :config_id
                    ORDER BY master_type, name
                """),
                {"config_id": config_id},
            )
            sites = [
                {
                    "id": r.id,
                    "name": r.name,
                    "type": r.type,
                    "master_type": r.master_type,
                    "latitude": r.latitude,
                    "longitude": r.longitude,
                    "geo_id": r.geo_id,
                }
                for r in sites_result.fetchall()
            ]

            # Transportation lanes — canonical TransportationLane carries
            # `lead_time_days` (no `transit_time`/`transit_time_uom`) and
            # has no mode column. Mode is derived per-shipment in TMS, not
            # stored on the lane. Earlier draft selected non-existent
            # columns; fixed 2026-04-30 typed-empty audit.
            lanes_result = await db.execute(
                sql_text("""
                    SELECT id, from_site_id, to_site_id, lead_time_days,
                           demand_lead_time, supply_lead_time
                    FROM transportation_lane
                    WHERE config_id = :config_id
                """),
                {"config_id": config_id},
            )
            lanes = [
                {
                    "id": r.id,
                    "from_site": r.from_site_id,
                    "to_site": r.to_site_id,
                    "mode": None,  # not modelled at lane level
                    "lead_time_days": r.lead_time_days,
                    "demand_lead_time": r.demand_lead_time,
                    "supply_lead_time": r.supply_lead_time,
                }
                for r in lanes_result.fetchall()
            ]

            # Active CDC triggers (last 24h)
            try:
                alerts_result = await db.execute(
                    sql_text("""
                        SELECT trigger_reason, severity, site_key, message, created_at
                        FROM powell_cdc_trigger_log
                        WHERE config_id = :config_id
                          AND created_at > NOW() - INTERVAL '24 hours'
                        ORDER BY created_at DESC
                        LIMIT 20
                    """),
                    {"config_id": config_id},
                )
                alerts = [
                    {
                        "reason": r.trigger_reason,
                        "severity": r.severity,
                        "site": r.site_key,
                        "message": r.message,
                        "timestamp": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in alerts_result.fetchall()
                ]
            except Exception:
                alerts = []

            return {
                "site_count": len(sites),
                "lane_count": len(lanes),
                "alert_count": len(alerts),
                "sites": sites,
                "lanes": lanes,
                "alerts": alerts,
            }

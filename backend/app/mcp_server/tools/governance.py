"""
MCP Tool: Governance Status.

Returns the current governance configuration, override rates, touchless
rates, and active guardrail directives. Useful for compliance and audit.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def register(mcp):
    """Register governance tools on the MCP server."""

    @mcp.tool()
    async def get_governance_status(
        tenant_id: int,
        config_id: Optional[int] = None,
    ) -> dict:
        """Get the current AIIO governance status for a tenant.

        Returns active policies, their thresholds, active guardrail directives,
        write-back delay settings, and the operating schedule.

        Args:
            tenant_id: Organization ID (REQUIRED).
            config_id: Optional supply-chain config scope. **Currently advisory** —
                the underlying tables (`decision_governance_policies`,
                `guardrail_directives`, `tenant_oversight_config`) are tenant-scoped,
                not config-scoped, so this argument is accepted for symmetry with
                other MCP tools but does not filter rows. If the schema gains
                `config_id` later, this becomes enforced. The `config_id_enforced`
                flag in the response makes the current state explicit.

        Returns:
            Governance configuration, active directives, write-back settings, and
            an `echoed` block with the input parameters + enforcement flags.
        """
        from sqlalchemy import text as sql_text
        from .db import get_db

        async with get_db() as db:
            # Active policies
            policies_result = await db.execute(
                sql_text("""
                    SELECT action_type, automate_below, inform_below,
                           hold_minutes, writeback_base_delay_minutes,
                           writeback_min_delay_minutes, writeback_max_delay_minutes,
                           writeback_enabled, name
                    FROM decision_governance_policies
                    WHERE tenant_id = :tenant_id AND is_active = true
                    ORDER BY priority
                """),
                {"tenant_id": tenant_id},
            )
            policies = [
                {
                    "action_type": r.action_type or "(catch-all)",
                    "name": r.name,
                    "automate_below": r.automate_below,
                    "inform_below": r.inform_below,
                    "hold_minutes": r.hold_minutes,
                    "writeback_enabled": r.writeback_enabled,
                    "writeback_delay_range": f"{r.writeback_min_delay_minutes}-{r.writeback_max_delay_minutes} min",
                }
                for r in policies_result.fetchall()
            ]

            # Active directives
            try:
                directives_result = await db.execute(
                    sql_text("""
                        SELECT objective, context, status, effective_from, effective_until,
                               source_channel, received_at
                        FROM guardrail_directives
                        WHERE tenant_id = :tenant_id AND status = 'APPLIED'
                          AND (effective_until IS NULL OR effective_until > NOW())
                        ORDER BY received_at DESC
                        LIMIT 10
                    """),
                    {"tenant_id": tenant_id},
                )
                directives = [
                    {
                        "objective": r.objective,
                        "context": r.context,
                        "channel": r.source_channel,
                        "effective_until": r.effective_until.isoformat() if r.effective_until else "indefinite",
                    }
                    for r in directives_result.fetchall()
                ]
            except Exception:
                directives = []

            # Operating schedule
            try:
                schedule_result = await db.execute(
                    sql_text("""
                        SELECT timezone, respect_business_hours,
                               urgent_bypass_enabled, urgent_bypass_threshold,
                               max_calendar_delay_hours, oncall_enabled
                        FROM tenant_oversight_config
                        WHERE tenant_id = :tenant_id
                    """),
                    {"tenant_id": tenant_id},
                )
                sched = schedule_result.fetchone()
                oversight = {
                    "timezone": sched.timezone if sched else "UTC",
                    "respect_business_hours": sched.respect_business_hours if sched else True,
                    "urgent_bypass_threshold": sched.urgent_bypass_threshold if sched else 0.85,
                    "max_calendar_delay_hours": sched.max_calendar_delay_hours if sched else 72,
                    "oncall_enabled": sched.oncall_enabled if sched else False,
                } if sched else {"configured": False}
            except Exception:
                oversight = {"configured": False}

            return {
                "tenant_id": tenant_id,
                "active_policies": policies,
                "active_directives": directives,
                "oversight_schedule": oversight,
                "echoed": {
                    "config_id": config_id,
                    "config_id_enforced": False,
                },
            }

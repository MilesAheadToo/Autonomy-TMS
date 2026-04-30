"""
MCP Tool: Decision Stream Query.

Exposes the Decision Stream — the primary human-facing view of agent
decisions across all 11 TRM types. Returns pending decisions, alerts,
and a natural-language digest.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def register(mcp):
    """Register Decision Stream tools on the MCP server."""

    @mcp.tool()
    async def get_decision_stream(
        tenant_id: int,
        config_id: Optional[int] = None,
        decision_level: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        """Query the Decision Stream for recent agent decisions.

        Returns pending decisions from all 11 TRM agent types with urgency,
        confidence, suggested actions, and human-readable summaries. Filter
        by decision level (governance, strategic, tactical, execution).

        Args:
            tenant_id: Organization ID (required for tenant isolation)
            config_id: Supply chain config ID (optional — uses active config if omitted)
            decision_level: Filter by level: governance, strategic, tactical, execution
            limit: Max decisions to return (default 50)

        Returns:
            Dictionary with decisions list, level counts, total pending, and display metadata.
        """
        from .db import get_db
        from app.services.decision_stream_service import DecisionStreamService

        async with get_db() as db:
            service = DecisionStreamService(db, tenant_id=tenant_id)
            digest = await service.get_decision_digest(
                decision_level=decision_level,
                config_id=config_id,
            )

            decisions = digest.get("decisions", [])[:limit]

            return {
                "total_pending": digest.get("total_pending", 0),
                "level_counts": digest.get("level_counts", {}),
                "decisions": decisions,
                "display_identifiers": digest.get("display_identifiers", "name"),
            }

    @mcp.tool()
    async def chat_with_decisions(
        tenant_id: int,
        message: str,
        config_id: Optional[int] = None,
        conversation_id: Optional[str] = None,
    ) -> dict:
        """Ask a natural-language question about agent decisions.

        > **SESSION-INTERNAL — not safe for cross-plane structured callers.**
        > Returns freeform conversational text in `response`, not a typed
        > structured payload. Cross-plane callers (SCP → TMS, future
        > WMS → TMS, etc.) cannot parse the response reliably across MCP
        > tool versions; structured queries should use the other Decision
        > Stream / AD-11 tools (`get_decision_stream`, `get_realized_shipments`,
        > `get_carrier_capacity`, etc.). This tool is intended for the
        > Autonomy UI's chat surface only — same JWT scope as the user's
        > REST session, not for service-to-service tokens.
        > See [`Autonomy-TMS/.claude/rules/cross-plane-mcp-only.md`](
        > ../../../../.claude/rules/cross-plane-mcp-only.md) §"Smaller hygiene flags"
        > and Autonomy-Core MIGRATION_REGISTER §3.8 hygiene rollup.

        The Decision Stream chat can answer questions like:
        - "What are the most urgent decisions right now?"
        - "Why did the procurement agent create a PO for 500 units?"
        - "Show me all quality holds at DC-West"
        - "What changed in the last hour?"

        Args:
            tenant_id: Organization ID
            message: Natural-language question
            config_id: Supply chain config ID (optional)
            conversation_id: Continue an existing conversation (optional)

        Returns:
            Response text, conversation ID, sources, and suggested follow-ups.
            Output is freeform — DO NOT parse downstream. Use a structured
            tool instead.
        """
        from .db import get_db
        from app.services.decision_stream_service import DecisionStreamService

        async with get_db() as db:
            service = DecisionStreamService(db, tenant_id=tenant_id)
            result = await service.chat(
                message=message,
                conversation_id=conversation_id,
                config_id=config_id,
            )
            # Tag the response so downstream callers that DO see it know
            # it's session-internal and not part of the structured
            # cross-plane API contract.
            if isinstance(result, dict):
                result["_session_internal"] = True
                result["_not_for_cross_plane_parsing"] = True
            return result

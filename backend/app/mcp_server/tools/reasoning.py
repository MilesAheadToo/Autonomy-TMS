"""SCP shim — canonical MCP "ask_why" tool in Core.

``register(mcp)`` now lives in ``azirella_integrations.mcp.tools.reasoning``
(lifted 2026-05-16 per MIGRATION_REGISTER §3.74). Byte-identical SCP↔TMS pre-lift.
"""
from azirella_integrations.mcp.tools.reasoning import register  # noqa: F401


__all__ = ["register"]

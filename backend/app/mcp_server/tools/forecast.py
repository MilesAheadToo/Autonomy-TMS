"""SCP shim — canonical MCP "get_forecast" tool in Core.

``register(mcp)`` now lives in ``azirella_integrations.mcp.tools.forecast``
(lifted 2026-05-16 per MIGRATION_REGISTER §3.74). Byte-identical SCP↔TMS pre-lift.
"""
from azirella_integrations.mcp.tools.forecast import register  # noqa: F401


__all__ = ["register"]

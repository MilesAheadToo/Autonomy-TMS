"""SCP shim — canonical D365 MCP adapter in Core.

``D365MCPAdapter`` + ``CDC_POLL_ENTITIES`` now live in
``azirella_integrations.mcp.adapters.d365`` (lifted 2026-05-15 per
MIGRATION_REGISTER §3.74). Byte-identical between SCP and TMS pre-lift.
"""
from azirella_integrations.mcp.adapters.d365 import (  # noqa: F401
    D365MCPAdapter,
    CDC_POLL_ENTITIES,
)


__all__ = ["D365MCPAdapter", "CDC_POLL_ENTITIES"]

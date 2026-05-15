"""SCP shim — canonical MCP client session manager in Core.

``MCPClientSession`` + ``MCPConnectionPool`` + ``MCPToolInfo`` +
``MCPToolResult`` + the ``mcp_pool`` singleton now live in
``azirella_integrations.mcp.client`` (lifted 2026-05-15 per
MIGRATION_REGISTER §3.74). Byte-identical between SCP and TMS pre-lift.
"""
from azirella_integrations.mcp.client import (  # noqa: F401
    MCPClientSession,
    MCPConnectionPool,
    MCPToolInfo,
    MCPToolResult,
    mcp_pool,
)


__all__ = [
    "MCPClientSession",
    "MCPConnectionPool",
    "MCPToolInfo",
    "MCPToolResult",
    "mcp_pool",
]

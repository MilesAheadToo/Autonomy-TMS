"""SCP shim — canonical MCP server config in Core.

``MCPServerConfig`` ORM + ``MCPServerParams`` + enums + query helpers
now live in ``azirella_integrations.mcp.config`` (lifted 2026-05-15
per MIGRATION_REGISTER §3.74). Byte-identical between SCP and TMS
pre-lift.
"""
from azirella_integrations.mcp.config import (  # noqa: F401
    ERPType,
    MCPServerConfig,
    MCPServerParams,
    MCPTransport,
    get_mcp_config,
    list_mcp_configs,
)


__all__ = [
    "ERPType",
    "MCPServerConfig",
    "MCPServerParams",
    "MCPTransport",
    "get_mcp_config",
    "list_mcp_configs",
]

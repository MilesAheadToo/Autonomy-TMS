"""SCP shim — canonical MCP audit logger in Core.

``MCPAuditLogger`` + ``MCPCallLog`` ORM now live in
``azirella_integrations.mcp.audit`` (lifted 2026-05-15 per
MIGRATION_REGISTER §3.74 / §1.1.2 sibling lift). Byte-identical
between SCP and TMS pre-lift.
"""
from azirella_integrations.mcp.audit import (  # noqa: F401
    MCPAuditLogger,
    MCPCallLog,
)


__all__ = ["MCPAuditLogger", "MCPCallLog"]

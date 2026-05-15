"""SCP shim — canonical MCP write-back service in Core.

``MCPWritebackService`` + ``WritebackResult`` + helpers now live in
``azirella_integrations.mcp.writeback_service`` (lifted 2026-05-15
per MIGRATION_REGISTER §3.74). Byte-identical between SCP and TMS
pre-lift.
"""
from azirella_integrations.mcp.writeback_service import (  # noqa: F401
    MCPWritebackService,
    WritebackResult,
    process_pending_writebacks,
    notify_oncall_if_needed,
    reverse_writeback,
)


__all__ = [
    "MCPWritebackService",
    "WritebackResult",
    "process_pending_writebacks",
    "notify_oncall_if_needed",
    "reverse_writeback",
]

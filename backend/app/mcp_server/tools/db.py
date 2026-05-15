"""SCP shim — canonical MCP tool DB helper in Core.

``get_db`` + ``require_config`` now live in
``azirella_integrations.mcp.tools.db`` (lifted 2026-05-16 per
MIGRATION_REGISTER §3.74). The two functional pieces match SCP and
TMS byte-for-byte; the 17-line diff between planes was docstrings only.
"""
from azirella_integrations.mcp.tools.db import get_db, require_config  # noqa: F401


__all__ = ["get_db", "require_config"]

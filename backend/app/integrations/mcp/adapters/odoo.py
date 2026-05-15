"""SCP shim — canonical Odoo MCP adapter in Core.

``OdooMCPAdapter`` + ``ODOO_TO_AWS_SC_ENTITY`` + ``CDC_POLL_MODELS`` now
live in ``azirella_integrations.mcp.adapters.odoo`` (lifted
2026-05-15 per MIGRATION_REGISTER §3.74). Byte-identical between SCP
and TMS pre-lift.
"""
from azirella_integrations.mcp.adapters.odoo import (  # noqa: F401
    OdooMCPAdapter,
    ODOO_TO_AWS_SC_ENTITY,
    CDC_POLL_MODELS,
)


__all__ = ["OdooMCPAdapter", "ODOO_TO_AWS_SC_ENTITY", "CDC_POLL_MODELS"]

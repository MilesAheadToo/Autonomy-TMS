"""SCP shim — canonical SAP S/4HANA MCP adapter in Core.

``SAPS4MCPAdapter`` + module-level constants now live in
``azirella_integrations.mcp.adapters.sap_s4`` (lifted 2026-05-15 per
MIGRATION_REGISTER §3.74). Byte-identical between SCP and TMS pre-lift.
"""
from azirella_integrations.mcp.adapters.sap_s4 import (  # noqa: F401
    SAPS4MCPAdapter,
    DEFAULT_TOOL_MAPPINGS,
    SAP_ENTITY_SETS,
    SAP_TO_AWS_SC_ENTITY,
)


__all__ = [
    "SAPS4MCPAdapter",
    "DEFAULT_TOOL_MAPPINGS",
    "SAP_ENTITY_SETS",
    "SAP_TO_AWS_SC_ENTITY",
]

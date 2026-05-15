"""SCP shim — canonical D365 connector in Core.

``D365Connector`` + ``D365ConnectionConfig`` + ``D365_SC_ENTITIES`` now
live in ``azirella_integrations.erp.d365.connector`` (lifted 2026-05-15
per MIGRATION_REGISTER §1.1.4). Byte-identical between SCP and TMS pre-lift.
"""
from azirella_integrations.erp.d365.connector import (  # noqa: F401
    D365Connector,
    D365ConnectionConfig,
    D365_SC_ENTITIES,
)


__all__ = ["D365Connector", "D365ConnectionConfig", "D365_SC_ENTITIES"]

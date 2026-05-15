"""SCP shim — canonical SAP APO connector in Core.

``APOConnector`` + ``APOConnectionConfig`` now live in
``azirella_integrations.erp.sap.apo_connector`` (lifted 2026-05-15,
MIGRATION_REGISTER §1.1.2 phase 1a, second tranche). Byte-identical
between SCP and TMS pre-lift.
"""
from azirella_integrations.erp.sap.apo_connector import (  # noqa: F401
    APOConnector,
    APOConnectionConfig,
)


__all__ = ["APOConnector", "APOConnectionConfig"]

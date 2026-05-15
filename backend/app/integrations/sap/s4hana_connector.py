"""SCP shim — canonical SAP S/4HANA connector in Core.

``S4HANAConnector`` + ``S4HANAConnectionConfig`` now live in
``azirella_integrations.erp.sap.s4hana_connector`` (lifted 2026-05-15,
MIGRATION_REGISTER §1.1.2 phase 1a, second tranche). Byte-identical
between SCP and TMS pre-lift.
"""
from azirella_integrations.erp.sap.s4hana_connector import (  # noqa: F401
    S4HANAConnector,
    S4HANAConnectionConfig,
)


__all__ = ["S4HANAConnector", "S4HANAConnectionConfig"]

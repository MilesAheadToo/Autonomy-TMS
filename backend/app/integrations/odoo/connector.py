"""SCP shim — canonical Odoo connector in Core.

``OdooConnector`` + ``OdooConnectionConfig`` now live in
``azirella_integrations.erp.odoo.connector`` (lifted 2026-05-15 per
MIGRATION_REGISTER §1.1.3). Byte-identical between SCP and TMS pre-lift.
"""
from azirella_integrations.erp.odoo.connector import (  # noqa: F401
    OdooConnector,
    OdooConnectionConfig,
)


__all__ = ["OdooConnector", "OdooConnectionConfig"]

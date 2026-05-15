"""SCP shim — canonical Odoo field mapping in Core.

``OdooFieldMappingService`` + ``OdooFieldMatch`` now live in
``azirella_integrations.erp.odoo.field_mapping`` (lifted 2026-05-15
per MIGRATION_REGISTER §1.1.3). Byte-identical between SCP and TMS
pre-lift.
"""
from azirella_integrations.erp.odoo.field_mapping import (  # noqa: F401
    OdooFieldMappingService,
    OdooFieldMatch,
)


__all__ = ["OdooFieldMappingService", "OdooFieldMatch"]

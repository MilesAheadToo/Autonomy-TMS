"""SCP shim — canonical D365 field mapping in Core.

``D365FieldMappingService`` + ``D365FieldMatch`` +
``D365_ENTITY_FIELD_MAPPINGS`` now live in
``azirella_integrations.erp.d365.field_mapping`` (lifted 2026-05-15 per
MIGRATION_REGISTER §1.1.4). Byte-identical between SCP and TMS pre-lift.
"""
from azirella_integrations.erp.d365.field_mapping import (  # noqa: F401
    D365FieldMappingService,
    D365FieldMatch,
    D365_ENTITY_FIELD_MAPPINGS,
)


__all__ = [
    "D365FieldMappingService",
    "D365FieldMatch",
    "D365_ENTITY_FIELD_MAPPINGS",
]

"""TMS shim for the canonical SAP field-mapping service.

The canonical ``SAPFieldMappingService`` + ``SAP_TABLE_FIELD_MAPPINGS``
live in Core's ``azirella_integrations.erp.sap.field_mapping``
(MIGRATION_REGISTER §1.1.2 phase 1a, lifted 2026-05-15).

The TMS copy lagged SCP's by 17 lines — it lacked the PBIM / PBED
Planned Independent Requirements tables that SCP's forecast extraction
populates. The lifted Core version includes them, and the
``LicensedEntityFilter`` automatically skips those for TMS-only
tenants whose plane registrations don't license forecast extraction.
"""
from azirella_integrations.erp.sap.field_mapping import (  # noqa: F401
    MatchConfidence,
    MappingSource,
    FieldMatchResult,
    ZTableAnalysis,
    SAP_TABLE_FIELD_MAPPINGS,
    SAPFieldMappingService,
    create_field_mapping_service,
)


__all__ = [
    "MatchConfidence",
    "MappingSource",
    "FieldMatchResult",
    "ZTableAnalysis",
    "SAP_TABLE_FIELD_MAPPINGS",
    "SAPFieldMappingService",
    "create_field_mapping_service",
]

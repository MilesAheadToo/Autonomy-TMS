"""SCP shim — canonical SAP config builder in Core.

``SAPConfigBuilder`` + ``SchemaFieldResolver`` + ``SitePreview`` +
``LanePreview`` + ``ConfigPreview`` + ``StepResult`` now live in
``azirella_integrations.erp.sap.config_builder`` (lifted 2026-05-15
per MIGRATION_REGISTER §1.1.2 phase 1b). SCP canonical (~7.5% diff vs
TMS pre-lift); all plane-model imports rewritten to canonical
`azirella_data_model.*` paths. Two plane-specific dependencies
(ExtractionAuditReport, SAPUserProvisioningService) stay as lazy
in-function imports — they resolve to the calling plane's `app.services`.
"""
from azirella_integrations.erp.sap.config_builder import (  # noqa: F401
    SchemaFieldResolver,
    SitePreview,
    LanePreview,
    ConfigPreview,
    StepResult,
    SAPConfigBuilder,
)


__all__ = [
    "SchemaFieldResolver",
    "SitePreview",
    "LanePreview",
    "ConfigPreview",
    "StepResult",
    "SAPConfigBuilder",
]

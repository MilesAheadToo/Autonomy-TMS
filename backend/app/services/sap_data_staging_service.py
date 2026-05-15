"""SCP shim — canonical SAP data staging service in Core.

``SAPDataStagingService`` + ``StagingEntityType`` + ``StagingResult`` +
``StagingPipelineResult`` now live in
``azirella_integrations.erp.sap.data_staging_service`` (lifted
2026-05-15 per MIGRATION_REGISTER §1.1.2 phase 1b). All plane-model
imports rewritten to canonical `azirella_data_model.*` paths.
``SAPUserProvisioningService`` stays as a lazy in-function import (the
one method that uses it resolves to the calling plane's
`app.services`).
"""
from azirella_integrations.erp.sap.data_staging_service import (  # noqa: F401
    StagingEntityType,
    StagingResult,
    StagingPipelineResult,
    SAPDataStagingService,
)


__all__ = [
    "StagingEntityType",
    "StagingResult",
    "StagingPipelineResult",
    "SAPDataStagingService",
]

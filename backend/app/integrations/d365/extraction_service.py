"""SCP shim — canonical D365 extraction service in Core.

``D365ExtractionService`` + ``ExtractionPhase`` + ``ExtractionResult`` +
``ExtractionJobResult`` + ``D365ConfigBuilder`` + ``D365IngestionMonitor`` +
module-level ``MASTER_DATA_ENTITIES`` / ``TRANSACTION_ENTITIES`` lists
all now live in ``azirella_integrations.erp.d365.extraction_service``
(lifted 2026-05-15 per MIGRATION_REGISTER §1.1.4). Byte-identical
between SCP and TMS pre-lift.
"""
from azirella_integrations.erp.d365.extraction_service import (  # noqa: F401
    ExtractionPhase,
    ExtractionResult,
    ExtractionJobResult,
    D365ExtractionService,
    D365ConfigBuilder,
    D365IngestionMonitor,
    MASTER_DATA_ENTITIES,
    TRANSACTION_ENTITIES,
)


__all__ = [
    "ExtractionPhase",
    "ExtractionResult",
    "ExtractionJobResult",
    "D365ExtractionService",
    "D365ConfigBuilder",
    "D365IngestionMonitor",
    "MASTER_DATA_ENTITIES",
    "TRANSACTION_ENTITIES",
]

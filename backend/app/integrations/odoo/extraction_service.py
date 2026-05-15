"""SCP shim — canonical Odoo extraction service in Core.

``OdooExtractionService`` + ``ExtractionPhase`` + ``ExtractionResult`` +
``ExtractionJobResult`` now live in
``azirella_integrations.erp.odoo.extraction_service`` (lifted 2026-05-15
per MIGRATION_REGISTER §1.1.3). Byte-identical between SCP and TMS
pre-lift.
"""
from azirella_integrations.erp.odoo.extraction_service import (  # noqa: F401
    ExtractionPhase,
    ExtractionResult,
    ExtractionJobResult,
    OdooExtractionService,
)


__all__ = [
    "ExtractionPhase",
    "ExtractionResult",
    "ExtractionJobResult",
    "OdooExtractionService",
]

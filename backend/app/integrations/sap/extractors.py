"""SCP shim — canonical SAP table extractors in Core.

``SAPTableExtractor`` ABC + concrete ``ODataExtractor``,
``HANADBExtractor``, ``RFCExtractor`` + ``create_extractor`` factory
now live in ``azirella_integrations.erp.sap.extractors`` (lifted
2026-05-15, MIGRATION_REGISTER §1.1.2 phase 1a, second tranche).
Byte-identical between SCP and TMS pre-lift.
"""
from azirella_integrations.erp.sap.extractors import (  # noqa: F401
    SAPTableExtractor,
    ODataExtractor,
    HANADBExtractor,
    RFCExtractor,
    create_extractor,
)


__all__ = [
    "SAPTableExtractor",
    "ODataExtractor",
    "HANADBExtractor",
    "RFCExtractor",
    "create_extractor",
]

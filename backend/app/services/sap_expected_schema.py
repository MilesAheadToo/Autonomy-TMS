"""TMS shim for the canonical SAP expected-schema definitions.

The canonical ``EXPECTED_SCHEMA`` + helper functions live in Core's
``azirella_integrations.erp.sap.expected_schema`` (MIGRATION_REGISTER
§1.1.2 phase 1a, lifted 2026-05-15).

Mirrors the SCP shim. The TMS copy was byte-identical to SCP's
before the lift, so this is purely a path redirect — every call site
keeps working unchanged. Tenants who don't license SCP will have
manufacturing entities (``production_order``, ``purchase_order``,
``manufacturing_order``, etc.) filtered out by ``LicensedEntityFilter``
at extraction time.
"""
from azirella_integrations.erp.sap.expected_schema import (  # noqa: F401
    EXPECTED_SCHEMA,
    get_all_required_tables,
    get_required_fields_for_entity,
)


__all__ = [
    "EXPECTED_SCHEMA",
    "get_all_required_tables",
    "get_required_fields_for_entity",
]

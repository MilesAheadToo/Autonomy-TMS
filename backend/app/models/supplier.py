"""SCP shim — canonical Supplier models in Core.

``VendorProduct`` + ``VendorLeadTime`` + ``SupplierPerformance`` now
live in ``azirella_data_model.master.supplier`` (lifted 2026-05-15 per
MIGRATION_REGISTER §1.1.2 phase 1b sub-lift). SCP canonical (~11% diff
vs TMS); the TMS variant carried the WRONG table names
(``vendor_products`` / ``vendor_lead_times`` plural; the live DB has
the singular forms ``vendor_product`` / ``vendor_lead_time`` matching
SCP). This shim transparently fixes TMS by routing through the
SCP-canonical Core class.
"""
from azirella_data_model.master.supplier import (  # noqa: F401
    VendorProduct,
    VendorLeadTime,
    SupplierPerformance,
)


__all__ = [
    "VendorProduct",
    "VendorLeadTime",
    "SupplierPerformance",
]

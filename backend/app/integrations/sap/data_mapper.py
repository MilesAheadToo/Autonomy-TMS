"""SCP shim — canonical SAP DataFrame → AWS-SC-DM mapper in Core.

``SupplyChainMapper`` now lives in
``azirella_integrations.erp.sap.data_mapper`` (lifted 2026-05-15 per
MIGRATION_REGISTER §1.1.2 phase 1a, second tranche). SCP and TMS
copies were byte-identical pre-lift; every call site keeps working
through this shim.
"""
from azirella_integrations.erp.sap.data_mapper import SupplyChainMapper  # noqa: F401


__all__ = ["SupplyChainMapper"]

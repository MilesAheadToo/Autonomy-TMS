"""SCP shim — canonical Odoo config builder in Core.

``OdooConfigBuilder`` + ``OdooConfigBuildResult`` + ``OdooIngestionMonitor``
now live in ``azirella_integrations.erp.odoo.config_builder`` (lifted
2026-05-15 per MIGRATION_REGISTER §1.1.3). SCP-superset canonical
(95% identical to TMS; SCP carried 3 extra inventory field handlers
TMS didn't have — those land in Core unchanged and will be filtered
per tenant by ``LicensedEntityFilter`` once ``OdooConnector`` adopts
``BaseErpConnector``).
"""
from azirella_integrations.erp.odoo.config_builder import (  # noqa: F401
    OdooConfigBuilder,
    OdooConfigBuildResult,
    OdooIngestionMonitor,
)


__all__ = [
    "OdooConfigBuilder",
    "OdooConfigBuildResult",
    "OdooIngestionMonitor",
]

"""SCP shim — canonical D365 config builder in Core.

``D365ConfigBuilder`` + ``D365ConfigBuildResult`` now live in
``azirella_integrations.erp.d365`` (lifted 2026-05-15 per
MIGRATION_REGISTER §1.1.4). SCP canonical (~2% diff vs TMS).
"""
from azirella_integrations.erp.d365 import (  # noqa: F401
    D365ConfigBuilder,
    D365ConfigBuildResult,
    D365IngestionMonitor,
)


__all__ = ["D365ConfigBuilder", "D365ConfigBuildResult", "D365IngestionMonitor"]

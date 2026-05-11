"""TMS shim for the canonical ExternalSignalService.

The orchestrator lives in Core's ``azirella_data_model.context_engine.service``
— this module exists only to inject TMS's plane-extended
``SOURCE_REGISTRY`` (base free feeds + FMCSA, DOE diesel, CBP, EPA
SmartWay, TSA, DAT, SONAR, Greenscreens, MarineTraffic, Drewry, Xeneta,
Inrix, HERE, OAG, CargoMetrics) so existing call sites that do
``from app.services.external_signal_service import ExternalSignalService``
keep working unchanged.

See Core ``packages/data-model/src/azirella_data_model/context_engine/service.py``
for the implementation, and MIGRATION_REGISTER §3.60 for the promotion
rationale.
"""

from azirella_data_model.context_engine import (
    ExternalSignalService as _CoreExternalSignalService,
    refresh_all_tenants as _core_refresh_all_tenants,
)

from app.models.external_signal import SOURCE_REGISTRY


class ExternalSignalService(_CoreExternalSignalService):
    """TMS wrapper — injects TMS's SOURCE_REGISTRY into the Core orchestrator."""

    def __init__(self, db, tenant_id: int):
        super().__init__(db, tenant_id, source_registry=SOURCE_REGISTRY)


async def refresh_all_tenants(db) -> dict:
    """TMS scheduler entry point — forwards to Core with TMS's SOURCE_REGISTRY."""
    return await _core_refresh_all_tenants(db, source_registry=SOURCE_REGISTRY)


__all__ = ["ExternalSignalService", "refresh_all_tenants"]

"""TMS shim for the canonical directive_broadcast service.

``DirectiveBroadcastService`` lives in Core's
``azirella_data_model.powell.directive_broadcast`` (MIGRATION_REGISTER
§3.49 tranche 3, lifted 2026-05-18). The Core version takes a
``SiteAgentProtocol`` instead of the concrete plane-side ``SiteAgent``
class — every existing TMS ``SiteAgent`` instance already satisfies
the protocol structurally (``signal_bus``, ``_registered_trms``,
``apply_directive``), so no consumer code changes.

SCP and TMS pre-lift copies were byte-identical; the canonical
version preserves their behaviour exactly, including the module-level
``_active_broadcast_service`` singleton DirectiveService discovers.
"""
from azirella_data_model.powell.directive_broadcast import (  # noqa: F401
    DirectiveBroadcastService,
    SiteAgentProtocol,
    _active_broadcast_service,
)


__all__ = [
    "DirectiveBroadcastService",
    "SiteAgentProtocol",
    "_active_broadcast_service",
]

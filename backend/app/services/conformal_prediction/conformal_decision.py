"""SCP shim — canonical Conformal Decision Theory wrapper in Core.

``ConformalDecisionWrapper`` + ``ConformalDecisionRegistry`` +
``DecisionOutcomePair`` + ``RiskAssessment`` + ``get_cdt_registry`` /
``reset_cdt_registry`` / ``get_all_tenant_registries`` now live in
``azirella_data_model.conformal.conformal_decision`` (lifted
2026-05-15 per MIGRATION_REGISTER §1.4). SCP canonical (~1% diff vs TMS).
"""
from azirella_data_model.conformal.conformal_decision import (  # noqa: F401
    DecisionOutcomePair,
    RiskAssessment,
    ConformalDecisionWrapper,
    ConformalDecisionRegistry,
    get_cdt_registry,
    reset_cdt_registry,
    get_all_tenant_registries,
)


__all__ = [
    "DecisionOutcomePair",
    "RiskAssessment",
    "ConformalDecisionWrapper",
    "ConformalDecisionRegistry",
    "get_cdt_registry",
    "reset_cdt_registry",
    "get_all_tenant_registries",
]

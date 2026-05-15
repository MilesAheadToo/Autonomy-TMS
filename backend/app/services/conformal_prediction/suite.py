"""SCP shim — canonical conformal suite in Core.

``SupplyChainConformalSuite`` + 7 per-domain conformal predictors +
``JointUncertaintyScenario`` + ``RelationalConformalPredictor`` +
``get_conformal_suite`` factory now live in
``azirella_data_model.conformal.suite`` (lifted 2026-05-15 per
MIGRATION_REGISTER §1.4 first tranche). Byte-identical between SCP
and TMS pre-lift (1460 LOC).
"""
from azirella_data_model.conformal.suite import (  # noqa: F401
    JointUncertaintyScenario,
    YieldConformalPredictor,
    PriceConformalPredictor,
    TransitTimeConformalPredictor,
    ReceiptVarianceConformalPredictor,
    QualityRejectionConformalPredictor,
    MaintenanceDowntimeConformalPredictor,
    ForecastBiasConformalPredictor,
    SupplyChainConformalSuite,
    RelationalConformalPredictor,
    get_conformal_suite,
    reset_conformal_suite,
)


__all__ = [
    "JointUncertaintyScenario",
    "YieldConformalPredictor",
    "PriceConformalPredictor",
    "TransitTimeConformalPredictor",
    "ReceiptVarianceConformalPredictor",
    "QualityRejectionConformalPredictor",
    "MaintenanceDowntimeConformalPredictor",
    "ForecastBiasConformalPredictor",
    "SupplyChainConformalSuite",
    "RelationalConformalPredictor",
    "get_conformal_suite",
    "reset_conformal_suite",
]

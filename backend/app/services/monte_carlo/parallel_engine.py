"""SCP shim — canonical parallel Monte Carlo engine in Core.

``ParallelMonteCarloEngine`` + ``ScenarioConfig`` + ``ScenarioResult`` +
``compare_sequential_vs_parallel`` now live in
``azirella_data_model.monte_carlo.parallel_engine`` (lifted 2026-05-15
per MIGRATION_REGISTER §1.4 first tranche). Byte-identical between
SCP and TMS pre-lift (606 LOC). No plane-specific imports — pure
multiprocessing driver over scenario configs.
"""
from azirella_data_model.monte_carlo.parallel_engine import (  # noqa: F401
    ScenarioConfig,
    ScenarioResult,
    ParallelMonteCarloEngine,
    compare_sequential_vs_parallel,
)


__all__ = [
    "ScenarioConfig",
    "ScenarioResult",
    "ParallelMonteCarloEngine",
    "compare_sequential_vs_parallel",
]

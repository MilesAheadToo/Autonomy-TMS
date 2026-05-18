"""TMS shim — re-exports from ``azirella_data_model.powell.hive_signal``.

The hive-signal substrate moved to Core on 2026-05-01 as part of
MIGRATION_REGISTER §1.13 / §3.49 (data-model 0.12.0). SCP already
collapsed to a shim on the same date; TMS retained a local copy
because three visibility signals (`LATE_ARRIVAL_DETECTED`,
`DWELL_BREACH_ALERT`, `GEOFENCE_DEPARTED_EARLY`) lived only here.

2026-05-18: those three signals lifted to Core (rationale per the
Core HiveSignalType docstring + §3.49 register entry — shipment-
visibility coordination is substrate, plausibly consumed by SCP and
DP as well). TMS now collapses to the same re-export shape SCP
uses. Imports against the old module path stay valid; every name
TMS code previously exposed here is reachable via Core.

If a future need surfaces a TMS-specific helper, prefer to first
ask whether it should be in Core; only fall back to adding it here
if it has no platform reuse value.
"""
from azirella_data_model.powell.hive_signal import (  # noqa: F401
    # Public dataclasses + enums
    HiveSignal,
    HiveSignalBus,
    HiveSignalType,
    UrgencyVector,
    # Caste signal sets (consumed by hive_feedback, site_tgnn, ...)
    SCOUT_SIGNALS,
    FORAGER_SIGNALS,
    NURSE_SIGNALS,
    GUARD_SIGNALS,
    BUILDER_SIGNALS,
    TGNN_SIGNALS,
    VISIBILITY_SIGNALS,
    # Decay constants
    DECAY_THRESHOLD,
    _LN2,
)


__all__ = [
    "HiveSignal",
    "HiveSignalBus",
    "HiveSignalType",
    "UrgencyVector",
    "SCOUT_SIGNALS",
    "FORAGER_SIGNALS",
    "NURSE_SIGNALS",
    "GUARD_SIGNALS",
    "BUILDER_SIGNALS",
    "TGNN_SIGNALS",
    "VISIBILITY_SIGNALS",
    "DECAY_THRESHOLD",
]

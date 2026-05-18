"""TMS shim — re-exports from ``azirella_data_model.powell.inter_hive_signal``.

The inter-hive signal substrate moved to Core on 2026-05-01 as part
of MIGRATION_REGISTER §1.13 / §3.49 (data-model 0.12.0). The Core
version was hoisted from SCP's superset (the TMS-side copy diverged
on a few specifics — layer-numbering, missing DP overlay fields);
adopting the canonical version adds those fields' default values
without removing anything TMS reads.

This file is now a thin re-export so existing imports in
``site_agent.py`` and ``directive_broadcast_service.py`` keep
working without a per-file rewrite. Mirrors the shim pattern already
used for ``hive_signal.py``, ``hive_feedback.py``, ``hive_health.py``.
"""
from azirella_data_model.powell.inter_hive_signal import (  # noqa: F401
    InterHiveSignal,
    InterHiveSignalType,
    tGNNSiteDirective,
)


__all__ = [
    "InterHiveSignal",
    "InterHiveSignalType",
    "tGNNSiteDirective",
]

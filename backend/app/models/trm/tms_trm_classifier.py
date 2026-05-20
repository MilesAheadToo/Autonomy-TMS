"""TMS TRM classifier — re-export shim over Core's canonical class.

Lifted to ``azirella_data_model.ml.trm_classifier`` on 2026-05-20 (TMS
Phase 3 of the TRM rework). DP's ``load_volume_sensing`` TRM uses the
same classifier; keeping two copies would re-create the drift the lift
just closed.

TMS-internal callers may continue to import from this path
(``app.models.trm.tms_trm_classifier``) or from
``app.models.trm`` directly — both resolve to the same Core class.
New code should prefer ``from azirella_data_model.ml import TRMClassifier``.
"""
from __future__ import annotations

from azirella_data_model.ml.trm_classifier import (
    ACTION_INDEX_TO_NAME,
    ACTION_NAMES,
    NUM_ACTIONS,
    TRMClassifier,
)


__all__ = [
    "TRMClassifier",
    "ACTION_NAMES",
    "ACTION_INDEX_TO_NAME",
    "NUM_ACTIONS",
]

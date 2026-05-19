"""TMS shim for the canonical Consensus Planning ORMs.

All four ORMs + the status enum live in Core's
``azirella_data_model.master.consensus_plan`` (MIGRATION_REGISTER
§3.78 Step F, lifted 2026-05-19). Re-exported here so existing
import paths (``from app.models.consensus_plan import ConsensusPlan,
...``) keep working unchanged.

TMS pre-lift was a fork-leftover near-duplicate of SCP's same file;
post-lift both planes re-export from Core.
"""
from azirella_data_model.master.consensus_plan import (  # noqa: F401
    ConsensusPlan,
    ConsensusPlanComment,
    ConsensusPlanStatus,
    ConsensusPlanVersion,
    ConsensusPlanVote,
)

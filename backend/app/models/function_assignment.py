"""TMS function_assignment shim — re-exports canonical from Core.

The canonical `FunctionAssignment` lives in
`azirella_data_model.simulation.function_assignment` alongside
`ScenarioUser` (promoted 2026-05-13). Both SCP and TMS carried
byte-identical copies before promotion. Promoted 2026-05-14 per
CLAUDE.md Rule 1+2.

Why simulation/ rather than work_order/: FunctionAssignment is the
(scenario_user × site × function × agent_mode) mapping that the
scenario engine uses to decide which TRM responds to which decision
at which site. It's tied to the scenario-user model, not the
work-order family.
"""
from azirella_data_model.simulation.function_assignment import (  # noqa: F401
    FunctionAssignment,
)


__all__ = [
    "FunctionAssignment",
]

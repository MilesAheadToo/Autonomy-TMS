"""Scenario — re-exported from azirella_data_model.

Core defines the canonical Scenario, Period, ScenarioUserAction classes
(Core v0.5.1, MIGRATION_REGISTER item 1.12: rename cascade Round → Period).
TMS re-exports them and attaches TMS-specific relationships + columns
post-definition (same pattern as SCP).

The TMS Scenario table has extra columns beyond Core's definition
(use_sc_planning, use_dag_sequential, time_bucket, start_date, etc.)
which are added via extend_existing=True on Core's class.
"""
from datetime import datetime, date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from sqlalchemy import Integer, String, DateTime, JSON, ForeignKey, Boolean, Date
from sqlalchemy.orm import relationship, Mapped, mapped_column, Session

from azirella_data_model.simulation import (
    Scenario,
    Period,
    ScenarioUserAction,
    ScenarioStatus,
)

from app.core.time_buckets import TimeBucket, DEFAULT_START_DATE

if TYPE_CHECKING:
    from .participant import ScenarioUser
    from .agent_config import AgentConfig


# ── TMS-specific columns on Scenario ────────────────────────────────────
# These columns exist in TMS's scenarios table but not in Core's definition.
# extend_existing=True on Core's class allows them to coexist.

if not hasattr(Scenario, 'use_sc_planning'):
    Scenario.use_sc_planning = mapped_column(Boolean, default=False, index=True)
if not hasattr(Scenario, 'use_dag_sequential'):
    Scenario.use_dag_sequential = mapped_column(Boolean, default=True, index=True)
if not hasattr(Scenario, 'role_assignments'):
    Scenario.role_assignments = mapped_column(JSON, default=dict)
if not hasattr(Scenario, 'demand_pattern'):
    Scenario.demand_pattern = mapped_column(JSON, default=dict)
if not hasattr(Scenario, 'time_bucket'):
    Scenario.time_bucket = mapped_column(String(16), default=TimeBucket.WEEK.value)
if not hasattr(Scenario, 'start_date'):
    Scenario.start_date = mapped_column(Date, default=DEFAULT_START_DATE)
if not hasattr(Scenario, 'current_period_start'):
    Scenario.current_period_start = mapped_column(Date, nullable=True)
if not hasattr(Scenario, 'max_periods'):
    Scenario.max_periods = mapped_column(Integer, default=52)
if not hasattr(Scenario, 'current_period'):
    Scenario.current_period = mapped_column(Integer, default=0)


# ── TMS-specific relationships on Scenario ──────────────────────────────
#
# Only relationships whose target lives TMS-side stay here. Core↔Core
# back-relations now live in Core itself (§3.73, §3.82).
#
# Moved to Core:
#   §3.73 Step 2 (2026-05-14): `Scenario.supply_chain_periods`
#       (target `ScenarioPeriod` in `simulation.supply_chain`).
#   §3.73 Step 3 (2026-05-14): `Scenario.chat_messages`,
#       `.agent_suggestions`, `.what_if_analyses`
#       (targets in `simulation.chat`).
#   §3.73 Step 2b (2026-05-14): `Scenario.function_assignments`.
#   §3.82 (2026-05-19): `Scenario.scenario_users`,
#       `ScenarioUserAction.scenario_user` — symmetric Core↔Core pairs
#       whose plane-side monkey-patch broke Core-only
#       `configure_mappers()`.
#
# Still TMS-local below: `Scenario.users` (depends on TMS-side
# `user_scenarios` association table; tracked as follow-up to §3.82),
# `Scenario.supervisor_actions` (TMS-side `SupervisorAction`),
# `Scenario.agent_configs` (TMS-side `AgentConfig`).
Scenario.users = relationship(
    "User", secondary="user_scenarios", lazy="selectin"
)
Scenario.supervisor_actions = relationship(
    "SupervisorAction", back_populates="scenario", lazy="selectin"
)
Scenario.agent_configs = relationship(
    "AgentConfig", back_populates="scenario", lazy="selectin"
)


# ── TMS-specific methods on Scenario ────────────────────────────────────

def _get_role_assignment(self, role: str) -> Dict[str, Any]:
    return (self.role_assignments or {}).get(
        role, {'is_ai': False, 'agent_config_id': None, 'user_id': None}
    )

def _set_role_assignment(self, role: str, is_ai: bool,
                         agent_config_id: Optional[int] = None,
                         user_id: Optional[int] = None):
    if not hasattr(self, 'role_assignments') or not self.role_assignments:
        self.role_assignments = {}
    self.role_assignments[role] = {
        'is_ai': is_ai,
        'agent_config_id': agent_config_id,
        'user_id': user_id if not is_ai else None
    }

def _get_agent_config(self, role: str, db: Session) -> Optional['AgentConfig']:
    from .agent_config import AgentConfig
    assignment = self.get_role_assignment(role)
    if not assignment or not assignment['is_ai'] or not assignment['agent_config_id']:
        return None
    return db.query(AgentConfig).filter(
        AgentConfig.id == assignment['agent_config_id'],
        AgentConfig.scenario_id == self.id
    ).first()

Scenario.get_role_assignment = _get_role_assignment
Scenario.set_role_assignment = _set_role_assignment
Scenario.get_agent_config = _get_agent_config


# Backward-compat
ParticipantAction = ScenarioUserAction


__all__ = [
    "Scenario",
    "Period",
    "ScenarioUserAction",
    "ScenarioStatus",
    "ParticipantAction",
]

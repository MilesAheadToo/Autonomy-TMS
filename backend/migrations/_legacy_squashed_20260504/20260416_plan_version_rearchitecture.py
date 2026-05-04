"""Widen transportation_plan.plan_version + honest labelling backfill

Phase 0 of the Tactical Planning Rearchitecture
(see docs/TACTICAL_PLANNING_REARCHITECTURE.md).

Revision ID: 20260416_plan_version
Revises: 463a7f80070f
Create Date: 2026-04-16 09:00:00.000000

Changes:
- `transportation_plan.plan_version` widens from VARCHAR(20) → VARCHAR(30)
  so it can hold the new canonical values (`unconstrained_reference` is
  23 chars).
- Default value flips from `'live'` → `'constrained_live'` so newly
  generated agent-written plans get the honest label.
- Existing rows with `plan_version='live'` get back-filled to
  `'constrained_live'` — today's agent-written plans are really decision
  records, but labelling them constrained_live keeps the Execution TRMs
  reading from the same slot until the Integrated Balancer ships
  (Phase 3).

The four canonical values documented in `app.models.tms_planning.PlanVersion`:
  unconstrained_reference  — Demand Potential / Unconstrained Movement Plan
  constrained_live         — Constrained Committed Plan (current decision record)
  erp_baseline             — External TMS / ERP comparison plan
  decision_action          — User-authored override plan

Note: column stays a plain VARCHAR (not a PG ENUM) so future values can
be added without Alembic churn.
"""
from alembic import op
import sqlalchemy as sa


revision = '20260416_plan_version'
down_revision = '463a7f80070f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Widen the column
    op.alter_column(
        'transportation_plan', 'plan_version',
        existing_type=sa.String(length=20),
        type_=sa.String(length=30),
        existing_nullable=False,
        server_default=sa.text("'constrained_live'"),
    )
    # Back-fill historical 'live' rows
    op.execute(
        "UPDATE transportation_plan "
        "SET plan_version = 'constrained_live' "
        "WHERE plan_version = 'live'"
    )


def downgrade() -> None:
    # Reverse the back-fill (best effort; loses distinction between
    # historical 'live' and any rows that were genuinely new
    # constrained_live plans, but acceptable for downgrade)
    op.execute(
        "UPDATE transportation_plan "
        "SET plan_version = 'live' "
        "WHERE plan_version = 'constrained_live'"
    )
    op.alter_column(
        'transportation_plan', 'plan_version',
        existing_type=sa.String(length=30),
        type_=sa.String(length=20),
        existing_nullable=False,
        server_default=sa.text("'live'"),
    )

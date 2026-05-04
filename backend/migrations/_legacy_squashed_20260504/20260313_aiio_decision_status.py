"""Rename decision_status_enum to AIIO model (Actioned, Informed, Inspected, Overridden)

Revision ID: 20260313_aiio
Revises: None (standalone)
"""
from alembic import op
import sqlalchemy as sa

revision = '20260313_aiio'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # Add new AIIO values (uppercase to match existing enum convention)
    conn.execute(sa.text("ALTER TYPE decision_status_enum ADD VALUE IF NOT EXISTS 'INFORMED'"))
    conn.execute(sa.text("ALTER TYPE decision_status_enum ADD VALUE IF NOT EXISTS 'ACTIONED'"))
    conn.execute(sa.text("ALTER TYPE decision_status_enum ADD VALUE IF NOT EXISTS 'INSPECTED'"))
    conn.execute(sa.text("ALTER TYPE decision_status_enum ADD VALUE IF NOT EXISTS 'OVERRIDDEN'"))

    # Commit so new enum values are visible (required by PostgreSQL)
    op.execute("COMMIT")

    # Update existing rows: map old values to new AIIO values
    for tbl in ['agent_decisions', 'sop_worklist_items']:
        conn.execute(sa.text(f"UPDATE {tbl} SET status = 'INFORMED' WHERE status = 'PENDING'"))
        conn.execute(sa.text(f"UPDATE {tbl} SET status = 'ACTIONED' WHERE status IN ('ACCEPTED', 'AUTO_EXECUTED', 'EXPIRED')"))
        conn.execute(sa.text(f"UPDATE {tbl} SET status = 'OVERRIDDEN' WHERE status = 'REJECTED'"))

    # Note: Old enum values (PENDING, ACCEPTED, REJECTED, AUTO_EXECUTED, EXPIRED) remain
    # in the type but are no longer used. PostgreSQL cannot drop individual enum values
    # without recreating the type.


def downgrade():
    conn = op.get_bind()

    for tbl in ['agent_decisions', 'sop_worklist_items']:
        conn.execute(sa.text(f"UPDATE {tbl} SET status = 'PENDING' WHERE status IN ('INFORMED', 'INSPECTED')"))
        conn.execute(sa.text(f"UPDATE {tbl} SET status = 'ACCEPTED' WHERE status = 'ACTIONED'"))
        conn.execute(sa.text(f"UPDATE {tbl} SET status = 'OVERRIDDEN' WHERE status = 'REJECTED'"))

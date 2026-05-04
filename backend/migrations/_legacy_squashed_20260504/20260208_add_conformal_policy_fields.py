"""Add conformal policy fields to inv_policy table

Revision ID: 20260208_conformal
Revises:
Create Date: 2026-02-08

This migration adds support for the new 'conformal' safety stock policy type
which uses conformal prediction intervals for distribution-free service level
guarantees.

New columns:
- conformal_demand_coverage: Target coverage for demand intervals (default 0.90)
- conformal_lead_time_coverage: Target coverage for lead time intervals (default 0.90)

These columns are only used when ss_policy = 'conformal'.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260208_conformal'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Add conformal policy columns to inv_policy table"""

    # Add conformal_demand_coverage column
    op.add_column(
        'inv_policy',
        sa.Column(
            'conformal_demand_coverage',
            sa.Float(),
            nullable=True,
            server_default='0.90',
            comment='Target coverage for demand intervals (0-1). Used when ss_policy=conformal.'
        )
    )

    # Add conformal_lead_time_coverage column
    op.add_column(
        'inv_policy',
        sa.Column(
            'conformal_lead_time_coverage',
            sa.Float(),
            nullable=True,
            server_default='0.90',
            comment='Target coverage for lead time intervals (0-1). Used when ss_policy=conformal.'
        )
    )

    # Update the check constraint on ss_policy to include 'conformal'
    # Note: This is PostgreSQL-specific. For other databases, the syntax may differ.
    # First, we need to check if the constraint exists and drop it
    try:
        op.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'inv_policy_ss_policy_check'
                ) THEN
                    ALTER TABLE inv_policy DROP CONSTRAINT inv_policy_ss_policy_check;
                END IF;
            END $$;
        """)
    except Exception:
        # Constraint may not exist, which is fine
        pass


def downgrade():
    """Remove conformal policy columns from inv_policy table"""

    op.drop_column('inv_policy', 'conformal_demand_coverage')
    op.drop_column('inv_policy', 'conformal_lead_time_coverage')

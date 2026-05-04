"""Add use_aws_sc_planning flag to Game model

Revision ID: 20260112_aws_sc_flag
Revises: 20260111_aws_sc_multi_tenancy
Create Date: 2026-01-12

This migration adds a feature flag to enable AWS SC planning mode for games.
When enabled, games will use AWSSupplyChainPlanner instead of the legacy engine.py logic.

This supports Phase 2 of the AWS SC integration:
- Dual-mode operation (legacy vs AWS SC)
- Gradual migration path
- A/B testing of planning algorithms
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '20260112_aws_sc_flag'
down_revision = '20260111_aws_sc_multi_tenancy'
branch_labels = None
depends_on = None


def upgrade():
    """Add use_aws_sc_planning flag to games table"""

    # Add the flag column (default False for backwards compatibility)
    op.add_column(
        'games',
        sa.Column('use_aws_sc_planning', sa.Boolean(), nullable=False, server_default=sa.text("FALSE"))
    )

    # Add index for filtering games by planning mode
    op.create_index(
        'idx_games_aws_sc_planning',
        'games',
        ['use_aws_sc_planning']
    )

    print("✓ Added use_aws_sc_planning flag to games table")
    print("  Default: False (uses legacy engine.py)")
    print("  Set to True to enable AWS SC planning mode")


def downgrade():
    """Remove use_aws_sc_planning flag"""

    op.drop_index('idx_games_aws_sc_planning', 'games')
    op.drop_column('games', 'use_aws_sc_planning')

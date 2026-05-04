"""Rename Training group mode to Learning

Revision ID: 20260204_training_to_learning
Revises: 20260203_powell_cdc_tables
Create Date: 2026-02-04

This migration renames the 'training' group mode to 'learning' to avoid
confusion with AI model training (TRM/GNN/RL training).

- Training Group -> Learning Group (user education mode)
- AI Model Training remains unchanged (training TRM/GNN/RL models)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers
revision = '20260204_training_to_learning'
down_revision = '20260203_powell_cdc_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rename 'training' to 'learning' in group mode enum."""
    conn = op.get_bind()

    # Check if 'training' value exists in the enum
    result = conn.execute(text("""
        SELECT 1 FROM pg_enum
        WHERE enumlabel = 'training'
        AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'group_mode_enum')
    """))
    training_exists = result.fetchone() is not None

    # Check if 'learning' value already exists
    result = conn.execute(text("""
        SELECT 1 FROM pg_enum
        WHERE enumlabel = 'learning'
        AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'group_mode_enum')
    """))
    learning_exists = result.fetchone() is not None

    if training_exists and not learning_exists:
        # PostgreSQL 10+ supports RENAME VALUE which works in same transaction
        conn.execute(text(
            "ALTER TYPE group_mode_enum RENAME VALUE 'training' TO 'learning'"
        ))
    elif not training_exists and not learning_exists:
        # Neither exists - add 'learning' (this would be unusual)
        conn.execute(text(
            "ALTER TYPE group_mode_enum ADD VALUE 'learning'"
        ))
    # If learning_exists is True, nothing to do - already migrated


def downgrade() -> None:
    """Revert 'learning' back to 'training' in group mode enum."""
    conn = op.get_bind()

    # Check if 'learning' value exists
    result = conn.execute(text("""
        SELECT 1 FROM pg_enum
        WHERE enumlabel = 'learning'
        AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'group_mode_enum')
    """))
    learning_exists = result.fetchone() is not None

    # Check if 'training' value exists
    result = conn.execute(text("""
        SELECT 1 FROM pg_enum
        WHERE enumlabel = 'training'
        AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'group_mode_enum')
    """))
    training_exists = result.fetchone() is not None

    if learning_exists and not training_exists:
        # Rename back to training
        conn.execute(text(
            "ALTER TYPE group_mode_enum RENAME VALUE 'learning' TO 'training'"
        ))

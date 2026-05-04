"""add_explainability_level_override_to_users

Revision ID: 8145adf51ea2
Revises: 20260128_weight_learning_tables
Create Date: 2026-01-28 11:41:27.240751

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8145adf51ea2'
down_revision: Union[str, None] = '20260128_weight_learning_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum type if it doesn't exist
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'explainability_level_enum') THEN
                CREATE TYPE explainability_level_enum AS ENUM ('VERBOSE', 'NORMAL', 'SUCCINCT');
            END IF;
        END $$;
    """)
    
    # Add column to users table
    op.add_column('users', 
        sa.Column('explainability_level_override', 
                  sa.Enum('VERBOSE', 'NORMAL', 'SUCCINCT', name='explainability_level_enum'),
                  nullable=True)
    )


def downgrade() -> None:
    # Remove column
    op.drop_column('users', 'explainability_level_override')
    
    # Drop enum type (optional - may be used by other tables)
    # op.execute("DROP TYPE IF EXISTS explainability_level_enum")

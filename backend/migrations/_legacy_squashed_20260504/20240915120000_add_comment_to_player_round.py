from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20240915120000'
down_revision = '20240912120000'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('player_rounds', sa.Column('comment', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('player_rounds', 'comment')


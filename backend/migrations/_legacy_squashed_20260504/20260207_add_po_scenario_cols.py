"""Add scenario_id and order_round columns to purchase_order

Revision ID: 20260207_po_scenario
Revises: 20260207_trm_data
Create Date: 2026-02-07

Adds Beer Game extensions to purchase_order table:
- scenario_id: Link to Beer Game scenario
- order_round: Round when PO was created
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260207_po_scenario'
down_revision = '20260207_trm_data'
branch_labels = None
depends_on = None


def upgrade():
    # Add Beer Game extension columns to purchase_order
    op.add_column('purchase_order', sa.Column('scenario_id', sa.Integer(), nullable=True))
    op.add_column('purchase_order', sa.Column('order_round', sa.Integer(), nullable=True))

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_po_scenario',
        'purchase_order', 'scenarios',
        ['scenario_id'], ['id'],
        ondelete='CASCADE'
    )

    # Add index for scenario_id and order_round
    op.create_index(
        'idx_po_scenario_round',
        'purchase_order',
        ['scenario_id', 'order_round']
    )


def downgrade():
    op.drop_index('idx_po_scenario_round', table_name='purchase_order')
    op.drop_constraint('fk_po_scenario', 'purchase_order', type_='foreignkey')
    op.drop_column('purchase_order', 'order_round')
    op.drop_column('purchase_order', 'scenario_id')

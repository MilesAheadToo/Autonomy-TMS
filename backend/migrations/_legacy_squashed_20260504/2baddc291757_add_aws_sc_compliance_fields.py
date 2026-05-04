"""add_aws_sc_compliance_fields

Revision ID: 2baddc291757
Revises: 988b35b7c60d
Create Date: 2026-01-21 07:39:17.187548

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2baddc291757'
down_revision: Union[str, None] = '988b35b7c60d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add AWS SC compliance fields to purchase_order table
    op.add_column('purchase_order', sa.Column('company_id', sa.String(length=100), nullable=True))
    op.add_column('purchase_order', sa.Column('order_type', sa.String(length=50), nullable=True, server_default='po'))
    op.add_column('purchase_order', sa.Column('supplier_reference_id', sa.String(length=100), nullable=True))
    op.add_column('purchase_order', sa.Column('source', sa.String(length=100), nullable=True))
    op.add_column('purchase_order', sa.Column('source_event_id', sa.String(length=100), nullable=True))
    op.add_column('purchase_order', sa.Column('source_update_dttm', sa.DateTime(), nullable=True))

    # Add indexes for new fields
    op.create_index('idx_po_company', 'purchase_order', ['company_id'], unique=False)
    op.create_index('idx_po_order_type', 'purchase_order', ['order_type'], unique=False)

    # Add AWS SC compliance fields to transfer_order table
    op.add_column('transfer_order', sa.Column('company_id', sa.String(length=100), nullable=True))
    op.add_column('transfer_order', sa.Column('order_type', sa.String(length=50), nullable=True, server_default='transfer'))
    op.add_column('transfer_order', sa.Column('from_tpartner_id', sa.String(length=100), nullable=True))
    op.add_column('transfer_order', sa.Column('to_tpartner_id', sa.String(length=100), nullable=True))
    op.add_column('transfer_order', sa.Column('source', sa.String(length=100), nullable=True))
    op.add_column('transfer_order', sa.Column('source_event_id', sa.String(length=100), nullable=True))
    op.add_column('transfer_order', sa.Column('source_update_dttm', sa.DateTime(), nullable=True))

    # Add indexes for new fields
    op.create_index('idx_to_company', 'transfer_order', ['company_id'], unique=False)
    op.create_index('idx_to_order_type', 'transfer_order', ['order_type'], unique=False)

    # Populate company_id from customer_id (use customer.name as company_id for now)
    # This will be updated in a follow-up migration when company table is added
    op.execute("""
        UPDATE purchase_order po
        SET company_id = (SELECT CAST(g.id AS VARCHAR) FROM customers g WHERE g.id = po.customer_id)
        WHERE po.customer_id IS NOT NULL
    """)

    op.execute("""
        UPDATE transfer_order tord
        SET company_id = (SELECT CAST(g.id AS VARCHAR) FROM customers g WHERE g.id = tord.customer_id)
        WHERE tord.customer_id IS NOT NULL
    """)


def downgrade() -> None:
    # Drop indexes first
    op.drop_index('idx_to_order_type', table_name='transfer_order')
    op.drop_index('idx_to_company', table_name='transfer_order')
    op.drop_index('idx_po_order_type', table_name='purchase_order')
    op.drop_index('idx_po_company', table_name='purchase_order')

    # Drop columns from transfer_order
    op.drop_column('transfer_order', 'source_update_dttm')
    op.drop_column('transfer_order', 'source_event_id')
    op.drop_column('transfer_order', 'source')
    op.drop_column('transfer_order', 'to_tpartner_id')
    op.drop_column('transfer_order', 'from_tpartner_id')
    op.drop_column('transfer_order', 'order_type')
    op.drop_column('transfer_order', 'company_id')

    # Drop columns from purchase_order
    op.drop_column('purchase_order', 'source_update_dttm')
    op.drop_column('purchase_order', 'source_event_id')
    op.drop_column('purchase_order', 'source')
    op.drop_column('purchase_order', 'supplier_reference_id')
    op.drop_column('purchase_order', 'order_type')
    op.drop_column('purchase_order', 'company_id')

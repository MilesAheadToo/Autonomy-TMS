"""Rename node_type_definitions to site_type_definitions

Revision ID: 20260206_site_type
Revises: 20260206_aws_sc_dm
Create Date: 2026-02-06

AWS SC DM Compliance: Rename node_type_definitions column to site_type_definitions
to align with the site terminology used throughout the codebase.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260206_site_type'
down_revision = '20260206_aws_sc_dm'
branch_labels = None
depends_on = None


def upgrade():
    # Rename column from node_type_definitions to site_type_definitions
    op.alter_column(
        'supply_chain_configs',
        'node_type_definitions',
        new_column_name='site_type_definitions'
    )


def downgrade():
    # Revert column name back to node_type_definitions
    op.alter_column(
        'supply_chain_configs',
        'site_type_definitions',
        new_column_name='node_type_definitions'
    )

"""Add external_identifiers JSON column to product and trading_partners tables.

Revision ID: 20260318_ext_ids
Revises: (auto-detected)
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "20260318_ext_ids"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product",
        sa.Column("external_identifiers", sa.JSON(), nullable=True,
                   comment="Typed external IDs: {sap_material_number, gtin, upc, ean, ...}"),
    )
    op.add_column(
        "trading_partners",
        sa.Column("external_identifiers", sa.JSON(), nullable=True,
                   comment="Typed external IDs: {duns, lei, sap_vendor_number, ...}"),
    )

    # Backfill existing duns_number and os_id into the new JSON field
    op.execute("""
        UPDATE trading_partners
        SET external_identifiers = jsonb_build_object(
            'duns', duns_number,
            'open_supplier_hub_id', os_id
        )
        WHERE duns_number IS NOT NULL OR os_id IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_column("trading_partners", "external_identifiers")
    op.drop_column("product", "external_identifiers")

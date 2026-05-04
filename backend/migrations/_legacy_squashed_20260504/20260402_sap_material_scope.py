"""Add material_scope JSONB column to sap_ingestion_jobs for BOM-scoped extraction.

Stores the approved material scope (list of MATNR values) so subsequent
extraction phases filter all material-keyed tables to only in-scope materials.

Revision ID: 20260402_material_scope
Revises: 20260402_bom_fields
Create Date: 2026-04-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '20260402_material_scope'
down_revision: Union[str, None] = '20260402_bom_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sap_ingestion_jobs",
        sa.Column("material_scope", JSONB, nullable=True,
                  comment="Approved material scope from BOM analysis: {materials: [...], vendors: [...], include_mrp_fallback: bool}"),
    )


def downgrade() -> None:
    op.drop_column("sap_ingestion_jobs", "material_scope")

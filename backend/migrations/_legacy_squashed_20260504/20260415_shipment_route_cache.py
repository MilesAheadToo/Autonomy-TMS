"""Create shipment_route_cache for OSRM-derived route polylines

Revision ID: 20260415_route_cache
Revises: 20260414_equip_move
Create Date: 2026-04-15 12:00:00.000000

Adds a small additive cache table that holds GeoJSON LineString geometry
for a shipment's route. Populated lazily by GET /shipments/{id}/route the
first time the map asks for it.

Why a separate table:
- Keeps the (large, hot) shipment row narrow
- Lets the cache be invalidated independently
- Doesn't require touching the existing shipment migration chain
"""
from alembic import op
import sqlalchemy as sa


revision = '20260415_route_cache'
down_revision = '20260414_equip_move'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'shipment_route_cache',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('shipment_id', sa.Integer(),
                  sa.ForeignKey('tms_shipment.id', ondelete='CASCADE'),
                  nullable=False, unique=True, index=True),
        sa.Column('geometry_geojson', sa.Text(), nullable=False,
                  comment='GeoJSON LineString as JSON-encoded text'),
        sa.Column('distance_meters', sa.Float(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('source', sa.String(50), nullable=False, server_default='osrm',
                  comment='osrm | mapbox | manual'),
        sa.Column('computed_at', sa.DateTime(),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('shipment_route_cache')

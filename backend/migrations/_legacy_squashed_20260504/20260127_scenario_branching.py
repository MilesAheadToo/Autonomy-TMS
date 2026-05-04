"""Scenario Branching with Delta Storage

Revision ID: 20260127_scenario_branching
Revises: 20260127_order_tracking
Create Date: 2026-01-27

Implements git-like scenario branching for supply chain configurations:
- Parent-child inheritance with delta storage
- Copy-on-write semantics for efficiency
- Scenario types: BASELINE, WORKING, SIMULATION
- Operations: branch, commit, rollback, merge, diff

Inspired by Kinaxis RapidResponse scenario management patterns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql, postgresql

# revision identifiers, used by Alembic.
revision = '20260127_scenario_branching'
down_revision = '20260127_order_tracking'
branch_labels = None
depends_on = None


def upgrade():
    # =========================================================================
    # Check Database State
    # =========================================================================

    # Check database dialect
    conn = op.get_bind()
    dialect_name = conn.dialect.name
    inspector = sa.inspect(conn)

    # Get existing columns in supply_chain_configs
    existing_columns = [col['name'] for col in inspector.get_columns('supply_chain_configs')]

    # =========================================================================
    # Extend supply_chain_configs Table for Scenario Branching
    # =========================================================================

    # Add parent_config_id for inheritance (if not exists)
    if 'parent_config_id' not in existing_columns:
        op.add_column('supply_chain_configs',
            sa.Column('parent_config_id', sa.Integer, nullable=True))

    # Add base_config_id to track original baseline
    if 'base_config_id' not in existing_columns:
        op.add_column('supply_chain_configs',
            sa.Column('base_config_id', sa.Integer, nullable=True))

    # Add scenario_type (use String for all databases, add enum later if needed)
    if 'scenario_type' not in existing_columns:
        op.add_column('supply_chain_configs',
            sa.Column('scenario_type',
                sa.String(20),
                server_default='BASELINE',
                nullable=False))

    # Add uses_delta_storage flag
    if 'uses_delta_storage' not in existing_columns:
        op.add_column('supply_chain_configs',
            sa.Column('uses_delta_storage', sa.Boolean, server_default='1', nullable=False))

    # Add version number for tracking
    if 'version' not in existing_columns:
        op.add_column('supply_chain_configs',
            sa.Column('version', sa.Integer, server_default='1', nullable=False))

    # Add is_active flag (skip if already exists - common column)
    if 'is_active' not in existing_columns:
        op.add_column('supply_chain_configs',
            sa.Column('is_active', sa.Boolean, server_default='1', nullable=False))

    # Add snapshot_data for full materialization if needed
    if 'snapshot_data' not in existing_columns:
        if dialect_name == 'postgresql':
            op.add_column('supply_chain_configs',
                sa.Column('snapshot_data', postgresql.JSON(astext_type=sa.Text()), nullable=True))
        else:
            op.add_column('supply_chain_configs',
                sa.Column('snapshot_data', sa.JSON, nullable=True))

    # Add timestamps for scenario lifecycle
    if 'branched_at' not in existing_columns:
        op.add_column('supply_chain_configs',
            sa.Column('branched_at', sa.DateTime, nullable=True))

    if 'committed_at' not in existing_columns:
        op.add_column('supply_chain_configs',
            sa.Column('committed_at', sa.DateTime, nullable=True))

    # Add foreign keys
    op.create_foreign_key(
        'fk_supply_chain_configs_parent',
        'supply_chain_configs', 'supply_chain_configs',
        ['parent_config_id'], ['id'],
        ondelete='SET NULL'
    )

    op.create_foreign_key(
        'fk_supply_chain_configs_base',
        'supply_chain_configs', 'supply_chain_configs',
        ['base_config_id'], ['id'],
        ondelete='SET NULL'
    )

    # Create indexes
    op.create_index('idx_sc_config_parent', 'supply_chain_configs', ['parent_config_id'])
    op.create_index('idx_sc_config_base', 'supply_chain_configs', ['base_config_id'])
    op.create_index('idx_sc_config_type', 'supply_chain_configs', ['scenario_type'])
    op.create_index('idx_sc_config_active', 'supply_chain_configs', ['is_active'])
    op.create_index('idx_sc_config_group_type', 'supply_chain_configs', ['customer_id', 'scenario_type'])

    # =========================================================================
    # Create config_deltas Table for Delta Storage
    # =========================================================================

    op.create_table(
        'config_deltas',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('config_id', sa.Integer, nullable=False),

        # Entity being changed (use String type to avoid enum creation issues)
        sa.Column('entity_type', sa.String(30), nullable=False),
        sa.Column('entity_id', sa.Integer, nullable=True),  # Null for create operations

        # Operation type (use String type to avoid enum creation issues)
        sa.Column('operation', sa.String(10), nullable=False),

        # Delta data (JSON)
        sa.Column('delta_data',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=False),

        # For update operations: store only changed fields
        sa.Column('changed_fields',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=True),

        # Original values for rollback (update/delete only)
        sa.Column('original_values',
            postgresql.JSON(astext_type=sa.Text()) if dialect_name == 'postgresql' else sa.JSON,
            nullable=True),

        # Audit fields
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('NOW()')),
        sa.Column('created_by', sa.String(36), nullable=True),
        sa.Column('description', sa.String(500), nullable=True),

        # Foreign key to config
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
    )

    # Create indexes for config_deltas
    op.create_index('idx_config_deltas_config', 'config_deltas', ['config_id'])
    op.create_index('idx_config_deltas_entity', 'config_deltas', ['entity_type', 'entity_id'])
    op.create_index('idx_config_deltas_operation', 'config_deltas', ['operation'])
    op.create_index('idx_config_deltas_created', 'config_deltas', ['created_at'])

    # =========================================================================
    # Create config_lineage Table for Efficient Ancestor Queries
    # =========================================================================

    op.create_table(
        'config_lineage',
        sa.Column('config_id', sa.Integer, nullable=False),
        sa.Column('ancestor_id', sa.Integer, nullable=False),
        sa.Column('depth', sa.Integer, nullable=False),  # 0=self, 1=parent, 2=grandparent, etc.

        # Composite primary key
        sa.PrimaryKeyConstraint('config_id', 'ancestor_id'),

        # Foreign keys
        sa.ForeignKeyConstraint(['config_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['ancestor_id'], ['supply_chain_configs.id'], ondelete='CASCADE'),
    )

    # Create indexes for config_lineage
    op.create_index('idx_config_lineage_config', 'config_lineage', ['config_id'])
    op.create_index('idx_config_lineage_ancestor', 'config_lineage', ['ancestor_id'])
    op.create_index('idx_config_lineage_depth', 'config_lineage', ['config_id', 'depth'])


def downgrade():
    # =========================================================================
    # Drop Tables
    # =========================================================================

    op.drop_table('config_lineage')
    op.drop_table('config_deltas')

    # =========================================================================
    # Drop Indexes from supply_chain_configs
    # =========================================================================

    op.drop_index('idx_sc_config_group_type', table_name='supply_chain_configs')
    op.drop_index('idx_sc_config_active', table_name='supply_chain_configs')
    op.drop_index('idx_sc_config_type', table_name='supply_chain_configs')
    op.drop_index('idx_sc_config_base', table_name='supply_chain_configs')
    op.drop_index('idx_sc_config_parent', table_name='supply_chain_configs')

    # =========================================================================
    # Drop Foreign Keys
    # =========================================================================

    op.drop_constraint('fk_supply_chain_configs_base', 'supply_chain_configs', type_='foreignkey')
    op.drop_constraint('fk_supply_chain_configs_parent', 'supply_chain_configs', type_='foreignkey')

    # =========================================================================
    # Drop Columns from supply_chain_configs
    # =========================================================================

    op.drop_column('supply_chain_configs', 'committed_at')
    op.drop_column('supply_chain_configs', 'branched_at')
    op.drop_column('supply_chain_configs', 'snapshot_data')
    op.drop_column('supply_chain_configs', 'is_active')
    op.drop_column('supply_chain_configs', 'version')
    op.drop_column('supply_chain_configs', 'uses_delta_storage')
    op.drop_column('supply_chain_configs', 'scenario_type')
    op.drop_column('supply_chain_configs', 'base_config_id')
    op.drop_column('supply_chain_configs', 'parent_config_id')

    # =========================================================================
    # Drop ENUM Types
    # =========================================================================

    conn = op.get_bind()
    dialect_name = conn.dialect.name

    if dialect_name == 'postgresql':
        # Drop ENUMs
        op.execute('DROP TYPE IF EXISTS delta_operation_enum')
        op.execute('DROP TYPE IF EXISTS entity_type_enum')
        op.execute('DROP TYPE IF EXISTS scenario_type_enum')

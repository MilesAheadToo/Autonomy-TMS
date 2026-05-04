"""add risk analysis tables

Revision ID: 20260123_risk_tables
Revises: acb744466de8
Create Date: 2026-01-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260123_risk_tables'
down_revision = 'acb744466de8'
branch_labels = None
depends_on = None


def upgrade():
    # Create risk_alerts table
    op.create_table(
        'risk_alerts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('alert_id', sa.String(255), unique=True, nullable=False, index=True),
        # Alert classification
        sa.Column('type', sa.String(50), nullable=False, index=True),
        sa.Column('severity', sa.String(20), nullable=False, index=True),
        # Entity references
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id'), nullable=True),
        sa.Column('product_id', sa.String(255), nullable=False, index=True),
        sa.Column('site_id', sa.String(255), nullable=False, index=True),
        sa.Column('vendor_id', sa.String(255), nullable=True),
        # Risk metrics
        sa.Column('probability', sa.Float(), nullable=True),
        sa.Column('days_until_stockout', sa.Integer(), nullable=True),
        sa.Column('days_of_supply', sa.Float(), nullable=True),
        sa.Column('excess_quantity', sa.Float(), nullable=True),
        sa.Column('cost_impact', sa.Float(), nullable=True),
        # Message and recommendation
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('recommended_action', sa.Text(), nullable=False),
        # Risk factors (JSON)
        sa.Column('factors', sa.JSON(), nullable=True),
        # Status tracking
        sa.Column('status', sa.String(20), server_default='ACTIVE', index=True),
        sa.Column('acknowledged_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        # Timestamps
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, index=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # Create indexes for risk_alerts
    op.create_index('idx_risk_product_site', 'risk_alerts', ['product_id', 'site_id'])
    op.create_index('idx_risk_type_severity', 'risk_alerts', ['type', 'severity'])
    op.create_index('idx_risk_status_created', 'risk_alerts', ['status', 'created_at'])


    # Create watchlists table
    op.create_table(
        'watchlists',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        # Ownership
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id'), nullable=True, index=True),
        # Monitoring configuration
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id'), nullable=True),
        # Filters (JSON)
        sa.Column('product_filter', sa.JSON(), nullable=True),
        sa.Column('site_filter', sa.JSON(), nullable=True),
        # Alert thresholds (override defaults)
        sa.Column('stockout_threshold', sa.Float(), nullable=True),
        sa.Column('overstock_threshold_days', sa.Float(), nullable=True),
        sa.Column('leadtime_variance_threshold', sa.Float(), nullable=True),
        # Notification settings
        sa.Column('enable_notifications', sa.Boolean(), server_default=sa.text('true')),
        sa.Column('notification_frequency', sa.String(20), server_default='DAILY'),
        sa.Column('notification_channels', sa.JSON(), nullable=True),
        sa.Column('notification_recipients', sa.JSON(), nullable=True),
        # Status
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), index=True),
        # Timestamps
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(), nullable=True),
    )


    # Create risk_predictions table
    op.create_table(
        'risk_predictions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        # Prediction metadata
        sa.Column('model_name', sa.String(100), nullable=False),
        sa.Column('model_version', sa.String(50), nullable=False),
        sa.Column('prediction_date', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, index=True),
        # Entity references
        sa.Column('product_id', sa.String(255), nullable=False, index=True),
        sa.Column('site_id', sa.String(255), nullable=False, index=True),
        # Prediction type
        sa.Column('prediction_type', sa.String(50), nullable=False),
        # Forecast horizon
        sa.Column('horizon_days', sa.Integer(), nullable=False),
        sa.Column('target_date', sa.DateTime(), nullable=False, index=True),
        # Predicted values
        sa.Column('predicted_value', sa.Float(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('prediction_interval_lower', sa.Float(), nullable=True),
        sa.Column('prediction_interval_upper', sa.Float(), nullable=True),
        # Actual outcome (for validation)
        sa.Column('actual_value', sa.Float(), nullable=True),
        sa.Column('actual_recorded_at', sa.DateTime(), nullable=True),
        sa.Column('prediction_error', sa.Float(), nullable=True),
        # Model features (JSON)
        sa.Column('features', sa.JSON(), nullable=True),
        # Timestamps
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )

    # Create indexes for risk_predictions
    op.create_index('idx_pred_product_site_date', 'risk_predictions', ['product_id', 'site_id', 'target_date'])
    op.create_index('idx_pred_model_type', 'risk_predictions', ['model_name', 'prediction_type'])


def downgrade():
    # Drop indexes
    op.drop_index('idx_pred_model_type', 'risk_predictions')
    op.drop_index('idx_pred_product_site_date', 'risk_predictions')
    op.drop_index('idx_risk_status_created', 'risk_alerts')
    op.drop_index('idx_risk_type_severity', 'risk_alerts')
    op.drop_index('idx_risk_product_site', 'risk_alerts')

    # Drop tables
    op.drop_table('risk_predictions')
    op.drop_table('watchlists')
    op.drop_table('risk_alerts')

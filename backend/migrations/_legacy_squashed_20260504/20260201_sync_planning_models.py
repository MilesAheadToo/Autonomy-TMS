"""SAP Sync Cadence and Planning Cycle Models

Revision ID: 20260201_sync_planning
Revises: 20260201_terminology_refactoring
Create Date: 2026-02-01 18:00:00.000000

Creates tables for:
1. SAP Data Import Cadence System (SyncJobConfig, SyncJobExecution, SyncTableResult)
2. Workflow System (WorkflowTemplate, WorkflowExecution, WorkflowStepExecution)
3. Planning Cycle Management (PlanningCycle, PlanningSnapshot, SnapshotDelta, SnapshotLineage)
4. Decision Tracking (PlanningDecision, DecisionHistory, DecisionComment)
5. APScheduler Job Store
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20260201_sync_planning'
down_revision = '20260130_stochastic_lead_times'  # Connect to current head (bypass broken terminology chain)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ==================================================
    # APScheduler Job Store Table
    # ==================================================
    op.create_table(
        'apscheduler_jobs',
        sa.Column('id', sa.String(191), primary_key=True),
        sa.Column('next_run_time', sa.Float(), nullable=True, index=True),
        sa.Column('job_state', sa.LargeBinary(), nullable=False),
    )

    # ==================================================
    # Workflow Templates Table
    # ==================================================
    op.create_table(
        'workflow_templates',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('code', sa.String(50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE'), nullable=True),
        sa.Column('is_global', sa.Boolean(), default=False),
        sa.Column('trigger_types', sa.JSON(), nullable=True),
        sa.Column('trigger_data_types', sa.JSON(), nullable=True),
        sa.Column('steps', sa.JSON(), nullable=False),
        sa.Column('timeout_minutes', sa.Integer(), default=60),
        sa.Column('max_concurrent_executions', sa.Integer(), default=1),
        sa.Column('retry_on_failure', sa.Boolean(), default=False),
        sa.Column('max_retries', sa.Integer(), default=0),
        sa.Column('notify_on_start', sa.Boolean(), default=False),
        sa.Column('notify_on_completion', sa.Boolean(), default=True),
        sa.Column('notify_on_failure', sa.Boolean(), default=True),
        sa.Column('notification_config', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('is_default', sa.Boolean(), default=False),
        sa.Column('version', sa.Integer(), default=1),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_workflow_template_code_group', 'workflow_templates', ['code', 'customer_id'], unique=True)
    op.create_index('ix_workflow_template_active', 'workflow_templates', ['is_active'])
    op.create_index('ix_workflow_templates_group_id', 'workflow_templates', ['customer_id'])

    # ==================================================
    # Sync Job Configs Table
    # ==================================================
    op.create_table(
        'sync_job_configs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('data_type', sa.String(50), nullable=False),
        sa.Column('name', sa.String(100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('cron_expression', sa.String(100), nullable=False),
        sa.Column('timezone', sa.String(50), default='UTC'),
        sa.Column('is_enabled', sa.Boolean(), default=True, nullable=False),
        sa.Column('use_delta_load', sa.Boolean(), default=True),
        sa.Column('lookback_days', sa.Integer(), default=1),
        sa.Column('max_records_per_batch', sa.Integer(), default=10000),
        sa.Column('connection_config', sa.JSON(), nullable=True),
        sa.Column('sap_tables', sa.JSON(), nullable=False),
        sa.Column('field_mappings', sa.JSON(), nullable=True),
        sa.Column('max_retries', sa.Integer(), default=3),
        sa.Column('retry_delay_seconds', sa.Integer(), default=300),
        sa.Column('trigger_workflow', sa.Boolean(), default=True),
        sa.Column('workflow_template_id', sa.Integer(), sa.ForeignKey('workflow_templates.id', ondelete='SET NULL'), nullable=True),
        sa.Column('workflow_chain', sa.JSON(), nullable=True),
        sa.Column('notify_on_failure', sa.Boolean(), default=True),
        sa.Column('notify_on_success', sa.Boolean(), default=False),
        sa.Column('notification_emails', sa.JSON(), nullable=True),
        sa.Column('apscheduler_job_id', sa.String(100), nullable=True, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_sync_job_config_group_type', 'sync_job_configs', ['customer_id', 'data_type'], unique=True)
    op.create_index('ix_sync_job_config_enabled', 'sync_job_configs', ['is_enabled'])
    op.create_index('ix_sync_job_configs_group_id', 'sync_job_configs', ['customer_id'])
    op.create_index('ix_sync_job_configs_data_type', 'sync_job_configs', ['data_type'])

    # ==================================================
    # Planning Cycles Table
    # ==================================================
    op.create_table(
        'planning_cycles',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('code', sa.String(50), nullable=False),
        sa.Column('cycle_type', sa.String(20), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('supply_chain_configs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('planning_horizon_weeks', sa.Integer(), default=52),
        sa.Column('status', sa.String(20), default='draft', nullable=False),
        sa.Column('status_changed_at', sa.DateTime(), nullable=True),
        sa.Column('status_changed_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status_notes', sa.Text(), nullable=True),
        sa.Column('baseline_snapshot_id', sa.Integer(), nullable=True),  # FK added later
        sa.Column('current_snapshot_id', sa.Integer(), nullable=True),  # FK added later
        sa.Column('published_snapshot_id', sa.Integer(), nullable=True),  # FK added later
        sa.Column('metrics_summary', sa.JSON(), nullable=True),
        sa.Column('data_collection_started_at', sa.DateTime(), nullable=True),
        sa.Column('data_collection_completed_at', sa.DateTime(), nullable=True),
        sa.Column('planning_started_at', sa.DateTime(), nullable=True),
        sa.Column('planning_completed_at', sa.DateTime(), nullable=True),
        sa.Column('review_started_at', sa.DateTime(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('approved_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('approval_notes', sa.Text(), nullable=True),
        sa.Column('retention_tier', sa.String(10), default='hot'),
        sa.Column('archived_at', sa.DateTime(), nullable=True),
        sa.Column('previous_cycle_id', sa.Integer(), sa.ForeignKey('planning_cycles.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_planning_cycle_group_period', 'planning_cycles', ['customer_id', 'period_start', 'period_end'])
    op.create_index('ix_planning_cycle_group_code', 'planning_cycles', ['customer_id', 'code'], unique=True)
    op.create_index('ix_planning_cycle_status', 'planning_cycles', ['status'])
    op.create_index('ix_planning_cycles_group_id', 'planning_cycles', ['customer_id'])

    # ==================================================
    # Planning Snapshots Table
    # ==================================================
    op.create_table(
        'planning_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cycle_id', sa.Integer(), sa.ForeignKey('planning_cycles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('parent_snapshot_id', sa.Integer(), sa.ForeignKey('planning_snapshots.id', ondelete='SET NULL'), nullable=True),
        sa.Column('base_snapshot_id', sa.Integer(), sa.ForeignKey('planning_snapshots.id', ondelete='SET NULL'), nullable=True),
        sa.Column('snapshot_type', sa.String(20), nullable=False),
        sa.Column('commit_message', sa.String(500), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('sync_execution_id', sa.Integer(), nullable=True),  # FK added later
        sa.Column('workflow_execution_id', sa.Integer(), nullable=True),  # FK added later
        sa.Column('storage_tier', sa.String(10), default='hot', nullable=False),
        sa.Column('uses_delta_storage', sa.Boolean(), default=True),
        sa.Column('is_materialized', sa.Boolean(), default=False),
        sa.Column('demand_plan_data', sa.JSON(), nullable=True),
        sa.Column('supply_plan_data', sa.JSON(), nullable=True),
        sa.Column('inventory_data', sa.JSON(), nullable=True),
        sa.Column('forecast_data', sa.JSON(), nullable=True),
        sa.Column('kpi_data', sa.JSON(), nullable=True),
        sa.Column('record_counts', sa.JSON(), nullable=True),
        sa.Column('validation_status', sa.String(20), nullable=True),
        sa.Column('validation_issues', sa.JSON(), nullable=True),
        sa.Column('data_size_bytes', sa.Integer(), nullable=True),
        sa.Column('compressed_size_bytes', sa.Integer(), nullable=True),
        sa.Column('delta_count', sa.Integer(), default=0),
        sa.Column('data_hash', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('collapsed_at', sa.DateTime(), nullable=True),
        sa.Column('collapsed_to_id', sa.Integer(), sa.ForeignKey('planning_snapshots.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_snapshot_cycle_version', 'planning_snapshots', ['cycle_id', 'version'], unique=True)
    op.create_index('ix_snapshot_parent', 'planning_snapshots', ['parent_snapshot_id'])
    op.create_index('ix_snapshot_tier_expires', 'planning_snapshots', ['storage_tier', 'expires_at'])
    op.create_index('ix_snapshot_type_created', 'planning_snapshots', ['snapshot_type', 'created_at'])
    op.create_index('ix_planning_snapshots_cycle_id', 'planning_snapshots', ['cycle_id'])
    op.create_index('ix_planning_snapshots_created_at', 'planning_snapshots', ['created_at'])

    # Add FK constraints to planning_cycles for snapshot references
    op.create_foreign_key(
        'fk_planning_cycles_baseline_snapshot',
        'planning_cycles', 'planning_snapshots',
        ['baseline_snapshot_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_planning_cycles_current_snapshot',
        'planning_cycles', 'planning_snapshots',
        ['current_snapshot_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_planning_cycles_published_snapshot',
        'planning_cycles', 'planning_snapshots',
        ['published_snapshot_id'], ['id'],
        ondelete='SET NULL'
    )

    # ==================================================
    # Workflow Executions Table
    # ==================================================
    op.create_table(
        'workflow_executions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('template_id', sa.Integer(), sa.ForeignKey('workflow_templates.id', ondelete='SET NULL'), nullable=True),
        sa.Column('template_code', sa.String(50), nullable=False),
        sa.Column('template_version', sa.Integer(), nullable=True),
        sa.Column('trigger_type', sa.String(30), nullable=False),
        sa.Column('trigger_source_id', sa.Integer(), nullable=True),
        sa.Column('trigger_source_type', sa.String(50), nullable=True),
        sa.Column('trigger_metadata', sa.JSON(), nullable=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE'), nullable=True),
        sa.Column('planning_cycle_id', sa.Integer(), sa.ForeignKey('planning_cycles.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(30), default='pending', nullable=False),
        sa.Column('current_step', sa.Integer(), default=0),
        sa.Column('total_steps', sa.Integer(), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('timeout_at', sa.DateTime(), nullable=True),
        sa.Column('output_data', sa.JSON(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('error_step', sa.Integer(), nullable=True),
        sa.Column('retry_count', sa.Integer(), default=0),
        sa.Column('triggered_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_workflow_exec_status_created', 'workflow_executions', ['status', 'created_at'])
    op.create_index('ix_workflow_exec_group_created', 'workflow_executions', ['customer_id', 'created_at'])
    op.create_index('ix_workflow_exec_template_status', 'workflow_executions', ['template_id', 'status'])
    op.create_index('ix_workflow_exec_trigger', 'workflow_executions', ['trigger_type', 'trigger_source_type', 'trigger_source_id'])
    op.create_index('ix_workflow_executions_template_code', 'workflow_executions', ['template_code'])

    # ==================================================
    # Workflow Step Executions Table
    # ==================================================
    op.create_table(
        'workflow_step_executions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('workflow_id', sa.Integer(), sa.ForeignKey('workflow_executions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('step_number', sa.Integer(), nullable=False),
        sa.Column('step_name', sa.String(100), nullable=True),
        sa.Column('step_type', sa.String(30), nullable=False),
        sa.Column('step_config', sa.JSON(), nullable=True),
        sa.Column('timeout_seconds', sa.Integer(), nullable=True),
        sa.Column('continue_on_failure', sa.Boolean(), default=False),
        sa.Column('status', sa.String(30), default='pending', nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('input_data', sa.JSON(), nullable=True),
        sa.Column('output_data', sa.JSON(), nullable=True),
        sa.Column('metrics', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('error_details', sa.JSON(), nullable=True),
        sa.Column('retry_count', sa.Integer(), default=0),
    )
    op.create_index('ix_workflow_step_workflow_number', 'workflow_step_executions', ['workflow_id', 'step_number'], unique=True)
    op.create_index('ix_workflow_step_executions_workflow_id', 'workflow_step_executions', ['workflow_id'])

    # ==================================================
    # Sync Job Executions Table
    # ==================================================
    op.create_table(
        'sync_job_executions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('sync_job_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('apscheduler_job_id', sa.String(100), nullable=True),
        sa.Column('execution_mode', sa.String(20), default='scheduled'),
        sa.Column('triggered_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(20), default='pending', nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('total_records', sa.Integer(), default=0),
        sa.Column('new_records', sa.Integer(), default=0),
        sa.Column('updated_records', sa.Integer(), default=0),
        sa.Column('deleted_records', sa.Integer(), default=0),
        sa.Column('unchanged_records', sa.Integer(), default=0),
        sa.Column('failed_records', sa.Integer(), default=0),
        sa.Column('load_mode', sa.String(20), nullable=True),
        sa.Column('delta_from_timestamp', sa.DateTime(), nullable=True),
        sa.Column('delta_to_timestamp', sa.DateTime(), nullable=True),
        sa.Column('validation_issues', sa.Integer(), default=0),
        sa.Column('validation_warnings', sa.Integer(), default=0),
        sa.Column('z_fields_found', sa.JSON(), nullable=True),
        sa.Column('retry_count', sa.Integer(), default=0),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('error_details', sa.JSON(), nullable=True),
        sa.Column('workflow_triggered', sa.Boolean(), default=False),
        sa.Column('workflow_execution_id', sa.Integer(), sa.ForeignKey('workflow_executions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('planning_cycle_id', sa.Integer(), sa.ForeignKey('planning_cycles.id', ondelete='SET NULL'), nullable=True),
        sa.Column('snapshot_id', sa.Integer(), sa.ForeignKey('planning_snapshots.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_sync_job_exec_status_created', 'sync_job_executions', ['status', 'created_at'])
    op.create_index('ix_sync_job_exec_config_created', 'sync_job_executions', ['config_id', 'created_at'])
    op.create_index('ix_sync_job_exec_config_status', 'sync_job_executions', ['config_id', 'status'])
    op.create_index('ix_sync_job_executions_config_id', 'sync_job_executions', ['config_id'])
    op.create_index('ix_sync_job_executions_apscheduler_job_id', 'sync_job_executions', ['apscheduler_job_id'])

    # Add FK for sync_execution_id in planning_snapshots
    op.create_foreign_key(
        'fk_planning_snapshots_sync_execution',
        'planning_snapshots', 'sync_job_executions',
        ['sync_execution_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_planning_snapshots_workflow_execution',
        'planning_snapshots', 'workflow_executions',
        ['workflow_execution_id'], ['id'],
        ondelete='SET NULL'
    )

    # ==================================================
    # Sync Table Results Table
    # ==================================================
    op.create_table(
        'sync_table_results',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('execution_id', sa.Integer(), sa.ForeignKey('sync_job_executions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('table_name', sa.String(50), nullable=False),
        sa.Column('records_extracted', sa.Integer(), default=0),
        sa.Column('records_loaded', sa.Integer(), default=0),
        sa.Column('records_new', sa.Integer(), default=0),
        sa.Column('records_updated', sa.Integer(), default=0),
        sa.Column('records_deleted', sa.Integer(), default=0),
        sa.Column('records_failed', sa.Integer(), default=0),
        sa.Column('records_skipped', sa.Integer(), default=0),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('status', sa.String(20), default='pending'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('last_change_timestamp', sa.DateTime(), nullable=True),
        sa.Column('delta_state_key', sa.String(100), nullable=True),
        sa.Column('validation_issues', sa.JSON(), nullable=True),
    )
    op.create_index('ix_sync_table_result_exec_table', 'sync_table_results', ['execution_id', 'table_name'])
    op.create_index('ix_sync_table_results_execution_id', 'sync_table_results', ['execution_id'])

    # ==================================================
    # Snapshot Deltas Table
    # ==================================================
    op.create_table(
        'snapshot_deltas',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('snapshot_id', sa.Integer(), sa.ForeignKey('planning_snapshots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('entity_type', sa.String(30), nullable=False),
        sa.Column('entity_key', sa.String(200), nullable=True),
        sa.Column('operation', sa.String(10), nullable=False),
        sa.Column('delta_data', sa.JSON(), nullable=False),
        sa.Column('changed_fields', sa.JSON(), nullable=True),
        sa.Column('original_values', sa.JSON(), nullable=True),
        sa.Column('change_reason', sa.String(200), nullable=True),
        sa.Column('decision_id', sa.Integer(), nullable=True),  # FK added later
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_snapshot_delta_entity', 'snapshot_deltas', ['snapshot_id', 'entity_type', 'entity_key'])
    op.create_index('ix_snapshot_delta_operation', 'snapshot_deltas', ['snapshot_id', 'operation'])
    op.create_index('ix_snapshot_deltas_snapshot_id', 'snapshot_deltas', ['snapshot_id'])
    op.create_index('ix_snapshot_deltas_entity_type', 'snapshot_deltas', ['entity_type'])

    # ==================================================
    # Snapshot Lineage Table
    # ==================================================
    op.create_table(
        'snapshot_lineage',
        sa.Column('snapshot_id', sa.Integer(), sa.ForeignKey('planning_snapshots.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('ancestor_id', sa.Integer(), sa.ForeignKey('planning_snapshots.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('depth', sa.Integer(), nullable=False),
    )
    op.create_index('ix_snapshot_lineage_ancestor', 'snapshot_lineage', ['ancestor_id'])
    op.create_index('ix_snapshot_lineage_depth', 'snapshot_lineage', ['snapshot_id', 'depth'])

    # ==================================================
    # Planning Decisions Table
    # ==================================================
    op.create_table(
        'planning_decisions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cycle_id', sa.Integer(), sa.ForeignKey('planning_cycles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('snapshot_id', sa.Integer(), sa.ForeignKey('planning_snapshots.id', ondelete='SET NULL'), nullable=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('decision_code', sa.String(50), nullable=False),
        sa.Column('category', sa.String(30), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('priority', sa.String(20), default='medium'),
        sa.Column('product_id', sa.String(100), nullable=True),
        sa.Column('site_id', sa.String(100), nullable=True),
        sa.Column('vendor_id', sa.String(100), nullable=True),
        sa.Column('customer_id', sa.String(100), nullable=True),
        sa.Column('period_start', sa.DateTime(), nullable=True),
        sa.Column('period_end', sa.DateTime(), nullable=True),
        sa.Column('recommendation_id', sa.String(36), nullable=True),
        sa.Column('ai_recommended_value', sa.JSON(), nullable=True),
        sa.Column('ai_confidence', sa.Float(), nullable=True),
        sa.Column('ai_explanation', sa.Text(), nullable=True),
        sa.Column('original_value', sa.JSON(), nullable=True),
        sa.Column('decided_value', sa.JSON(), nullable=True),
        sa.Column('value_delta', sa.JSON(), nullable=True),
        sa.Column('reason_code', sa.String(50), nullable=True),
        sa.Column('reason_text', sa.Text(), nullable=True),
        sa.Column('supporting_data', sa.JSON(), nullable=True),
        sa.Column('estimated_impact', sa.JSON(), nullable=True),
        sa.Column('actual_impact', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(30), default='pending', nullable=False),
        sa.Column('requires_approval', sa.Boolean(), default=False),
        sa.Column('approval_level', sa.String(50), nullable=True),
        sa.Column('approval_threshold', sa.Float(), nullable=True),
        sa.Column('decided_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('approved_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('approval_notes', sa.Text(), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('applied_at', sa.DateTime(), nullable=True),
        sa.Column('applied_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('execution_snapshot_id', sa.Integer(), sa.ForeignKey('planning_snapshots.id', ondelete='SET NULL'), nullable=True),
        sa.Column('reverted_at', sa.DateTime(), nullable=True),
        sa.Column('reverted_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('revert_reason', sa.Text(), nullable=True),
        sa.Column('revert_snapshot_id', sa.Integer(), sa.ForeignKey('planning_snapshots.id', ondelete='SET NULL'), nullable=True),
        sa.Column('superseded_by_id', sa.Integer(), sa.ForeignKey('planning_decisions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('supersedes_id', sa.Integer(), sa.ForeignKey('planning_decisions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('assigned_to', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('watchers', sa.JSON(), nullable=True),
        sa.Column('comments_count', sa.Integer(), default=0),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index('ix_decision_cycle_category', 'planning_decisions', ['cycle_id', 'category'])
    op.create_index('ix_decision_product_site', 'planning_decisions', ['product_id', 'site_id'])
    op.create_index('ix_decision_status_created', 'planning_decisions', ['status', 'created_at'])
    op.create_index('ix_decision_action_category', 'planning_decisions', ['action', 'category'])
    op.create_index('ix_decision_reason_code', 'planning_decisions', ['reason_code'])
    op.create_index('ix_decision_assigned', 'planning_decisions', ['assigned_to', 'status'])
    op.create_index('ix_planning_decisions_cycle_id', 'planning_decisions', ['cycle_id'])
    op.create_index('ix_planning_decisions_group_id', 'planning_decisions', ['customer_id'])
    op.create_index('ix_planning_decisions_decision_code', 'planning_decisions', ['decision_code'])
    op.create_index('ix_planning_decisions_recommendation_id', 'planning_decisions', ['recommendation_id'])
    op.create_index('ix_planning_decisions_category', 'planning_decisions', ['category'])
    op.create_index('ix_planning_decisions_action', 'planning_decisions', ['action'])
    op.create_index('ix_planning_decisions_status', 'planning_decisions', ['status'])

    # Add FK for decision_id in snapshot_deltas
    op.create_foreign_key(
        'fk_snapshot_deltas_decision',
        'snapshot_deltas', 'planning_decisions',
        ['decision_id'], ['id'],
        ondelete='SET NULL'
    )

    # ==================================================
    # Decision History Table
    # ==================================================
    op.create_table(
        'decision_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('decision_id', sa.Integer(), sa.ForeignKey('planning_decisions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('change_type', sa.String(50), nullable=False),
        sa.Column('previous_status', sa.String(50), nullable=True),
        sa.Column('new_status', sa.String(50), nullable=True),
        sa.Column('changed_fields', sa.JSON(), nullable=True),
        sa.Column('previous_values', sa.JSON(), nullable=True),
        sa.Column('new_values', sa.JSON(), nullable=True),
        sa.Column('changed_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('changed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('change_notes', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('session_id', sa.String(100), nullable=True),
        sa.Column('snapshot_id', sa.Integer(), sa.ForeignKey('planning_snapshots.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_decision_history_decision_at', 'decision_history', ['decision_id', 'changed_at'])
    op.create_index('ix_decision_history_type', 'decision_history', ['decision_id', 'change_type'])
    op.create_index('ix_decision_history_decision_id', 'decision_history', ['decision_id'])

    # ==================================================
    # Decision Comments Table
    # ==================================================
    op.create_table(
        'decision_comments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('decision_id', sa.Integer(), sa.ForeignKey('planning_decisions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_internal', sa.Boolean(), default=False),
        sa.Column('parent_comment_id', sa.Integer(), sa.ForeignKey('decision_comments.id', ondelete='SET NULL'), nullable=True),
        sa.Column('mentioned_users', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('is_edited', sa.Boolean(), default=False),
        sa.Column('is_deleted', sa.Boolean(), default=False),
    )
    op.create_index('ix_decision_comment_decision', 'decision_comments', ['decision_id', 'created_at'])
    op.create_index('ix_decision_comments_decision_id', 'decision_comments', ['decision_id'])


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table('decision_comments')
    op.drop_table('decision_history')

    # Remove FK constraint before dropping planning_decisions
    op.drop_constraint('fk_snapshot_deltas_decision', 'snapshot_deltas', type_='foreignkey')

    op.drop_table('planning_decisions')
    op.drop_table('snapshot_lineage')
    op.drop_table('snapshot_deltas')
    op.drop_table('sync_table_results')

    # Remove FK constraints from planning_snapshots
    op.drop_constraint('fk_planning_snapshots_sync_execution', 'planning_snapshots', type_='foreignkey')
    op.drop_constraint('fk_planning_snapshots_workflow_execution', 'planning_snapshots', type_='foreignkey')

    op.drop_table('sync_job_executions')
    op.drop_table('workflow_step_executions')
    op.drop_table('workflow_executions')

    # Remove FK constraints from planning_cycles
    op.drop_constraint('fk_planning_cycles_baseline_snapshot', 'planning_cycles', type_='foreignkey')
    op.drop_constraint('fk_planning_cycles_current_snapshot', 'planning_cycles', type_='foreignkey')
    op.drop_constraint('fk_planning_cycles_published_snapshot', 'planning_cycles', type_='foreignkey')

    op.drop_table('planning_snapshots')
    op.drop_table('planning_cycles')
    op.drop_table('sync_job_configs')
    op.drop_table('workflow_templates')
    op.drop_table('apscheduler_jobs')

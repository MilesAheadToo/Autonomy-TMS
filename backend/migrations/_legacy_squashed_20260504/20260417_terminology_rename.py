"""Terminology rename: game→scenario, round→period.

Mirrors SCP migration 20260417_terminology_rename. Keeps TMS DB
aligned with Core's canonical naming.

Revision ID: 20260417_term
Revises: (no dependency on previous — runs against whatever HEAD is)
Create Date: 2026-04-17
"""
from alembic import op


revision = "20260417_term"
down_revision = None
branch_labels = None
depends_on = None


# Column renames — (table, old_name, new_name)
_RENAMES = [
    ("agent_suggestions", "round", "period"),
    ("inbound_order_line", "round_number", "period_number"),
    ("inv_level", "round_number", "period_number"),
    ("inv_projection", "round_number", "period_number"),
    ("notification_preferences", "game_completed", "scenario_completed"),
    ("notification_preferences", "game_started", "scenario_started"),
    ("notification_preferences", "round_started", "period_started"),
    ("orders", "round_number", "period_number"),
    ("purchase_order", "arrival_round", "arrival_period"),
    ("purchase_order", "order_round", "order_period"),
    ("round_metric", "round_number", "period_number"),
    ("scenario_rounds", "round_number", "period_number"),
    ("scenario_user_actions", "round_id", "period_id"),
    ("scenario_user_periods", "round_phase", "period_phase"),
    ("scenario_user_periods", "scenario_round_id", "scenario_period_id"),
    ("scenario_user_stats", "total_rounds_played", "total_periods_played"),
    ("supply_plan", "round_number", "period_number"),
    ("transfer_order", "arrival_round", "arrival_period"),
    ("transfer_order", "order_round", "order_period"),
    ("transfer_order", "source_participant_round_id", "source_participant_period_id"),
    ("what_if_analyses", "round", "period"),
]


def _safe_rename(table, old, new):
    """Rename column only if the old column exists (idempotent)."""
    conn = op.get_bind()
    result = conn.execute(
        __import__("sqlalchemy").text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c AND table_schema = 'public'"
        ),
        {"t": table, "c": old},
    ).scalar()
    if result:
        op.alter_column(table, old, new_column_name=new)


def _safe_rename_table(old, new):
    """Rename table only if it exists."""
    conn = op.get_bind()
    result = conn.execute(
        __import__("sqlalchemy").text(
            "SELECT 1 FROM pg_tables WHERE tablename = :t AND schemaname = 'public'"
        ),
        {"t": old},
    ).scalar()
    if result:
        op.rename_table(old, new)


def upgrade():
    for table, old, new in _RENAMES:
        _safe_rename(table, old, new)

    _safe_rename_table("rounds", "periods")
    _safe_rename_table("scenario_rounds", "scenario_periods")
    _safe_rename_table("round_metric", "period_metric")


def downgrade():
    _safe_rename_table("period_metric", "round_metric")
    _safe_rename_table("scenario_periods", "scenario_rounds")
    _safe_rename_table("periods", "rounds")

    for table, old, new in _RENAMES:
        actual_table = table
        if table == "rounds":
            actual_table = "periods"
        elif table == "scenario_rounds":
            actual_table = "scenario_periods"
        elif table == "round_metric":
            actual_table = "period_metric"
        _safe_rename(actual_table, new, old)

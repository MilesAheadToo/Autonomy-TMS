"""Mirror Core 0012: external_signal_sources credential / license columns.

Revision ID: 20260501_ext_signal_creds
Revises: 20260501_canonical_cfg_nn
Create Date: 2026-05-01

Mirrors Core ``0012_external_signal_source_credentials`` — adds 9
nullable columns to ``external_signal_sources`` for license-aware
credential storage and subscription metadata, plus a partial index on
``subscription_expires_at WHERE NOT NULL`` for license-expiry sweeps.

Legacy ``api_key_encrypted`` stays for back-compat. New paid sources
should populate ``credentials_encrypted`` (typed by ``auth_type``)
instead. See ``Autonomy-Core/docs/CONSUMER_ADOPTION_LOG.md`` 2026-04-30
entry "Context Engine source registry expansion".

Idempotent — column adds are guarded by ``information_schema``;
``CREATE INDEX IF NOT EXISTS`` for the index. Additive only — no
existing-row impact.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260501_ext_signal_creds"
down_revision = "20260501_canonical_cfg_nn"
branch_labels = None
depends_on = None


_NEW_COLUMNS = [
    ("auth_type",                 sa.Column("auth_type", sa.String(20), nullable=True)),
    ("credentials_encrypted",     sa.Column("credentials_encrypted", sa.JSON(), nullable=True)),
    ("subscription_tier",         sa.Column("subscription_tier", sa.String(50), nullable=True)),
    ("subscription_holder",       sa.Column("subscription_holder", sa.String(255), nullable=True)),
    ("subscription_expires_at",   sa.Column("subscription_expires_at", sa.DateTime(), nullable=True)),
    ("vendor_account_id",         sa.Column("vendor_account_id", sa.String(255), nullable=True)),
    ("vendor_url",                sa.Column("vendor_url", sa.String(500), nullable=True)),
    ("subscription_url",          sa.Column("subscription_url", sa.String(500), nullable=True)),
    ("last_credential_rotated_at", sa.Column("last_credential_rotated_at", sa.DateTime(), nullable=True)),
]


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def upgrade() -> None:
    for name, column in _NEW_COLUMNS:
        if not _column_exists("external_signal_sources", name):
            op.add_column("external_signal_sources", column)

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ext_signal_source_expiry
        ON external_signal_sources (subscription_expires_at)
        WHERE subscription_expires_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ext_signal_source_expiry")
    for name, _ in reversed(_NEW_COLUMNS):
        if _column_exists("external_signal_sources", name):
            op.drop_column("external_signal_sources", name)

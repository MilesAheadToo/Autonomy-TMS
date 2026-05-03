"""§3.46 Phase 4 — l3_cascade_cli arg-parsing + helper tests.

The full ``main()`` round-trip needs a real DB session via
``sync_session_factory()`` which isn't available in the test fixture
(same constraint as the Phase 2 jobs file). These tests cover the
deterministic helpers — argument parsing, range expansion — without
hitting the DB.
"""
from __future__ import annotations

from datetime import date

import pytest

from scripts.l3_cascade_cli import (
    _date_range,
    _parse_args,
)


# ---------------------------------------------------------------------------
# _date_range
# ---------------------------------------------------------------------------


def test_date_range_inclusive() -> None:
    assert _date_range(date(2026, 5, 1), date(2026, 5, 3)) == [
        date(2026, 5, 1),
        date(2026, 5, 2),
        date(2026, 5, 3),
    ]


def test_date_range_single_day() -> None:
    assert _date_range(date(2026, 5, 1), date(2026, 5, 1)) == [
        date(2026, 5, 1),
    ]


# ---------------------------------------------------------------------------
# _parse_args — happy paths
# ---------------------------------------------------------------------------


def test_parse_single_tenant_default_period() -> None:
    args = _parse_args(["--tenant-id", "42"])
    assert args.tenant_id == 42
    assert args.all_tenants is False
    assert args.period_start is None
    assert args.from_date is None
    assert args.period_days == 7
    assert args.force is False
    assert args.use_capacity_db is True
    assert args.dry_run is False


def test_parse_single_tenant_with_period() -> None:
    args = _parse_args([
        "--tenant-id", "42",
        "--period-start", "2026-05-04",
        "--config-id", "1",
        "--period-days", "14",
        "--force",
    ])
    assert args.tenant_id == 42
    assert args.config_id == 1
    assert args.period_start == date(2026, 5, 4)
    assert args.period_days == 14
    assert args.force is True


def test_parse_backfill_range() -> None:
    args = _parse_args([
        "--tenant-id", "42",
        "--from-date", "2026-05-01",
        "--to-date", "2026-05-07",
        "--force",
    ])
    assert args.from_date == date(2026, 5, 1)
    assert args.to_date == date(2026, 5, 7)


def test_parse_all_tenants() -> None:
    args = _parse_args(["--all-tenants"])
    assert args.all_tenants is True
    assert args.tenant_id is None


def test_parse_no_capacity_db_flag() -> None:
    args = _parse_args(["--tenant-id", "42", "--no-capacity-db"])
    assert args.use_capacity_db is False


def test_parse_dry_run() -> None:
    args = _parse_args(["--tenant-id", "42", "--dry-run"])
    assert args.dry_run is True


# ---------------------------------------------------------------------------
# _parse_args — error paths
# ---------------------------------------------------------------------------


def test_parse_requires_tenant_or_all() -> None:
    with pytest.raises(SystemExit):
        _parse_args([])


def test_parse_rejects_tenant_id_with_all_tenants() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--tenant-id", "42", "--all-tenants"])


def test_parse_rejects_period_start_with_from_date() -> None:
    with pytest.raises(SystemExit):
        _parse_args([
            "--tenant-id", "42",
            "--period-start", "2026-05-04",
            "--from-date", "2026-05-01",
            "--to-date", "2026-05-07",
        ])


def test_parse_rejects_from_date_without_to_date() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--tenant-id", "42", "--from-date", "2026-05-01"])


def test_parse_rejects_to_date_without_from_date() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--tenant-id", "42", "--to-date", "2026-05-07"])


def test_parse_rejects_inverted_range() -> None:
    with pytest.raises(SystemExit):
        _parse_args([
            "--tenant-id", "42",
            "--from-date", "2026-05-07",
            "--to-date", "2026-05-01",
        ])


def test_parse_rejects_bad_date_format() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--tenant-id", "42", "--period-start", "5/4/2026"])

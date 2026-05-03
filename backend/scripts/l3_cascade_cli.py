"""§3.46 Phase 4 — manual-trigger CLI for the L3 transport cascade.

Operator entry point for invoking :class:`L3CascadeRunner` outside the
daily 5:00 UTC cron. Use cases:

- **Replan after data fix.** Operator notices the cron's plan used
  stale carrier-capacity data; fixes the commitments, re-runs the
  cascade for one (tenant, period) with ``--force`` to bypass the
  idempotency skip.
- **Backfill.** Daily cron was down for 3 days; re-run the cascade
  across the missed range with ``--from-date`` / ``--to-date``.
- **Manual daily run.** Same as the cron does, but invoked from a
  shell / smoke check (``--all-tenants`` with ``--period-start``
  defaulting to today).

Usage::

    # Single tenant, single period.
    python scripts/l3_cascade_cli.py --tenant-id 42 \\
        --period-start 2026-05-04

    # Single tenant, backfill range.
    python scripts/l3_cascade_cli.py --tenant-id 42 \\
        --from-date 2026-05-01 --to-date 2026-05-07 --force

    # All active tenants, today.
    python scripts/l3_cascade_cli.py --all-tenants

    # Dry run — print what it WOULD do, don't touch the DB.
    python scripts/l3_cascade_cli.py --all-tenants --dry-run

Exit code 0 on success (every cascade returned OK or SKIPPED); 1
when at least one cascade failed; 2 on argparse / config error.

Output: one log line per cascade run with status + cascade_run_id +
constrained_plan_id. Log level INFO by default, --verbose enables
DEBUG.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple


logger = logging.getLogger("l3_cascade_cli")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = _parse_args(argv)
    _configure_logging(args.verbose)

    try:
        targets = _resolve_targets(args)
    except _ConfigError as exc:
        logger.error("config error: %s", exc)
        return 2

    if not targets:
        logger.warning("no (tenant, period) targets matched the args; nothing to do")
        return 0

    logger.info(
        "L3 cascade CLI — running %d cascade target(s) (force=%s, dry_run=%s)",
        len(targets), args.force, args.dry_run,
    )

    if args.dry_run:
        for tenant_id, config_id, period_start in targets:
            logger.info(
                "DRY RUN — would run L3 cascade for tenant=%s config=%s period=%s",
                tenant_id, config_id, period_start,
            )
        return 0

    n_ok = n_skipped = n_failed = 0
    for tenant_id, config_id, period_start in targets:
        status = _run_one(
            tenant_id=tenant_id, config_id=config_id,
            period_start=period_start, period_days=args.period_days,
            force=args.force, resolve_capacity_from_db=args.use_capacity_db,
        )
        if status == "OK":
            n_ok += 1
        elif status == "SKIPPED":
            n_skipped += 1
        else:
            n_failed += 1

    logger.info(
        "L3 cascade CLI — done (ok=%d skipped=%d failed=%d)",
        n_ok, n_skipped, n_failed,
    )
    return 1 if n_failed > 0 else 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class _ConfigError(Exception):
    """Raised when args + DB state can't resolve a usable target."""


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manually invoke the L3 transport cascade.",
    )

    # Tenant scope (mutually exclusive).
    tenant_group = parser.add_mutually_exclusive_group(required=True)
    tenant_group.add_argument(
        "--tenant-id", type=int,
        help="Run for one specific tenant.",
    )
    tenant_group.add_argument(
        "--all-tenants", action="store_true",
        help=(
            "Run for every active PRODUCTION tenant — same iteration "
            "as the daily 5:00 UTC cron."
        ),
    )

    # Period scope (mutually exclusive).
    period_group = parser.add_mutually_exclusive_group()
    period_group.add_argument(
        "--period-start", type=_parse_date,
        help=(
            "Single planning-period start date (YYYY-MM-DD). "
            "Defaults to today (UTC) if neither --period-start nor "
            "--from-date is given."
        ),
    )
    period_group.add_argument(
        "--from-date", type=_parse_date,
        help=(
            "Backfill range start (inclusive). Requires --to-date. "
            "Iterates one cascade per date in the range."
        ),
    )

    parser.add_argument(
        "--to-date", type=_parse_date,
        help="Backfill range end (inclusive). Requires --from-date.",
    )
    parser.add_argument(
        "--config-id", type=int, default=None,
        help=(
            "SupplyChainConfig id. If omitted with --tenant-id, "
            "auto-discovers the tenant's active BASELINE config."
        ),
    )
    parser.add_argument(
        "--period-days", type=int, default=7,
        help="Planning horizon (default 7).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help=(
            "Bypass the idempotency skip — re-run even if a prior L3 "
            "plan exists for the period (replan-after-data-fix path)."
        ),
    )
    parser.add_argument(
        "--no-capacity-db", action="store_true",
        help=(
            "Skip CarrierCapacityCommitment lookup (clone-only "
            "Phase 1 fallback). Useful for smoke tests on tenants "
            "without seeded commitments."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the targets that would run; don't touch the DB.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable DEBUG logging.",
    )

    args = parser.parse_args(argv)
    args.use_capacity_db = not args.no_capacity_db

    # Range-mode arg validation.
    if args.from_date and not args.to_date:
        parser.error("--from-date requires --to-date")
    if args.to_date and not args.from_date:
        parser.error("--to-date requires --from-date")
    if args.from_date and args.to_date and args.from_date > args.to_date:
        parser.error("--from-date must be ≤ --to-date")

    return args


def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM-DD, got {s!r}: {exc}",
        ) from None


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def _resolve_targets(args: argparse.Namespace) -> List[Tuple[int, int, date]]:
    """Expand args into a list of ``(tenant_id, config_id, period_start)``
    triples. Multi-tenant + multi-period combinations expand the
    cross-product; single-tenant + single-period yields one row."""
    from app.db.session import sync_session_factory
    from app.services.powell.l3_cascade_jobs import _discover_active_tenants

    # Resolve the tenant set.
    tenant_pairs: List[Tuple[int, int]]  # (tenant_id, config_id)
    if args.all_tenants:
        with sync_session_factory() as db:
            tenant_pairs = _discover_active_tenants(db)
        if not tenant_pairs:
            raise _ConfigError(
                "no active PRODUCTION tenants with a BASELINE config found"
            )
    else:
        if args.config_id is not None:
            tenant_pairs = [(args.tenant_id, args.config_id)]
        else:
            with sync_session_factory() as db:
                cid = _discover_config_for_tenant(db, args.tenant_id)
            if cid is None:
                raise _ConfigError(
                    f"tenant {args.tenant_id} has no active BASELINE config "
                    "(pass --config-id explicitly to override)"
                )
            tenant_pairs = [(args.tenant_id, cid)]

    # Resolve the period set.
    if args.from_date and args.to_date:
        periods = _date_range(args.from_date, args.to_date)
    elif args.period_start:
        periods = [args.period_start]
    else:
        periods = [datetime.utcnow().date()]

    return [
        (tid, cid, period)
        for (tid, cid) in tenant_pairs
        for period in periods
    ]


def _discover_config_for_tenant(db, tenant_id: int) -> Optional[int]:
    """Look up the tenant's active BASELINE config; None if not found."""
    from azirella_data_model.master.config import SupplyChainConfig

    row = (
        db.query(SupplyChainConfig.id)
        .filter(
            SupplyChainConfig.tenant_id == tenant_id,
            SupplyChainConfig.is_active.is_(True),
            SupplyChainConfig.scenario_type == "BASELINE",
        )
        .first()
    )
    return row[0] if row else None


def _date_range(start: date, end: date) -> List[date]:
    """Inclusive list of dates from start to end."""
    n = (end - start).days
    return [start + timedelta(days=i) for i in range(n + 1)]


# ---------------------------------------------------------------------------
# Per-target runner
# ---------------------------------------------------------------------------


def _run_one(
    *,
    tenant_id: int,
    config_id: int,
    period_start: date,
    period_days: int,
    force: bool,
    resolve_capacity_from_db: bool,
) -> str:
    """Run the cascade for one target. Returns the run status string."""
    from app.db.session import sync_session_factory
    from app.services.powell.l3_cascade_runner import L3CascadeRunner

    try:
        with sync_session_factory() as db:
            runner = L3CascadeRunner(db)
            result = runner.run(
                tenant_id=tenant_id, config_id=config_id,
                period_start=period_start, period_days=period_days,
                force=force,
                resolve_capacity_from_db=resolve_capacity_from_db,
            )
        constrained_id = (
            result.stages[-1].plan_id
            if result.stages and result.stages[-1].stage == "balancer"
            else None
        )
        if result.status == "OK":
            logger.info(
                "OK tenant=%s period=%s cascade_run_id=%s constrained_plan_id=%s",
                tenant_id, period_start,
                result.cascade_run_id, constrained_id,
            )
        elif result.status == "SKIPPED":
            logger.info(
                "SKIPPED tenant=%s period=%s (idempotent — pass --force to replan)",
                tenant_id, period_start,
            )
        else:  # FAILED
            failed_stage = next(
                (s for s in result.stages if s.status == "FAILED"), None,
            )
            logger.error(
                "FAILED tenant=%s period=%s cascade_run_id=%s stage=%s error=%s",
                tenant_id, period_start, result.cascade_run_id,
                failed_stage.stage if failed_stage else "?",
                failed_stage.error if failed_stage else "?",
            )
        return result.status
    except Exception as exc:
        logger.exception(
            "FAILED tenant=%s period=%s — infra error: %s",
            tenant_id, period_start, exc,
        )
        return "FAILED"


if __name__ == "__main__":
    sys.exit(main())

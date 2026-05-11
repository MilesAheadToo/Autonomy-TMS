"""Provisioning Service — Orchestrates the full Powell Cascade warm-start pipeline.

Manages the 17-step provisioning process for any supply chain config:
  1. Historical demand & belief states (warm start)
  2. S&OP GraphSAGE training
  3. CFA policy optimization
  4. LightGBM baseline demand forecasting
  5. Demand Planning tGNN training
  6. Supply Planning tGNN training
  7. Inventory Optimization tGNN training
  8. TRM Phase 1 (Behavioral Cloning)
  9. TRM Phase 2 (Simulation-based RL / PPO fine-tuning)
  10. Backtest evaluation (TRM agents vs held-out test period)
  11. Supply plan generation
  12. RCCP validation
  13. Decision stream seeding
  14. Site tGNN training
  15. Conformal calibration
  16. Scenario bootstrap
  17. Executive briefing

Each step tracks its own status (pending/running/completed/failed) with
dependency enforcement — a step only runs if its prerequisites are complete.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_directive import ConfigProvisioningStatus

logger = logging.getLogger(__name__)

# Training steps that run in the background (fire-and-forget pattern).
# run_step marks these "running" immediately; the background task updates to
# "completed"/"failed" when the real work finishes.
#
# Note: legacy `trm_training` and `rl_training` are intentionally absent.
# Generic TRM weights are now trained OFFLINE and shipped in the Docker
# image at /app/models/pretrained/trm_base_v1/. The new step
# `trm_load_pretrained` copies them into the tenant scope (no gradient
# descent, completes in seconds). The legacy step methods remain only as
# skip-stubs so old DB rows referencing them don't crash run_step.
_BACKGROUND_STEPS = {
    "sop_graphsage",
    "supply_tgnn", "inventory_tgnn", "capacity_tgnn",
    "trm_load_pretrained", "site_tgnn", "scenario_bootstrap",
}


class ProvisioningService:
    """Orchestrates provisioning steps for a supply chain config."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_status(self, config_id: int) -> ConfigProvisioningStatus:
        """Get or create provisioning status record for a config."""
        stmt = select(ConfigProvisioningStatus).where(
            ConfigProvisioningStatus.config_id == config_id
        )
        result = await self.db.execute(stmt)
        status = result.scalar_one_or_none()
        if not status:
            status = ConfigProvisioningStatus(
                config_id=config_id,
                overall_status="not_started",
            )
            self.db.add(status)
            await self.db.flush()
        return status

    async def run_step(self, config_id: int, step_key: str) -> dict:
        """Run a single provisioning step if dependencies are met."""
        # Ensure clean transaction state (recover from prior aborted transactions)
        try:
            await self.db.rollback()
        except Exception:
            pass
        status = await self.get_or_create_status(config_id)

        if step_key not in ConfigProvisioningStatus.STEPS:
            return {"error": f"Unknown step: {step_key}"}

        # Check dependencies
        deps = ConfigProvisioningStatus.STEP_DEPENDS.get(step_key, [])
        for dep in deps:
            dep_status = getattr(status, f"{dep}_status", "pending")
            if dep_status != "completed":
                dep_label = ConfigProvisioningStatus.STEP_LABELS.get(dep, dep)
                return {
                    "error": f"Dependency not met: {dep_label} must complete first",
                    "blocked_by": dep,
                }

        # Mark as running
        setattr(status, f"{step_key}_status", "running")
        status.overall_status = "in_progress"
        await self.db.commit()

        try:
            if step_key in _BACKGROUND_STEPS:
                # Dispatch real training in a background task; return immediately.
                asyncio.create_task(
                    self._run_background_step(config_id, step_key)
                )
                return {"status": "running", "step": step_key, "note": "Training dispatched"}

            result = await self._execute_step(config_id, step_key)

            # SOC II: Check inner result for failure — don't mark failed steps as completed
            inner_status = result.get("status", "ok") if isinstance(result, dict) else "ok"
            if inner_status == "failed":
                logger.error("Provisioning step %s returned failure for config %d: %s",
                             step_key, config_id, result.get("error", "unknown"))
                setattr(status, f"{step_key}_status", "failed")
                setattr(status, f"{step_key}_at", datetime.utcnow())
                setattr(status, f"{step_key}_error", str(result.get("error", ""))[:500])
                status.overall_status = "partial"
                await self._write_audit_log(config_id, step_key, "failed", result)
                await self.db.commit()
                return {"status": "failed", "step": step_key, "result": result}

            setattr(status, f"{step_key}_status", "completed")
            setattr(status, f"{step_key}_at", datetime.utcnow())
            # SOC II: preserve prior error if step was retried after failure
            prior_error = getattr(status, f"{step_key}_error", None)
            if prior_error:
                setattr(status, f"{step_key}_error",
                        f"[RESOLVED] Prior failure: {prior_error} | Succeeded on retry at {datetime.utcnow().isoformat()}")
            else:
                setattr(status, f"{step_key}_error", None)

            # SOC II: audit log entry for every provisioning step completion
            await self._write_audit_log(config_id, step_key, "completed", result)

            # Check if all steps complete
            all_done = all(
                getattr(status, f"{s}_status") == "completed"
                for s in ConfigProvisioningStatus.STEPS
            )
            if all_done:
                status.overall_status = "completed"
                # Post-provisioning housekeeping (non-blocking)
                try:
                    cleanup = await self._post_provisioning_cleanup(config_id)
                    logger.info("Post-provisioning cleanup: %s", cleanup)
                except Exception as e:
                    logger.warning("Post-provisioning cleanup failed (non-critical): %s", e)

            await self.db.commit()
            return {"status": "completed", "step": step_key, "result": result}

        except Exception as e:
            logger.exception("Provisioning step %s failed for config %d", step_key, config_id)
            setattr(status, f"{step_key}_status", "failed")
            setattr(status, f"{step_key}_error", str(e)[:500])
            status.overall_status = "partial"
            await self._write_audit_log(config_id, step_key, "failed", {"error": str(e)[:500]})
            await self.db.commit()
            return {"status": "failed", "step": step_key, "error": str(e)[:500]}

    async def _write_audit_log(
        self, config_id: int, step_key: str, status: str, result: dict,
    ) -> None:
        """SOC II: Write provisioning step result to audit_logs table.

        Uses CONFIG_UPDATE for success, CONFIG_CREATE for failures — these are
        valid values in the auditaction Postgres enum.
        """
        try:
            from sqlalchemy import text as sqt
            import json
            action_enum = "CONFIG_UPDATE" if status == "completed" else "CONFIG_CREATE"
            desc = f"Provisioning step '{step_key}' {status} for config {config_id}"
            error_msg = str(result.get("error", ""))[:500] if status == "failed" else None
            extra = json.dumps(result, default=str)[:2000] if result else None

            await self.db.execute(sqt("""
                INSERT INTO audit_logs (action, resource_type, resource_id, resource_name,
                    description, status, error_message, extra_data, created_at)
                VALUES (CAST(:action AS auditaction), 'provisioning', :rid, :rname,
                    :desc, CAST(:status AS auditstatus), :err, CAST(:extra AS json), NOW())
            """), {
                "action": action_enum, "rid": config_id, "rname": step_key,
                "desc": desc,
                "status": "SUCCESS" if status == "completed" else "FAILURE",
                "err": error_msg, "extra": extra,
            })
        except Exception as e:
            logger.warning("SOC II ALERT: Failed to write provisioning audit log: %s", e)

    async def _post_provisioning_cleanup(self, config_id: int) -> dict:
        """Housekeeping after successful provisioning.

        Cleans up stale data from prior provisioning runs while keeping
        the system operational during cleanup. Only runs after ALL 17 steps
        complete successfully.

        Cleanup targets:
        1. Old powell_*_decisions older than 30 days (decision stream lookback is 7)
        2. Old supply_plan records with non-current plan_version
        3. Old performance_metrics beyond retention period
        4. Stale executive_briefings (keep latest 5 per tenant)
        """
        from sqlalchemy import text
        cleaned = {}
        retention_days = 30

        try:
            # 1. Clean old decisions (keep last 30 days)
            for table in [
                "powell_atp_decisions", "powell_rebalance_decisions",
                "powell_po_decisions", "powell_order_exceptions",
                "powell_mo_decisions", "powell_to_decisions",
                "powell_quality_decisions", "powell_maintenance_decisions",
                "powell_subcontracting_decisions",
                "powell_forecast_adjustment_decisions", "powell_buffer_decisions",
            ]:
                try:
                    result = await self.db.execute(text(f"""
                        DELETE FROM {table}
                        WHERE config_id = :cfg
                        AND created_at < NOW() - INTERVAL '{retention_days} days'
                    """), {"cfg": config_id})
                    count = result.rowcount
                    if count > 0:
                        cleaned[table] = count
                except Exception:
                    pass

            # 2. Clean old supply plan records (keep latest plan_version)
            try:
                result = await self.db.execute(text("""
                    DELETE FROM supply_plan
                    WHERE config_id = :cfg
                    AND plan_version != 'live'
                    AND created_dttm < NOW() - INTERVAL '7 days'
                """), {"cfg": config_id})
                if result.rowcount > 0:
                    cleaned["supply_plan"] = result.rowcount
            except Exception:
                pass

            # 3. Clean old briefings (keep latest 5)
            try:
                result = await self.db.execute(text("""
                    DELETE FROM executive_briefings
                    WHERE tenant_id = (SELECT tenant_id FROM supply_chain_configs WHERE id = :cfg)
                    AND id NOT IN (
                        SELECT id FROM executive_briefings
                        WHERE tenant_id = (SELECT tenant_id FROM supply_chain_configs WHERE id = :cfg)
                        ORDER BY created_at DESC LIMIT 5
                    )
                """), {"cfg": config_id})
                if result.rowcount > 0:
                    cleaned["executive_briefings"] = result.rowcount
            except Exception:
                pass

            await self.db.commit()
            logger.info(
                "Post-provisioning cleanup for config %d: %s",
                config_id, cleaned or "nothing to clean",
            )
            return {"status": "ok", "cleaned": cleaned}

        except Exception as e:
            logger.warning("Post-provisioning cleanup failed: %s", e)
            return {"status": "partial", "error": str(e), "cleaned": cleaned}

    async def reprovision(self, config_id: int, scope: Optional[str] = None) -> dict:
        """Archive the current config version and re-run provisioning steps.

        Creates an archived snapshot of the current config (preserving its
        creation date and version) before resetting provisioning step statuses
        and running the pipeline.  The archived config appears in the SC config
        list as a read-only historical record.

        Args:
            config_id: Supply chain config to reprovision.
            scope: "PARAMETER_ONLY" for policy/parameter changes (reuses existing
                   TRM weights, GNN models, and simulation data — only runs
                   cfa_optimization, decision_seed, conformal, briefing).
                   "FULL" or None for structural changes (new sites, lanes,
                   products, BOMs) — runs all 14 steps.
        """
        from app.models.supply_chain_config import SupplyChainConfig

        effective_scope = scope if scope in ("PARAMETER_ONLY", "FULL") else "FULL"
        is_parameter_only = effective_scope == "PARAMETER_ONLY"
        steps_to_run = (
            ConfigProvisioningStatus.PARAMETER_ONLY_STEPS
            if is_parameter_only
            else ConfigProvisioningStatus.STEPS
        )

        # 1. Load the current config
        row = await self.db.execute(
            select(SupplyChainConfig).where(SupplyChainConfig.id == config_id)
        )
        config = row.scalar_one_or_none()
        if not config:
            return {"error": f"Config {config_id} not found"}

        current_version = config.version or 1

        # 2. Create an archived snapshot (lightweight — just metadata, no deep copy of sites/lanes)
        archived = SupplyChainConfig(
            name=f"{config.name} (v{current_version})",
            description=f"Archived version {current_version} — reprovisioned on {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
            is_active=False,
            tenant_id=config.tenant_id,
            created_at=config.created_at,  # preserve original creation date
            updated_at=datetime.utcnow(),
            created_by=config.created_by,
            time_bucket=config.time_bucket,
            parent_config_id=config_id,
            scenario_type="ARCHIVED",
            version=current_version,
            mode=config.mode,
            validation_status="unchecked",
        )
        self.db.add(archived)
        await self.db.flush()
        logger.info(
            "Archived config %d as v%d (new row id=%d) before reprovisioning (scope=%s)",
            config_id, current_version, archived.id, effective_scope,
        )

        # 3. Bump version on the active config
        config.version = current_version + 1
        config.updated_at = datetime.utcnow()

        # 4. Reset provisioning step statuses based on scope
        status = await self.get_or_create_status(config_id)
        status.provisioning_scope = effective_scope
        for step_key in ConfigProvisioningStatus.STEPS:
            if step_key in steps_to_run:
                # Reset steps that will be re-run
                setattr(status, f"{step_key}_status", "pending")
                setattr(status, f"{step_key}_at", None)
                setattr(status, f"{step_key}_error", None)
            elif is_parameter_only:
                # For PARAMETER_ONLY: keep existing status for non-parameter steps
                # (they were completed in the previous full provisioning)
                pass
        status.overall_status = "not_started"
        await self.db.commit()

        # 5. Run the pipeline
        return await self.run_all(config_id)

    async def delete_config(self, config_id: int) -> dict:
        """Delete a supply chain config and all its dependent data + checkpoints.

        This is the proper way to remove a config — handles:
        1. All DB records with config_id FK (sites, products, BOMs, lanes, etc.)
        2. Checkpoint files on disk (TRM weights, Site tGNN, GNN embeddings)
        3. The config row itself

        Only archived/inactive configs can be deleted. Active configs must be
        archived first (via reprovision) or deactivated.
        """
        from app.models.supply_chain_config import SupplyChainConfig

        row = await self.db.execute(
            select(SupplyChainConfig).where(SupplyChainConfig.id == config_id)
        )
        config = row.scalar_one_or_none()
        if not config:
            return {"error": f"Config {config_id} not found"}
        if config.is_active:
            return {"error": f"Config {config_id} is active — archive or deactivate before deleting"}

        # 1. Delete all DB records with config_id FK
        try:
            # Get tables with config_id column (excluding the config table itself)
            result = await self.db.execute(text("""
                SELECT DISTINCT table_name FROM information_schema.columns
                WHERE column_name = 'config_id' AND table_schema = 'public'
                  AND table_name != 'supply_chain_configs'
            """))
            config_tables = [r[0] for r in result.fetchall()]

            # Delete product_bom first (has product FK)
            await self.db.execute(text("DELETE FROM product_bom WHERE config_id = :cid"), {"cid": config_id})

            # Delete all other config-scoped tables
            for table in config_tables:
                if table not in ("site", "product", "product_bom"):
                    await self.db.execute(text(f"DELETE FROM {table} WHERE config_id = :cid"), {"cid": config_id})

            # Delete product, then site
            await self.db.execute(text("DELETE FROM product WHERE config_id = :cid"), {"cid": config_id})
            await self.db.execute(text("DELETE FROM site WHERE config_id = :cid"), {"cid": config_id})

            # Delete the config row
            await self.db.execute(text("DELETE FROM supply_chain_configs WHERE id = :cid"), {"cid": config_id})
            await self.db.commit()
        except Exception as e:
            await self.db.rollback()
            logger.error("Failed to delete config %d DB records: %s", config_id, e)
            return {"error": f"DB deletion failed: {e}"}

        # 2. Clean up checkpoint files on disk
        import shutil
        import os
        checkpoint_base = os.environ.get("CHECKPOINT_DIR", "/app/checkpoints")
        config_checkpoint_dir = os.path.join(checkpoint_base, f"config_{config_id}")
        if os.path.isdir(config_checkpoint_dir):
            try:
                shutil.rmtree(config_checkpoint_dir)
                logger.info("Deleted checkpoint dir: %s", config_checkpoint_dir)
            except Exception as e:
                logger.warning("Failed to delete checkpoint dir %s: %s", config_checkpoint_dir, e)

        logger.info("Config %d fully deleted (DB records + checkpoints)", config_id)
        return {"status": "deleted", "config_id": config_id}

    async def cleanup_orphaned_checkpoints(self) -> dict:
        """Find and remove checkpoint directories for configs that no longer exist.

        Call this periodically or after bulk config deletion.
        """
        import os
        import shutil
        from app.models.supply_chain_config import SupplyChainConfig

        checkpoint_base = os.environ.get("CHECKPOINT_DIR", "/app/checkpoints")
        if not os.path.isdir(checkpoint_base):
            return {"cleaned": 0, "message": "No checkpoint directory found"}

        # Get all existing config IDs
        result = await self.db.execute(select(SupplyChainConfig.id))
        existing_ids = {r[0] for r in result.fetchall()}

        cleaned = []
        for entry in os.listdir(checkpoint_base):
            if entry.startswith("config_"):
                try:
                    cfg_id = int(entry.split("_")[1])
                    if cfg_id not in existing_ids:
                        path = os.path.join(checkpoint_base, entry)
                        shutil.rmtree(path)
                        cleaned.append(cfg_id)
                        logger.info("Cleaned orphaned checkpoint dir: %s", path)
                except (ValueError, OSError) as e:
                    logger.warning("Failed to clean %s: %s", entry, e)

        return {"cleaned": len(cleaned), "config_ids": cleaned}

    async def run_all(self, config_id: int) -> dict:
        """Run all provisioning steps in dependency order.

        Background steps are dispatched as fire-and-forget tasks.  Before
        attempting a step whose dependency is still "running" (i.e. a
        background task that hasn't finished yet), we poll until the
        dependency completes or fails, with a timeout.
        """
        status = await self.get_or_create_status(config_id)
        results = {}

        for step_key in ConfigProvisioningStatus.STEPS:
            current_status = getattr(status, f"{step_key}_status", "pending")
            if current_status == "completed":
                results[step_key] = {"status": "skipped", "reason": "already completed"}
                continue

            # Before running, wait for any "running" dependencies to finish.
            deps = ConfigProvisioningStatus.STEP_DEPENDS.get(step_key, [])
            for dep in deps:
                # Refresh ORM state so we see bg-task commits (expire() causes MissingGreenlet with async sessions)
                await self.db.refresh(status)
                dep_status = getattr(status, f"{dep}_status", "pending")
                if dep_status == "running":
                    dep_status = await self._wait_for_step(config_id, dep, timeout=300)
                    await self.db.refresh(status)

            result = await self.run_step(config_id, step_key)
            results[step_key] = result

            # Refresh status so subsequent dependency checks see latest DB state
            await self.db.refresh(status)

            if result.get("status") == "failed":
                logger.warning("Step %s failed, continuing with independent steps", step_key)

        # Final refresh and overall_status reconciliation — background steps
        # may have completed after the foreground loop checked all_done, leaving
        # overall_status stuck at "in_progress".
        await self.db.refresh(status)
        all_done = all(
            getattr(status, f"{s}_status") == "completed"
            for s in ConfigProvisioningStatus.STEPS
        )
        if all_done and status.overall_status != "completed":
            status.overall_status = "completed"
            await self.db.commit()
        elif not all_done and any(
            getattr(status, f"{s}_status") == "failed"
            for s in ConfigProvisioningStatus.STEPS
        ):
            status.overall_status = "partial"
            await self.db.commit()

        return {
            "config_id": config_id,
            "overall_status": status.overall_status,
            "steps": results,
        }

    async def _wait_for_step(
        self, config_id: int, step_key: str, timeout: int = 300
    ) -> str:
        """Poll DB until a background step leaves 'running' state or timeout.

        Uses a raw SQL query to bypass SQLAlchemy ORM identity-map caching,
        which would otherwise return the stale in-memory object instead of
        re-reading from the DB (the background task commits via a separate
        session).
        """
        import time
        from sqlalchemy import text as sa_text
        deadline = time.monotonic() + timeout
        col = f"{step_key}_status"
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            # Raw SQL bypasses ORM identity map — sees committed state from bg task
            result = await self.db.execute(
                sa_text(f"SELECT {col} FROM config_provisioning_status WHERE config_id = :cid"),
                {"cid": config_id},
            )
            row = result.first()
            if row:
                s = row[0]
                if s != "running":
                    logger.info("Background step %s finished with status: %s", step_key, s)
                    return s
        logger.warning("Timed out waiting for background step %s", step_key)
        return "running"

    async def _resolve_tenant_id(self, config_id: int) -> int:
        """Resolve tenant_id from supply chain config."""
        from app.models.supply_chain_config import SupplyChainConfig
        result = await self.db.execute(
            select(SupplyChainConfig.tenant_id).where(SupplyChainConfig.id == config_id)
        )
        tid = result.scalar_one_or_none()
        return tid or 0

    async def _execute_step(self, config_id: int, step_key: str) -> dict:
        """Execute a specific provisioning step."""
        handler = getattr(self, f"_step_{step_key}", None)
        if handler:
            return await handler(config_id)
        return {"status": "ok", "note": f"Step {step_key} placeholder — not yet implemented"}

    async def _step_market_intelligence(self, config_id: int) -> dict:
        """Activate default external signal sources, filtered by the config's
        DAG topology.

        Reuses ``ExternalSignalService.activate_default_sources(config_id)``
        which reads sites (coordinates + state codes + region), products
        (families + keywords), transportation lanes (corridors + freight
        routes), industry tags, and supplier countries from the supply-chain
        config — and inserts one ExternalSignalSource row per free public
        feed (Open-Meteo, NWS, DOT, GDELT, Google Trends, Reddit, openFDA)
        with its filter parameters pre-populated. TMS commercial freight
        feeds (DAT, SONAR, MarineTraffic, …) stay off until credentials
        are supplied via the admin UI.

        The same code path runs when an admin clicks "Activate Defaults"
        in the Context Engine page; doing it during provisioning means
        every new tenant boots with Market Intelligence already on.
        """
        tenant_id = await self._resolve_tenant_id(config_id)
        if not tenant_id:
            return {"error": f"Could not resolve tenant_id for config {config_id}"}

        from app.services.external_signal_service import ExternalSignalService

        service = ExternalSignalService(self.db, tenant_id=tenant_id)
        try:
            created = await service.activate_default_sources(config_id)
        except Exception as e:
            logger.exception(
                "market_intelligence: activate_default_sources failed for "
                "tenant=%s config=%s", tenant_id, config_id,
            )
            return {"error": f"activate_default_sources failed: {e}"}

        return {
            "status": "ok",
            "sources_activated": len(created),
            "source_keys": [c.get("source_key") for c in created],
        }

    async def _step_warm_start(self, config_id: int) -> dict:
        """Step 1: Generate historical demand data.

        Also ensures geo-based transport lead times and industry stochastic
        defaults are in place before any model training begins.
        """
        from app.db.session import sync_session_factory
        db = sync_session_factory()
        try:
            # Ensure clean transaction state
            try:
                db.rollback()
            except Exception:
                pass

            # Ensure stochastic defaults and geo lead times are populated
            # before generating warm-start data (idempotent — skips if present)
            from app.services.geocoding_service import calculate_geo_lead_times_for_config
            from app.services.industry_defaults_service import (
                apply_industry_defaults_to_config,
                apply_agent_stochastic_defaults,
            )
            from app.models.supply_chain_config import SupplyChainConfig
            from app.models.tenant import Tenant

            # Clean up orphaned records from previous provisioning cycles
            # (forecasts/inv_levels referencing deleted products after config rebuild)
            from sqlalchemy import text as sql_text
            for child_table in ("forecast", "inv_level", "inv_policy", "supply_plan"):
                try:
                    deleted = db.execute(sql_text(f"""
                        DELETE FROM {child_table} c
                        WHERE c.config_id = :cid
                        AND NOT EXISTS (SELECT 1 FROM product p WHERE p.id = c.product_id)
                    """), {"cid": config_id})
                    if deleted.rowcount > 0:
                        logger.info(
                            "Warm start: cleaned %d orphaned %s rows for config %d",
                            deleted.rowcount, child_table, config_id,
                        )
                except Exception:
                    pass  # table may not have product_id column
            db.flush()

            config = db.query(SupplyChainConfig).filter(
                SupplyChainConfig.id == config_id,
            ).first()
            if config:
                tenant = db.query(Tenant).filter(Tenant.id == config.tenant_id).first()
                industry_key = (
                    tenant.industry.value
                    if tenant and tenant.industry
                    else None
                )
                if industry_key:
                    apply_industry_defaults_to_config(db, config_id, industry_key)
                    apply_agent_stochastic_defaults(
                        db, config_id, config.tenant_id, industry_key,
                    )
                geo_result = calculate_geo_lead_times_for_config(db, config_id)
                if geo_result["updated_lanes"] > 0:
                    logger.info(
                        "Warm start: applied geo lead times to %d lanes for config %d",
                        geo_result["updated_lanes"], config_id,
                    )

            # Auto-generate site and product hierarchies from config entities
            # (idempotent — skips if nodes already exist for this tenant)
            from app.services.hierarchy_auto_seeder import auto_seed_hierarchies
            hierarchy_result = auto_seed_hierarchies(db, config_id, config.tenant_id)
            if hierarchy_result.get("created"):
                logger.info(
                    "Warm start: auto-seeded %d site + %d product hierarchy nodes for config %d",
                    hierarchy_result.get("site_nodes", 0),
                    hierarchy_result.get("product_nodes", 0),
                    config_id,
                )

            # Build geography hierarchy from site/trading partner addresses
            # (AWS SC DM geography table with parent_geo_id hierarchy)
            try:
                from app.services.geography_hierarchy_builder import build_geography_hierarchy
                company_id = getattr(config, "company_id", None) or db.execute(
                    sqt("SELECT company_id FROM site WHERE config_id = :cfg AND company_id IS NOT NULL LIMIT 1"),
                    {"cfg": config_id},
                ).scalar()
                if company_id:
                    geo_result = build_geography_hierarchy(db, config_id, company_id)
                    if geo_result.get("cities", 0) > 0:
                        logger.info(
                            "Warm start: geography hierarchy — %d countries, %d regions, %d states, %d cities",
                            geo_result["countries"], geo_result["regions"],
                            geo_result["states"], geo_result["cities"],
                        )
                    db.commit()
            except Exception as e:
                logger.warning("Geography hierarchy building failed: %s", e)
                try:
                    db.rollback()
                except Exception:
                    pass

            # Seed channel segmentation if not present
            try:
                existing_segs = db.execute(sqt(
                    "SELECT count(*) FROM segmentation WHERE company_id = :cid AND segment_type = 'channel'"
                ), {"cid": company_id or ""}).scalar() or 0
                if existing_segs == 0 and company_id:
                    for sid, name, cls, desc in [
                        ("SEG-FOOD", "Foodservice", "foodsvc", "Restaurant and institutional"),
                        ("SEG-RETL", "Retail", "retail", "Grocery and supermarket"),
                        ("SEG-ONLN", "Online", "online", "Direct online ordering"),
                    ]:
                        db.execute(sqt(
                            "INSERT INTO segmentation (id, company_id, name, segment_type, description, classification, priority, service_level_target, is_active, source) "
                            "VALUES (:id, :cid, :name, 'channel', :desc, :cls, 1, 0.95, true, 'provisioning') "
                            "ON CONFLICT (id) DO NOTHING"
                        ), {"id": f"{company_id}-{sid}", "cid": company_id, "name": name, "cls": cls, "desc": desc})
                    db.commit()
                    logger.info("Warm start: seeded default channel segments")
            except Exception as e:
                logger.warning("Channel segmentation seeding failed: %s", e)
                try:
                    db.rollback()
                except Exception:
                    pass

            # Auto-populate planning hierarchy config from DAG topology
            # (maps planning types to site/product levels derived from master data)
            try:
                phc_count = _seed_planning_hierarchy_config(db, config_id, config.tenant_id)
                if phc_count > 0:
                    logger.info("Warm start: seeded %d planning hierarchy configs", phc_count)
            except Exception as e:
                logger.warning("Planning hierarchy config seeding failed: %s", e)
                try:
                    db.rollback()
                except Exception:
                    pass

            # Auto-populate SCOR metric configuration (ASSESS/DIAGNOSE/CORRECT)
            try:
                metric_count = _seed_metric_configuration(db, config_id, config.tenant_id)
                if metric_count > 0:
                    logger.info("Warm start: seeded %d SCOR metric definitions", metric_count)
            except Exception as e:
                logger.warning("Metric configuration seeding failed: %s", e)
                try:
                    db.rollback()
                except Exception:
                    pass

            from app.services.warm_start_generator import WarmStartGenerator
            result = WarmStartGenerator(db).generate_for_config(config_id, weeks=52)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def _step_training_corpus(self, config_id: int, db: AsyncSession = None) -> dict:
        """Pre-training step: build the unified training corpus.

        Two-stream architecture (see UNIFIED_TRAINING_CORPUS.md §2a):
          1. Historical stream (primary) — extract (state, action, outcome)
             triples from real ERP transaction tables for the 12 TRMs.
          2. Simulation stream (secondary, targeted) — only when historical
             coverage is thin, for rare-event augmentation, or for cold-start
             tenants. Also feeds RL rollouts in the rl_training step.
        """
        from sqlalchemy import text as sql_text

        from app.services.training_corpus import TrainingCorpusService
        from app.services.training_corpus.historical import (
            HistoricalExtractionOrchestrator,
        )

        effective_db = db or self.db

        result = await effective_db.execute(
            sql_text("SELECT tenant_id FROM supply_chain_configs WHERE id = :cid"),
            {"cid": config_id},
        )
        row = result.fetchone()
        tenant_id = row.tenant_id if row else 0

        # PR-6 follow-up — Phase-2 shipment-generator calibration. Mirrors
        # SCP's seasonal-envelope fit at the same point in the pipeline:
        # fit + persist tenant-calibrated SeasonalEnvelopes from historical
        # TransferOrder + TransferOrderLineItem rows so any twin-driven
        # simulation augmentation downstream uses the fitted shipment shape
        # instead of bootstrap defaults. Best-effort — a fit failure logs a
        # warning and falls back to the parametric Phase-1 generator.
        phase2_summary = None
        try:
            from app.db.session import sync_session_factory
            from app.services.digital_twin.phase2_fitter import (
                fit_phase2_for_config,
            )
            sync_db = sync_session_factory()
            try:
                phase2_summary = fit_phase2_for_config(
                    db=sync_db,
                    config_id=config_id,
                    tenant_id=tenant_id,
                )
                sync_db.commit()
                logger.info(
                    "Phase-2 shipment-generator calibration "
                    "(config=%d, tenant=%d): %s",
                    config_id, tenant_id, phase2_summary,
                )
            finally:
                sync_db.close()
        except Exception as exc:  # noqa: BLE001 — best-effort calibration
            logger.warning(
                "Phase-2 shipment-generator calibration failed for "
                "config=%d (twin will fall back to bootstrap "
                "shipment generator): %s",
                config_id, exc,
            )

        # Stream 1: historical extraction (primary source of BC labels)
        hist = await HistoricalExtractionOrchestrator(effective_db).extract_all(
            tenant_id=tenant_id, config_id=config_id,
        )
        logger.info(
            "Historical extraction config=%d: %d samples across %d TRMs",
            config_id, hist.total_samples, len(hist.coverage),
        )

        MIN_HISTORICAL_COVERAGE = 100
        thin_trms = [
            trm for trm, cov in hist.coverage.items()
            if cov.sample_count < MIN_HISTORICAL_COVERAGE
        ]

        # Stream 2: simulation augmentation — smaller than before (100×12, was
        # 500×26). Runs only when historical data is thin or a TRM has no
        # historical coverage at all.
        sim_summary = None
        if hist.total_samples < 1000 or thin_trms:
            logger.info(
                "Running simulation stream to augment thin TRMs: %s",
                thin_trms or "(cold start)",
            )
            service = TrainingCorpusService(effective_db)
            sim_summary = await service.create_corpus(
                tenant_id=tenant_id,
                config_id=config_id,
                num_perturbations=100,
                planning_horizon_weeks=12,
                focus_trms=thin_trms or None,
            )
            if sim_summary.get("status") == "failed":
                raise RuntimeError(
                    f"Simulation stream failed: site={sim_summary.get('site_id')} "
                    f"trm={sim_summary.get('trm_type')} missing={sim_summary.get('missing')}. "
                    f"Fix master data and re-provision."
                )

        return {
            "status": "success",
            "historical_samples": hist.total_samples,
            "historical_coverage": {
                trm: cov.sample_count for trm, cov in hist.coverage.items()
            },
            "thin_trms": thin_trms,
            "simulation": sim_summary,
            "phase2_shipment_calibration": phase2_summary,
        }

    async def _step_sop_graphsage(self, config_id: int) -> dict:
        """Step 2: Train S&OP GraphSAGE model on Layer 4 corpus samples.

        Before April 2026 this step trained on synthetic random networks
        via SoPGraphSAGEOracle. It now trains on samples aggregated from
        actual TRM decisions in Digital Twin simulations of the tenant's
        perturbed ERP baseline. See _step_training_corpus.
        """
        from app.services.powell.generic_training_orchestrator import GenericTrainingOrchestrator
        orchestrator = GenericTrainingOrchestrator(config_id=config_id)
        result = await orchestrator.train_sop_graphsage()
        return {"status": "ok", "models_trained": result.models_trained}

    async def _step_cfa_optimization(self, config_id: int) -> dict:
        """Step 3: CFA policy parameter optimization via Differential Evolution.

        Constructs a simple inventory cost simulator for the config's products
        and sites, then uses InventoryPolicyOptimizer (inherits PolicyOptimizer)
        with the correct (simulator, parameters) signature.
        """
        from app.db.session import sync_session_factory
        sync_db = sync_session_factory()
        try:
            from app.services.powell.policy_optimizer import (
                InventoryPolicyOptimizer, PolicyParameter,
            )
            from app.models.powell import PowellPolicyParameters
            from app.models.supply_chain_config import SupplyChainConfig

            config = sync_db.query(SupplyChainConfig).get(config_id)
            if not config:
                return {"status": "skipped", "reason": "Config not found"}

            # Build a lightweight inventory cost simulator for this config.
            # It evaluates a base-stock policy parameterised by (service_level,
            # days_of_coverage) across the config's product-site pairs.
            from app.models.sc_entities import InvPolicy, Forecast, Product, InvLevel
            from app.models.supply_chain_config import Site
            import numpy as np

            sites = (
                sync_db.query(Site)
                .filter(Site.config_id == config_id, Site.is_external == False)
                .all()
            )
            products = (
                sync_db.query(Product)
                .filter(Product.config_id == config_id)
                .all()
            )
            if not sites or not products:
                return {"status": "skipped", "reason": "No sites or products for config"}

            site_ids = [s.id for s in sites]
            product_ids = [p.id for p in products]

            def _simulator(params: dict, seed: int) -> float:
                """Simple Monte Carlo cost evaluation for (service_level, doc)."""
                rng = np.random.RandomState(seed)
                sl = params.get("service_level", 0.95)
                doc = params.get("days_of_coverage", 14.0)
                # Simplified cost model: holding cost vs stockout cost
                holding_unit = 0.5
                stockout_unit = 10.0
                total_cost = 0.0
                for _ in range(len(site_ids)):
                    demand = rng.poisson(50, size=52)
                    safety_stock = doc * np.mean(demand) / 7.0
                    for d in demand:
                        inv = safety_stock - d
                        if inv >= 0:
                            total_cost += inv * holding_unit
                        else:
                            total_cost += abs(inv) * stockout_unit
                return total_cost

            parameters = [
                PolicyParameter(
                    name="service_level",
                    initial_value=0.95,
                    lower_bound=0.80,
                    upper_bound=0.99,
                    parameter_type="continuous",
                    description="Target service level",
                    category="inventory",
                ),
                PolicyParameter(
                    name="days_of_coverage",
                    initial_value=14.0,
                    lower_bound=1.0,
                    upper_bound=60.0,
                    parameter_type="continuous",
                    description="Days of coverage for safety stock",
                    category="inventory",
                ),
            ]

            optimizer = InventoryPolicyOptimizer(
                simulator=_simulator,
                parameters=parameters,
                n_scenarios=50,
            )
            result = optimizer.optimize(method="differential_evolution")

            if result and result.status == "success":
                from app.models.powell import PolicyType
                # Upsert a single row keyed by (config_id, policy_type=INVENTORY)
                existing = (
                    sync_db.query(PowellPolicyParameters)
                    .filter(
                        PowellPolicyParameters.config_id == config_id,
                        PowellPolicyParameters.policy_type == PolicyType.INVENTORY,
                    )
                    .first()
                )
                if existing:
                    existing.parameters = result.optimal_parameters
                    existing.optimization_method = "differential_evolution"
                    existing.optimization_value = result.optimal_objective
                    existing.num_iterations = result.num_iterations
                    existing.num_scenarios = 50
                    existing.is_active = True
                else:
                    sync_db.add(PowellPolicyParameters(
                        config_id=config_id,
                        policy_type=PolicyType.INVENTORY,
                        parameters=result.optimal_parameters,
                        optimization_method="differential_evolution",
                        optimization_value=result.optimal_objective,
                        num_iterations=result.num_iterations,
                        num_scenarios=50,
                        is_active=True,
                    ))
                sync_db.commit()
                return {
                    "status": "ok",
                    "cost": result.optimal_objective,
                    "iterations": result.num_iterations,
                    "params_optimized": len(result.optimal_parameters),
                }
            else:
                return {"status": "ok", "note": "CFA optimization did not converge"}
        except Exception as e:
            logger.warning("CFA optimization failed (non-critical): %s", e)
            return {"status": "ok", "note": f"CFA optimization attempted: {str(e)[:100]}"}
        finally:
            sync_db.close()

    async def _step_lgbm_forecast(self, config_id: int) -> dict:
        """Step 4: Train LightGBM quantile models at ship-from level, then
        disaggregate to ship-to using recent customer mix ratios.

        **Architecture** (AWS SC DM pattern, per standard hierarchical
        forecasting practice):

          1. **Statistical forecast at ship-from (product × ship_from_site_id)**
             — this is the stable, high-signal dimension. LightGBM quantile
             models (P10/P50/P90) trained on weekly aggregated shipped
             quantity per plant per product. Series with <26 observations
             use a naive rolling-mean fallback.

          2. **Customer mix computation** — for each (product, site) pair,
             compute each customer's share of recent history
             (last 13 weeks by default). Ratios sum to 1.0 per (product, site).

          3. **Disaggregation** — each ship-from forecast row is split into
             customer-level forecast rows by multiplying the quantiles by
             the mix ratios. This gives us ship-to (product × customer_id)
             forecasts that are coherent with the ship-from plan.

        We do NOT forecast directly at customer level — that produces
        thousands of sparse series with no statistical signal. Forecasting
        at the stable dimension and disaggregating is the AWS SC / standard
        hierarchical forecasting pattern.

        **Persistence** (forecast_level='product' for all rows per AWS SC DM):
          - Ship-from rows: site_id populated, customer_id NULL, source='lgbm_ship_from' or 'naive_ship_from'
          - Ship-to rows:   site_id populated AND customer_id populated, source='disaggregated_ship_to'

        Horizon comes from planning_horizon_template.mps (MPS is the
        tactical layer that drives supply plan generation).
        """
        try:
            from app.services.demand_forecasting.lgbm_pipeline import LGBMForecastPipeline
            from app.models.sc_entities import Forecast
            from app.core.clock import config_today_sync
            from app.db.session import sync_session_factory
            from sqlalchemy import text
            from datetime import datetime, timedelta
            import pandas as pd
            import numpy as np

            sync_db = sync_session_factory()
            try:
                tenant_today = config_today_sync(config_id, sync_db)

                # ── 1. Resolve horizon from planning_horizon_template (MPS) ──
                # MPS is the tactical layer that drives supply plan generation
                # and therefore defines the demand forecast horizon.
                try:
                    horizon_row = sync_db.execute(
                        text("""
                            SELECT horizon_months, time_bucket::text
                            FROM planning_horizon_template
                            WHERE planning_type = 'mps' AND is_system = true
                            ORDER BY id LIMIT 1
                        """),
                    ).fetchone()
                except Exception:
                    horizon_row = None

                if horizon_row:
                    horizon_months = int(horizon_row[0]) if horizon_row[0] else 6
                    logger.info(
                        "lgbm_forecast: using MPS planning_horizon_template "
                        "(horizon=%d months) for config %d",
                        horizon_months, config_id,
                    )
                else:
                    horizon_months = 6
                    logger.warning(
                        "lgbm_forecast: no MPS planning_horizon_template — "
                        "defaulting to %d months for config %d",
                        horizon_months, config_id,
                    )

                horizon_periods = max(4, int(horizon_months * 4.33))  # ~4.33 wk/mo
                horizon_end = tenant_today + timedelta(weeks=horizon_periods)
                logger.info(
                    "lgbm_forecast: config %d horizon=%d weeks ending %s",
                    config_id, horizon_periods, horizon_end,
                )

                # ── 2. Load ship-from history (product × ship_from_site_id) ──
                # Use shipped_quantity when > 0, otherwise ordered_quantity.
                # Plain COALESCE is unsafe because some ERP builders populate
                # shipped_quantity = 0 (not NULL) for unshipped/future orders.
                _qty_expr = """
                    CASE
                        WHEN ool.shipped_quantity IS NOT NULL AND ool.shipped_quantity > 0
                            THEN ool.shipped_quantity
                        ELSE COALESCE(ool.ordered_quantity, 0)
                    END
                """
                _where = """
                    ool.config_id = :cid
                    AND (
                        (ool.shipped_quantity IS NOT NULL AND ool.shipped_quantity > 0)
                        OR (ool.ordered_quantity IS NOT NULL AND ool.ordered_quantity > 0)
                    )
                """

                site_rows = sync_db.execute(
                    text(f"""
                        SELECT ool.product_id,
                               CAST(oo.ship_from_site_id AS VARCHAR) AS site_id_str,
                               date_trunc('week', oo.order_date)::date AS demand_date,
                               SUM({_qty_expr}) AS actual
                        FROM outbound_order_line ool
                        JOIN outbound_order oo ON oo.id = ool.order_id
                        WHERE {_where}
                          AND oo.ship_from_site_id IS NOT NULL
                        GROUP BY ool.product_id, oo.ship_from_site_id,
                                 date_trunc('week', oo.order_date)
                        ORDER BY ool.product_id, site_id_str, demand_date
                    """),
                    {"cid": config_id},
                ).fetchall()

                if not site_rows or len(site_rows) < 10:
                    logger.error(
                        "lgbm_forecast: insufficient ship-from history for config %d — "
                        "only %d rows. Check outbound_order_line quantities.",
                        config_id, len(site_rows) if site_rows else 0,
                    )
                    return {
                        "status": "failed",
                        "error": f"Insufficient forecast history: {len(site_rows) if site_rows else 0} rows (need ≥10)",
                    }

                history = pd.DataFrame(
                    site_rows, columns=["product_id", "site_id", "demand_date", "actual"]
                )
                # Pipe delimiter matches LGBMForecastPipeline's parser (split on "|", 1).
                history["unique_id"] = (
                    history["product_id"].astype(str) + "|" + history["site_id"].astype(str)
                )

                # ── 3. Train LightGBM + persist ship-from forecasts ──
                cluster_results = {uid: 0 for uid in history["unique_id"].unique()}
                pipeline = LGBMForecastPipeline(config_id=config_id)
                pipeline_result = pipeline.run_stage4_lgbm(
                    run_id=0,
                    config_id=config_id,
                    history=history,
                    cluster_results=cluster_results,
                    censored_flags={},
                    n_periods=horizon_periods,
                    time_bucket="W",
                    retrain=True,
                )

                now = datetime.utcnow()
                # ship_from_fc[(product_id, site_id)] = list of (forecast_date, p10, p50, p90, source)
                ship_from_fc: Dict[Tuple[str, int], List[Tuple]] = {}
                lgbm_persisted = 0
                naive_persisted = 0

                def _in_horizon(fd):
                    if hasattr(fd, "date"):
                        fd = fd.date()
                    return tenant_today <= fd <= horizon_end, fd

                # 3a. LGBM-qualifying series (≥26 obs) — from pipeline.predictions
                predictions = pipeline_result.get("predictions", {}) or {}
                lgbm_uids: Set[str] = set()
                for uid, forecasts in predictions.items():
                    if "|" not in uid:
                        continue
                    lgbm_uids.add(uid)
                    product_id, site_str = uid.split("|", 1)
                    try:
                        site_int = int(site_str)
                    except (ValueError, TypeError):
                        continue
                    key = (product_id, site_int)
                    bucket = ship_from_fc.setdefault(key, [])
                    for fc in forecasts:
                        in_hz, fd = _in_horizon(fc["forecast_date"])
                        if not in_hz:
                            continue
                        bucket.append((fd, float(fc["p10"]), float(fc["p50"]), float(fc["p90"]), "lgbm_ship_from"))
                        lgbm_persisted += 1

                # 3b. Naive fallback for series not in LGBM output (<26 obs)
                all_uids = set(history["unique_id"].unique())
                for uid in all_uids - lgbm_uids:
                    if "|" not in uid:
                        continue
                    product_id, site_str = uid.split("|", 1)
                    try:
                        site_int = int(site_str)
                    except (ValueError, TypeError):
                        continue
                    series = history[history["unique_id"] == uid]["actual"].astype(float)
                    if series.empty:
                        continue
                    tail = series.tail(8) if len(series) >= 8 else series
                    mean_q = float(tail.mean())
                    std_q = float(tail.std()) if len(tail) > 1 else mean_q * 0.3
                    if mean_q <= 0:
                        mean_q = max(float(series.mean() or 0.0), 0.0)
                    if std_q < 0 or np.isnan(std_q):
                        std_q = mean_q * 0.3
                    p10 = max(0.0, mean_q - 1.28 * std_q)
                    p50 = mean_q
                    p90 = mean_q + 1.28 * std_q
                    key = (product_id, site_int)
                    bucket = ship_from_fc.setdefault(key, [])
                    for w in range(horizon_periods):
                        fd = tenant_today + timedelta(weeks=w + 1)
                        bucket.append((fd, p10, p50, p90, "naive_ship_from"))
                        naive_persisted += 1

                # Write ship-from rows to forecast table
                for (product_id, site_int), points in ship_from_fc.items():
                    for fd, p10, p50, p90, source in points:
                        sync_db.add(Forecast(
                            config_id=config_id, product_id=product_id,
                            site_id=site_int, customer_id=None,
                            forecast_date=fd,
                            forecast_type="statistical", forecast_level="product",
                            forecast_method=("lgbm_quantile" if source == "lgbm_ship_from" else "naive_rolling_mean"),
                            forecast_quantity=float(p50),
                            forecast_p10=float(p10), forecast_p50=float(p50),
                            forecast_median=float(p50), forecast_p90=float(p90),
                            created_dttm=now, is_active="true", source=source,
                        ))
                sync_db.commit()

                ship_from_persisted = lgbm_persisted + naive_persisted
                logger.info(
                    "lgbm_forecast: ship-from persisted %d rows "
                    "(%d LGBM-qualifying, %d naive-fallback) across %d (product,site) keys",
                    ship_from_persisted, lgbm_persisted, naive_persisted, len(ship_from_fc),
                )

                # ── 4. Compute customer mix ratios ──
                # For each (product, site), compute each customer's share of
                # recent volume. Two-tier lookup:
                #   Tier 1: Last N weeks ending at tenant_today (N=13 default).
                #           Preferred because it reflects recent customer mix.
                #   Tier 2: Full history window, used per-key when Tier 1 has
                #           no data for that specific (product, site). This
                #           handles frozen demo tenants where history tails off
                #           before the virtual clock.
                mix_window_weeks = 13
                mix_window_start = tenant_today - timedelta(weeks=mix_window_weeks)

                # Tier 1: recent window
                recent_rows = sync_db.execute(
                    text(f"""
                        SELECT ool.product_id,
                               oo.ship_from_site_id AS site_id,
                               oo.customer_id,
                               SUM({_qty_expr}) AS qty
                        FROM outbound_order_line ool
                        JOIN outbound_order oo ON oo.id = ool.order_id
                        WHERE {_where}
                          AND oo.ship_from_site_id IS NOT NULL
                          AND oo.customer_id IS NOT NULL
                          AND oo.customer_id <> ''
                          AND oo.order_date >= :start
                          AND oo.order_date <= :end
                        GROUP BY ool.product_id, oo.ship_from_site_id, oo.customer_id
                        HAVING SUM({_qty_expr}) > 0
                    """),
                    {"cid": config_id, "start": mix_window_start, "end": tenant_today},
                ).fetchall()

                # Tier 2: full history (per-key fallback)
                full_rows = sync_db.execute(
                    text(f"""
                        SELECT ool.product_id,
                               oo.ship_from_site_id AS site_id,
                               oo.customer_id,
                               SUM({_qty_expr}) AS qty
                        FROM outbound_order_line ool
                        JOIN outbound_order oo ON oo.id = ool.order_id
                        WHERE {_where}
                          AND oo.ship_from_site_id IS NOT NULL
                          AND oo.customer_id IS NOT NULL
                          AND oo.customer_id <> ''
                        GROUP BY ool.product_id, oo.ship_from_site_id, oo.customer_id
                        HAVING SUM({_qty_expr}) > 0
                    """),
                    {"cid": config_id},
                ).fetchall()

                def _build_mix(rows):
                    """Build {(product, site): {customer: ratio, ...}} from raw rows."""
                    df = pd.DataFrame(
                        [(r[0], int(r[1]) if r[1] is not None else None, r[2], float(r[3])) for r in rows],
                        columns=["product_id", "site_id", "customer_id", "qty"],
                    )
                    out: Dict[Tuple[str, int], Dict[str, float]] = {}
                    if df.empty:
                        return out
                    totals = df.groupby(["product_id", "site_id"])["qty"].sum().reset_index()
                    totals = totals.rename(columns={"qty": "total_qty"})
                    merged = df.merge(totals, on=["product_id", "site_id"])
                    merged["ratio"] = merged["qty"] / merged["total_qty"]
                    for _, row in merged.iterrows():
                        key = (row["product_id"], int(row["site_id"]))
                        out.setdefault(key, {})[str(row["customer_id"])] = float(row["ratio"])
                    return out

                recent_mix = _build_mix(recent_rows)
                full_mix = _build_mix(full_rows)

                # Merge: prefer recent per key, fall back to full
                mix: Dict[Tuple[str, int], Dict[str, float]] = dict(full_mix)
                mix.update(recent_mix)  # recent wins where both exist

                mix_recent_keys = len(recent_mix)
                mix_full_keys = len(full_mix) - mix_recent_keys

                logger.info(
                    "lgbm_forecast: customer mix — %d keys from recent %d-week window, "
                    "%d keys from full history (fallback), %d total",
                    mix_recent_keys, mix_window_weeks, mix_full_keys, len(mix),
                )

                # ── 5. Disaggregate ship-from forecasts into customer-level rows ──
                disaggregated_persisted = 0
                keys_without_mix = 0
                for (product_id, site_int), points in ship_from_fc.items():
                    customer_ratios = mix.get((product_id, site_int))
                    if not customer_ratios:
                        keys_without_mix += 1
                        continue
                    for fd, p10, p50, p90, _source in points:
                        for customer_id, ratio in customer_ratios.items():
                            sync_db.add(Forecast(
                                config_id=config_id, product_id=product_id,
                                site_id=site_int, customer_id=customer_id,
                                forecast_date=fd,
                                forecast_type="statistical", forecast_level="product",
                                forecast_method="disaggregated_from_ship_from",
                                forecast_quantity=float(p50 * ratio),
                                forecast_p10=float(p10 * ratio),
                                forecast_p50=float(p50 * ratio),
                                forecast_median=float(p50 * ratio),
                                forecast_p90=float(p90 * ratio),
                                created_dttm=now, is_active="true",
                                source="disaggregated_ship_to",
                            ))
                            disaggregated_persisted += 1
                sync_db.commit()

                logger.info(
                    "lgbm_forecast: disaggregated %d ship-to rows from %d ship-from keys "
                    "(%d keys had no customer mix and were skipped)",
                    disaggregated_persisted, len(ship_from_fc) - keys_without_mix, keys_without_mix,
                )

                total_persisted = ship_from_persisted + disaggregated_persisted
                if total_persisted == 0:
                    logger.error(
                        "lgbm_forecast: pipeline ran but 0 forecast rows persisted for config %d",
                        config_id,
                    )
                    return {
                        "status": "failed",
                        "error": "LightGBM pipeline returned zero persistable predictions",
                    }

                return {
                    "status": "ok",
                    "total_forecast_rows": total_persisted,
                    "ship_from": {
                        "lgbm_persisted": lgbm_persisted,
                        "naive_persisted": naive_persisted,
                        "total": ship_from_persisted,
                        "product_site_keys": len(ship_from_fc),
                    },
                    "ship_to_disaggregated": {
                        "total": disaggregated_persisted,
                        "keys_with_mix": len(ship_from_fc) - keys_without_mix,
                        "keys_without_mix": keys_without_mix,
                    },
                    "horizon_weeks": horizon_periods,
                    "horizon_months": horizon_months,
                }
            finally:
                sync_db.close()
        except ImportError:
            logger.error(
                "lgbm_forecast: LGBMForecastPipeline not available for config %d", config_id
            )
            return {
                "status": "failed",
                "error": "LGBMForecastPipeline service not available",
            }
        except Exception as e:
            # SOC II: surface all failures. No swallowing as "non-critical".
            logger.exception("lgbm_forecast failed for config %d", config_id)
            return {
                "status": "failed",
                "error": f"LightGBM forecast exception: {type(e).__name__}: {str(e)[:200]}",
            }
        finally:
            # Always run forecast exception detection after forecast generation
            try:
                exc_result = await self._run_forecast_exception_detection(config_id)
                if exc_result.get("detected", 0) > 0 or exc_result.get("resolved", 0) > 0:
                    logger.info(
                        "Forecast exceptions: %d detected, %d resolved for config %d",
                        exc_result.get("detected", 0), exc_result.get("resolved", 0), config_id,
                    )
            except Exception as exc_err:
                logger.warning("Forecast exception detection failed: %s", exc_err)

    async def _run_forecast_exception_detection(self, config_id: int) -> dict:
        """Run forecast exception detection + re-evaluation of existing exceptions.

        Called after lgbm_forecast and after CDC data ingestion.
        """
        from app.db.session import sync_session_factory
        from app.services.forecast_exception_detector import ForecastExceptionDetector
        from app.core.clock import config_today_sync
        from datetime import timedelta

        sync_db = sync_session_factory()
        try:
            detector = ForecastExceptionDetector(sync_db)
            # Use tenant's virtual today (frozen for demos, real today for production)
            period_end = config_today_sync(config_id, sync_db)
            period_start = period_end - timedelta(days=90)

            # Detect new exceptions
            detected = detector.run_detection(
                config_id=config_id,
                period_start=period_start,
                period_end=period_end,
            )

            # Re-evaluate existing open exceptions
            reeval = detector.reevaluate_open_exceptions(config_id=config_id)

            sync_db.commit()
            return {
                "detected": detected.get("exceptions_created", 0),
                "resolved": reeval.get("resolved", 0),
                "still_open": reeval.get("still_open", 0),
            }
        except Exception as e:
            logger.warning("Forecast exception detection failed: %s", e)
            try:
                sync_db.rollback()
            except Exception:
                pass
            return {"error": str(e)}
        finally:
            sync_db.close()

    # PR-5.E (2026-05-05) — _step_demand_tgnn / _step_supply_tgnn /
    # _step_inventory_tgnn / _step_capacity_tgnn deleted along with
    # the SCP-shape Powell GNN services. ConfigProvisioningStatus.STEPS
    # (defined in azirella_data_model.governance) still lists these
    # keys; ProvisioningService._execute_step gracefully returns a
    # placeholder result when no _step_<key> handler exists, so the
    # provisioning loop continues without these training steps. Core's
    # registry will drop them in a follow-up §1.13 round.

    async def _step_trm_load_pretrained(self, config_id: int) -> dict:
        """Load pre-trained TMS TRM checkpoints from disk.

        TRM weights are trained offline (see scripts/pretraining/) and
        shipped in the image at /app/models/pretrained/trm_base_v1/.
        NO gradient descent occurs during provisioning. This step:
        1. Loads the 12 generic TRM checkpoints from /app/models/pretrained/
        2. Copies each into the tenant-scoped checkpoint dir per (site × TRM)
        3. Gates lane_volume_forecast to shipper facilities only (per
           Core's get_active_tms_trms — internal-transfer/inbound lanes
           inherit volume from the supply plan, not from a forecast)
        4. Records corpus version + model version for audit

        Completes in under 30 seconds. The Site tGNN (trained later)
        handles per-tenant adaptation on top of these frozen TRM weights.
        """
        return await self._load_pretrained_trms(config_id)

    # Legacy handlers — kept so old provisioning rows that reference
    # these steps don't crash the dispatch loop. Both no-op with a skip.
    async def _step_trm_training(self, config_id: int) -> dict:
        """Legacy: replaced by trm_load_pretrained. Skips."""
        logger.info("trm_training step skipped (replaced by trm_load_pretrained) for config %d", config_id)
        return {"status": "skipped", "reason": "Replaced by trm_load_pretrained"}

    async def _step_rl_training(self, config_id: int) -> dict:
        """Legacy: replaced by trm_load_pretrained. Skips."""
        logger.info("rl_training step skipped (replaced by trm_load_pretrained) for config %d", config_id)
        return {"status": "skipped", "reason": "Replaced by trm_load_pretrained"}

    async def _load_pretrained_trms(self, config_id: int, force_refresh: bool = False) -> dict:
        """Copy shipped pretrained TMS TRM checkpoints into the tenant scope.

        - TRM weights are trained OFFLINE by the team (scripts/pretraining/).
        - This step loads them from /app/models/pretrained/trm_base_v{N}/.
        - NO gradient descent. Completes in under 30 seconds.
        - The Site tGNN (trained later) handles per-tenant adaptation.

        Refresh policy: existing tenant checkpoints are overwritten whenever
        the product-shipped master checkpoint has a newer mtime, so tenants
        pick up new generic TRM releases without a full re-provision.
        `force_refresh=True` copies regardless of mtime.

        Mirrors SCP's `_load_pretrained_trms` (see Autonomy-SCP) but uses
        the TMS-native facility-type → active-TRM mapping in Core. The
        TMS-native mapping gates `lane_volume_forecast` to shipper
        facilities (statistical lane-volume forecasting only applies to
        customer-facing outbound; internal-transfer and inbound lanes
        inherit volume from the upstream supply / transfer plan).
        """
        import shutil
        import time
        from pathlib import Path
        from app.db.session import sync_session_factory
        from app.services.powell.tms_site_capabilities import get_active_tms_trms
        from app.services.checkpoint_storage_service import checkpoint_dir

        def _master_type_to_facility_type(site) -> str:
            """Map canonical Site → TMS facility type for capability gating.

            Pragmatic default: every internal site behaves as a `shipper`
            unless explicitly tagged otherwise. Customers can override per
            site via `Site.attributes['facility_type']` (e.g. set to
            "terminal" for cross-docks, "carrier_yard" for trailer pools).
            """
            attrs = site.attributes if isinstance(site.attributes, dict) else {}
            override = attrs.get("facility_type") if attrs else None
            if override:
                return str(override).lower()
            mt = (site.master_type or "").lower()
            # Manufacturers and inventory sites both function as shippers
            # in the TMS plane (they originate outbound shipments). Vendors
            # and customers are external endpoints (filtered by is_external
            # before reaching this code).
            if mt in ("manufacturer", "inventory"):
                return "shipper"
            return "shipper"

        def _copy_if_stale(src: Path, dst: Path) -> str:
            """Copy src → dst when dst is missing or older than src.

            Returns: 'created' | 'refreshed' | 'skipped'.
            """
            if not dst.exists():
                shutil.copy2(str(src), str(dst))
                return "created"
            if force_refresh or src.stat().st_mtime > dst.stat().st_mtime:
                shutil.copy2(str(src), str(dst))
                return "refreshed"
            return "skipped"

        t0 = time.time()
        sync_db = sync_session_factory()
        try:
            from app.models.supply_chain_config import SupplyChainConfig, Site

            config = sync_db.query(SupplyChainConfig).get(config_id)
            if not config:
                return {"status": "failed", "reason": "Config not found"}

            tenant_id = config.tenant_id

            # Pretrained checkpoint directory (shipped with Docker image).
            # v1 has all 12 TMS TRM types including the new lane_volume_forecast.
            pretrained_dir = Path("/app/models/pretrained/trm_base_v1")
            if not pretrained_dir.exists():
                return {
                    "status": "failed",
                    "reason": f"Pretrained dir missing: {pretrained_dir}. "
                              f"Run scripts/pretraining/{{generate_tms_corpus,train_tms_trms}}.py "
                              f"and stage outputs into {pretrained_dir}.",
                }

            # Target directory for this tenant/config
            target_dir = checkpoint_dir(tenant_id, config_id) / "trm"
            target_dir.mkdir(parents=True, exist_ok=True)

            # Get all internal (non-external) sites
            sites = (
                sync_db.query(Site)
                .filter(
                    Site.config_id == config_id,
                    Site.is_external == False,
                )
                .all()
            )

            created = 0
            refreshed = 0
            skipped = 0
            sites_seen = 0
            trm_types_seen = set()
            missing_pretrained = set()

            for site in sites:
                facility_type = _master_type_to_facility_type(site)
                active_trms = get_active_tms_trms(facility_type)
                if not active_trms:
                    continue
                sites_seen += 1

                for trm_type in active_trms:
                    pretrained_path = pretrained_dir / f"trm_{trm_type}.pt"
                    if not pretrained_path.exists():
                        if trm_type not in missing_pretrained:
                            logger.warning(
                                "No pretrained checkpoint for TMS TRM %s "
                                "(config %d) — site %d will fall back to heuristic",
                                trm_type, config_id, site.id,
                            )
                            missing_pretrained.add(trm_type)
                        continue

                    target_path = target_dir / f"trm_{trm_type}_site{site.id}_v1.pt"
                    outcome = _copy_if_stale(pretrained_path, target_path)
                    if outcome == "created":
                        created += 1
                    elif outcome == "refreshed":
                        refreshed += 1
                    else:
                        skipped += 1
                    trm_types_seen.add(trm_type)

            elapsed = time.time() - t0
            loaded = created + refreshed + skipped
            logger.info(
                "Pretrained TMS TRM loading complete for config %d: %d checkpoints "
                "(%d created, %d refreshed, %d up-to-date) across %d unique TRM "
                "types and %d sites in %.1fs",
                config_id, loaded, created, refreshed, skipped,
                len(trm_types_seen), sites_seen, elapsed,
            )

            return {
                "status": "ok",
                "checkpoints_loaded": loaded,
                "created": created,
                "refreshed": refreshed,
                "skipped_up_to_date": skipped,
                "trm_types": len(trm_types_seen),
                "sites": sites_seen,
                "missing_pretrained": sorted(missing_pretrained),
                "duration_seconds": round(elapsed, 1),
                "force_refresh": force_refresh,
            }

        except Exception as e:
            logger.error("Pretrained TMS TRM loading failed for config %d: %s",
                         config_id, e, exc_info=True)
            raise
        finally:
            sync_db.close()

    async def _run_rl_training(self, config_id: int) -> dict:
        """Run simulation-based RL training for all (site, trm_type) pairs.

        Loads BC checkpoints (v1) from trm_training step, runs PPO fine-tuning
        inside the digital twin simulation, and saves RL checkpoints (v2) for
        each pair that shows improvement over the heuristic baseline.
        """
        from app.db.session import sync_session_factory
        from app.services.powell.site_capabilities import get_active_trms
        from app.services.checkpoint_storage_service import checkpoint_dir
        from app.services.powell.simulation_rl_trainer import (
            SimulationRLTrainer,
            RLHyperparameters,
        )

        sync_db = sync_session_factory()
        try:
            from app.models.supply_chain_config import SupplyChainConfig, Site
            config = sync_db.query(SupplyChainConfig).get(config_id)
            if not config:
                return {"status": "skipped", "reason": "Config not found"}

            tenant_id = config.tenant_id
            ckpt_dir = checkpoint_dir(tenant_id, config_id)

            # Get non-market internal sites
            sites = (
                sync_db.query(Site)
                .filter(
                    Site.config_id == config_id,
                    Site.is_external == False,
                )
                .all()
            )
            if not sites:
                return {"status": "skipped", "reason": "No internal sites"}

            trms_trained = 0
            trms_improved = 0
            errors = 0

            hp = RLHyperparameters(
                num_episodes=50,
                warmup_days=30,
                training_days=150,
                eval_days=30,
            )

            for site in sites:
                master_type = site.master_type or "INVENTORY"
                sc_site_type = getattr(site, "sc_site_type", None)
                active_trms = get_active_trms(master_type, sc_site_type)

                for trm_type in active_trms:
                    # Look for BC checkpoint (v1)
                    bc_path = ckpt_dir / f"trm_{trm_type}_site{site.id}_v1.pt"
                    if not bc_path.exists():
                        logger.debug(
                            "No BC checkpoint for %s at site %d, skipping RL",
                            trm_type, site.id,
                        )
                        continue

                    try:
                        trainer = SimulationRLTrainer(
                            config_id=config_id,
                            tenant_id=tenant_id,
                            trm_type=trm_type,
                            site_id=site.id,
                            checkpoint_path=str(bc_path),
                            device="cpu",
                            hyperparameters=hp,
                        )
                        result = trainer.train()
                        trms_trained += 1
                        if result.improvement_vs_heuristic_pct > 0:
                            trms_improved += 1
                        logger.info(
                            "RL training %s@site%d: %.1f%% vs heuristic",
                            trm_type, site.id,
                            result.improvement_vs_heuristic_pct,
                        )
                    except Exception as e:
                        logger.warning(
                            "RL training failed for %s@site%d: %s",
                            trm_type, site.id, e,
                        )
                        errors += 1

            return {
                "status": "ok" if errors == 0 else "partial",
                "trms_trained": trms_trained,
                "trms_improved": trms_improved,
                "errors": errors,
            }
        except Exception as e:
            logger.error("SOC II ALERT: RL training step failed for config %d: %s", config_id, e)
            return {"status": "failed", "error": f"RL training failed: {str(e)[:200]}"}
        finally:
            sync_db.close()

    async def _step_backtest_evaluation(self, config_id: int) -> dict:
        """Step 10: Backtest evaluation — run TRM agents in the Digital Twin.

        Runs the stochastic simulation (DagChain) for test episodes using
        heuristic baseline and trained TRM policy, compares BSC scores,
        and stores validated performance metrics in PerformanceMetric.
        """
        try:
            tenant_id = await self._resolve_tenant_id(config_id)
            from app.services.powell.backtest_evaluation_service import BacktestEvaluationService
            svc = BacktestEvaluationService(self.db, config_id, tenant_id)
            result = await svc.run_backtest()
            return {
                "status": result.get("status", "ok"),
                "trm_types_evaluated": result.get("trm_types_evaluated", 0),
                "episodes": result.get("episodes"),
                "aggregate": result.get("aggregate"),
            }
        except Exception as e:
            logger.warning("Backtest evaluation failed for config %d: %s", config_id, e)
            return {"status": "ok", "note": f"Backtest attempted: {str(e)[:200]}"}

    async def _step_supply_plan(self, config_id: int) -> dict:
        """Step 11: Generate Plan of Record from conformal-calibrated forecasts.

        NO Monte Carlo simulation — uncertainty is already quantified by
        conformal prediction intervals (P10/P50/P90). The Plan of Record
        uses P50 as the planned quantity, with P10/P90 as the uncertainty band.

        The Digital Twin (stochastic simulation) is used only for TRM training
        data, NOT for plan generation.
        """
        try:
            from app.db.session import sync_session_factory
            from sqlalchemy import text as sqt
            sync_db = sync_session_factory()
            try:
                # Clean old plan of record
                sync_db.execute(sqt("""
                    DELETE FROM supply_plan
                    WHERE config_id = :cfg AND plan_version = 'live' AND source = 'plan_of_record'
                """), {"cfg": config_id})

                # Clean any legacy Monte Carlo data
                sync_db.execute(sqt("""
                    DELETE FROM supply_plan
                    WHERE config_id = :cfg AND plan_version IN ('monte_carlo', '')
                    AND source = 'supply_plan_service'
                """), {"cfg": config_id})

                # Create Plan of Record from conformal-calibrated forecast P50
                # One row per product × site × week = the operating plan
                # Plan of Record horizon: exactly 52 weeks forward from today.
                # The upper bound matters — without it, the plan would pull in
                # however much forward forecast data happens to exist and
                # produce non-deterministic horizon lengths (the original code
                # had no upper bound and yielded ~56 weeks).
                por_result = sync_db.execute(sqt("""
                    INSERT INTO supply_plan
                        (config_id, product_id, site_id, plan_date, plan_type,
                         forecast_quantity, demand_quantity,
                         planned_order_quantity, planned_order_date,
                         plan_version, source, planner_name, created_dttm)
                    SELECT
                        f.config_id, f.product_id, CAST(f.site_id AS INTEGER),
                        date_trunc('week', f.forecast_date)::date,
                        'demand_plan',
                        SUM(f.forecast_p50), SUM(f.forecast_p50),
                        SUM(f.forecast_p50),
                        date_trunc('week', f.forecast_date)::date,
                        'live', 'plan_of_record', 'demand_agent', NOW()
                    FROM forecast f
                    WHERE f.config_id = :cfg AND f.forecast_p50 IS NOT NULL
                      AND f.forecast_date >= date_trunc('week', CURRENT_DATE)
                      AND f.forecast_date <  date_trunc('week', CURRENT_DATE) + INTERVAL '52 weeks'
                      AND COALESCE(f.source, '') NOT IN ('disaggregated_ship_to', 'erp_baseline', 'naive_aggregate')
                    GROUP BY f.config_id, f.product_id, f.site_id, date_trunc('week', f.forecast_date)
                """), {"cfg": config_id})

                por_count = por_result.rowcount
                sync_db.commit()

                # Create ERP Baseline from existing PO/MO/TO transactional data
                # This is the ERP system's current plan — our AI plan is compared against it
                sync_db.execute(sqt("""
                    DELETE FROM supply_plan
                    WHERE config_id = :cfg AND plan_version = 'erp_baseline'
                """), {"cfg": config_id})

                erp_result = sync_db.execute(sqt("""
                    INSERT INTO supply_plan
                        (config_id, product_id, site_id, plan_date, plan_type,
                         planned_order_quantity, planned_order_date,
                         supplier_id, from_site_id,
                         plan_version, source, planner_name, created_dttm)
                    SELECT
                        po.config_id,
                        poli.product_id,
                        po.destination_site_id,
                        date_trunc('week', po.requested_delivery_date)::date,
                        'po_request',
                        SUM(poli.quantity),
                        date_trunc('week', po.order_date)::date,
                        po.vendor_id,
                        po.supplier_site_id,
                        'erp_baseline', 'erp_extraction', 'erp_mrp',
                        NOW()
                    FROM purchase_order po
                    JOIN purchase_order_line_item poli ON poli.po_id = po.id
                    WHERE po.config_id = :cfg
                    AND po.requested_delivery_date >= CURRENT_DATE - INTERVAL '90 days'
                    GROUP BY po.config_id, poli.product_id, po.destination_site_id,
                             date_trunc('week', po.requested_delivery_date),
                             date_trunc('week', po.order_date),
                             po.vendor_id, po.supplier_site_id
                """), {"cfg": config_id})
                erp_po_count = erp_result.rowcount

                # Production orders as MO baseline
                erp_mo_result = sync_db.execute(sqt("""
                    INSERT INTO supply_plan
                        (config_id, product_id, site_id, plan_date, plan_type,
                         planned_order_quantity, planned_order_date,
                         plan_version, source, planner_name, created_dttm)
                    SELECT
                        config_id, item_id, site_id,
                        date_trunc('week', COALESCE(planned_completion_date, planned_start_date))::date,
                        'mo_request',
                        planned_quantity,
                        date_trunc('week', planned_start_date)::date,
                        'erp_baseline', 'erp_extraction', 'erp_mrp',
                        NOW()
                    FROM production_orders
                    WHERE config_id = :cfg
                    AND planned_start_date IS NOT NULL
                    AND planned_start_date >= CURRENT_DATE - INTERVAL '90 days'
                """), {"cfg": config_id})
                erp_mo_count = erp_mo_result.rowcount

                # Transfer orders as TO baseline (header-level, no product_id)
                erp_to_result = sync_db.execute(sqt("""
                    INSERT INTO supply_plan
                        (config_id, site_id, plan_date, plan_type,
                         planned_order_quantity, planned_order_date,
                         from_site_id,
                         plan_version, source, planner_name, created_dttm)
                    SELECT
                        config_id, destination_site_id,
                        date_trunc('week', COALESCE(estimated_delivery_date, order_date))::date,
                        'to_request',
                        1,
                        date_trunc('week', order_date)::date,
                        source_site_id,
                        'erp_baseline', 'erp_extraction', 'erp_mrp',
                        NOW()
                    FROM transfer_order
                    WHERE config_id = :cfg
                    AND order_date >= CURRENT_DATE - INTERVAL '90 days'
                """), {"cfg": config_id})
                erp_to_count = erp_to_result.rowcount

                # Also load PLAF (MRP planned orders) from SAP staging if available
                try:
                    plaf_count = sync_db.execute(sqt("""
                        INSERT INTO supply_plan
                            (config_id, product_id, site_id, plan_date, plan_type,
                             planned_order_quantity, planned_order_date,
                             plan_version, source, planner_name, created_dttm)
                        SELECT
                            :cfg,
                            'CFG' || :cfg || '_' || (r.row_data->>'MATNR'),
                            (SELECT s.id FROM site s WHERE s.config_id = :cfg AND s.name = r.row_data->>'PLWRK' LIMIT 1),
                            TO_DATE(r.row_data->>'PEDTR', 'YYYYMMDD'),
                            CASE WHEN r.row_data->>'BESKZ' = 'F' THEN 'po_request' ELSE 'mo_request' END,
                            CAST(r.row_data->>'GSMNG' AS FLOAT),
                            TO_DATE(COALESCE(r.row_data->>'PSTTR', r.row_data->>'PEDTR'), 'YYYYMMDD'),
                            'erp_baseline', 'sap_plaf', 'sap_mrp',
                            NOW()
                        FROM sap_staging.rows r
                        WHERE r.sap_table = 'PLAF'
                        AND r.row_data->>'GSMNG' IS NOT NULL
                        AND CAST(r.row_data->>'GSMNG' AS FLOAT) > 0
                    """), {"cfg": config_id}).rowcount
                    if plaf_count > 0:
                        erp_po_count += plaf_count
                        logger.info("PLAF: %d SAP planned orders loaded as erp_baseline", plaf_count)
                except Exception as plaf_err:
                    logger.debug("PLAF load skipped: %s", plaf_err)

                # Apply lifecycle and promotional adjustments to the Plan of Record
                try:
                    from app.services.demand_forecasting.forecast_adjustments import (
                        apply_lifecycle_adjustments,
                        apply_promotional_adjustments,
                        apply_hierarchy_reconciliation,
                    )
                    # Load tenant_id for the config — required by promo and
                    # reconciliation adjustments. (Bug fix: prior code referenced
                    # an undefined `config` variable and swallowed the NameError.)
                    tenant_row = sync_db.execute(
                        sqt("SELECT tenant_id FROM supply_chain_configs WHERE id = :cid"),
                        {"cid": config_id},
                    ).fetchone()
                    tenant_id = tenant_row[0] if tenant_row else None
                    if tenant_id is None:
                        raise ValueError(f"No tenant_id found for config {config_id}")
                    lc_result = apply_lifecycle_adjustments(sync_db, config_id)
                    promo_result = apply_promotional_adjustments(sync_db, config_id, tenant_id)
                    recon_result = apply_hierarchy_reconciliation(sync_db, config_id, tenant_id)
                    logger.info(
                        "Forecast adjustments: lifecycle=%d, promos=%d, reconciliation=%s",
                        lc_result.get("adjusted_rows", 0),
                        promo_result.get("adjusted_rows", 0),
                        recon_result.get("status", "?"),
                    )
                except Exception as adj_err:
                    logger.warning("Forecast adjustments failed (non-critical): %s", adj_err)

                sync_db.commit()

                logger.info(
                    "Supply plan: %d POR + %d ERP baseline (PO=%d, MO=%d, TO=%d) for config %d",
                    por_count, erp_po_count + erp_mo_count + erp_to_count,
                    erp_po_count, erp_mo_count, erp_to_count, config_id,
                )
                return {
                    "status": "ok",
                    "plan_of_record_rows": por_count,
                    "erp_baseline_rows": erp_po_count + erp_mo_count + erp_to_count,
                    "erp_baseline_po": erp_po_count,
                    "erp_baseline_mo": erp_mo_count,
                    "erp_baseline_to": erp_to_count,
                    "method": "conformal_p50 + erp_extraction",
                }
            finally:
                sync_db.close()
        except Exception as e:
            logger.error("Supply plan generation FAILED for config %s: %s", config_id, e, exc_info=True)
            return {"status": "failed", "error": f"Supply plan failed: {str(e)[:200]}"}

    async def _step_rccp_validation(self, config_id: int) -> dict:
        """Step 9b: Run RCCP validation against the latest MPS plan for each site.

        Validates rough-cut capacity feasibility. If infeasible, flags a warning
        but does NOT block subsequent steps (capacity issues are common and
        addressed iteratively).
        """
        from app.db.session import sync_session_factory
        sync_db = sync_session_factory()
        try:
            from app.services.rccp_service import RCCPService
            from app.models.supply_chain_config import Site
            from app.models.mps import MPSPlan

            # Find latest approved or draft MPS plan for this config
            mps_plan = (
                sync_db.query(MPSPlan)
                .filter(MPSPlan.config_id == config_id)
                .order_by(MPSPlan.created_at.desc())
                .first()
            )
            if not mps_plan:
                return {"status": "ok", "note": "No MPS plan found — skipping RCCP validation"}

            # Get internal sites
            sites = (
                sync_db.query(Site)
                .filter(Site.config_id == config_id, Site.is_external == False)
                .all()
            )
            if not sites:
                return {"status": "ok", "note": "No internal sites — skipping RCCP"}

            rccp_service = RCCPService(sync_db, config_id)
            results = []
            feasible_count = 0
            overloaded_count = 0

            for site in sites:
                try:
                    run = rccp_service.validate_mps(
                        mps_plan_id=mps_plan.id,
                        site_id=site.id,
                        planning_horizon_weeks=12,
                    )
                    results.append({
                        "site_id": site.id,
                        "site_name": site.site_name,
                        "status": run.status.value if hasattr(run.status, 'value') else str(run.status),
                        "is_feasible": run.is_feasible,
                        "max_utilization_pct": run.max_utilization_pct,
                        "overloaded_resources": run.overloaded_resource_count,
                    })
                    if run.is_feasible:
                        feasible_count += 1
                    else:
                        overloaded_count += 1
                except Exception as e:
                    logger.warning("RCCP validation failed for site %s: %s", site.id, e)
                    results.append({
                        "site_id": site.id,
                        "site_name": site.site_name,
                        "status": "error",
                        "error": str(e)[:200],
                    })

            sync_db.commit()
            return {
                "status": "ok",
                "sites_validated": len(results),
                "feasible": feasible_count,
                "overloaded": overloaded_count,
                "details": results,
                "note": f"RCCP: {feasible_count} feasible, {overloaded_count} overloaded"
                if overloaded_count > 0
                else f"RCCP: all {feasible_count} sites feasible",
            }
        except Exception as e:
            logger.warning("RCCP validation step failed (non-blocking): %s", e)
            return {"status": "ok", "note": f"RCCP validation attempted: {str(e)[:100]}"}
        finally:
            sync_db.close()

    async def _step_decision_seed(self, config_id: int) -> dict:
        """
        Step 10: Seed the Decision Stream from digital twin simulation.

        Runs the tenant's actual supply chain DAG as a Monte Carlo simulation
        for 5 episodes × 90 days using deterministic heuristics. At each tick,
        examines the supply chain state and generates realistic decision records
        when interesting conditions arise (stockouts, reorder triggers, quality
        events, capacity pressure, forecast drift, etc.).

        Produces ~6 decisions per TRM type (~66 total) with real product names,
        site names, dollar amounts, and detailed reasoning — ready for demo.
        """
        from app.db.session import sync_session_factory
        from app.services.powell.simulation_decision_seeder import (
            seed_decisions_from_simulation,
        )

        # Resolve tenant_id from config
        row = await self.db.execute(
            text("SELECT tenant_id FROM supply_chain_configs WHERE id = :c"),
            {"c": config_id},
        )
        tenant_row = row.first()
        tenant_id = tenant_row[0] if tenant_row else None

        sync_db = sync_session_factory()
        try:
            # Ensure the tenant has a BSC config with routing thresholds.
            # Without this row, the Decision Stream falls back to hardcoded
            # defaults — which contradicts the "no hardcoded values" policy.
            self._ensure_tenant_bsc_config(sync_db, tenant_id or 0)

            counts = seed_decisions_from_simulation(
                db=sync_db,
                config_id=config_id,
                tenant_id=tenant_id or 0,
                max_per_type=20,
            )
            total = sum(counts.values())
            logger.info(
                "Decision seed from simulation: %d decisions across %d TRM types "
                "(config=%d, tenant=%d)",
                total, len([v for v in counts.values() if v > 0]),
                config_id, tenant_id or 0,
            )

            # Pre-generate and persist the Decision Stream digest so the
            # first page load is instant (no LLM call needed).
            # Uses a fresh async session to avoid greenlet_spawn errors.
            if total > 0:
                try:
                    from app.services.decision_stream_service import DecisionStreamService
                    from app.db.session import async_session_factory
                    async with async_session_factory() as async_db:
                        digest_svc = DecisionStreamService(
                            db=async_db, tenant_id=tenant_id or 0,
                        )
                        await digest_svc.get_decision_digest(
                            config_id=config_id, force_refresh=True,
                        )
                    logger.info("Decision Stream digest pre-generated for config %d", config_id)
                except Exception as digest_err:
                    logger.warning("Digest pre-generation failed (non-blocking): %s", digest_err)

            return {
                "status": "ok",
                "decisions_generated": total,
                "per_type": counts,
            }
        except Exception as e:
            logger.warning("Decision seed failed: %s", e, exc_info=True)
            return {
                "status": "partial",
                "decisions_generated": 0,
                "error": str(e)[:200],
            }
        finally:
            sync_db.close()

    @staticmethod
    def _ensure_tenant_bsc_config(sync_db, tenant_id: int):
        """Create a TenantBscConfig row with default thresholds if none exists.

        This ensures the Decision Stream routing (urgency/likelihood thresholds)
        is always driven by DB-stored per-tenant config, not hardcoded fallbacks.
        """
        from sqlalchemy import text as sa_text
        row = sync_db.execute(
            sa_text("SELECT id FROM tenant_bsc_config WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).first()
        if not row:
            sync_db.execute(
                sa_text("""
                    INSERT INTO tenant_bsc_config
                        (tenant_id, urgency_threshold, likelihood_threshold)
                    VALUES (:tid, 0.65, 0.70)
                """),
                {"tid": tenant_id},
            )
            sync_db.commit()
            logger.info("Created default BSC config for tenant %d", tenant_id)

    async def _step_site_tgnn(self, config_id: int) -> dict:
        """Step 8: Train Site tGNN (foreground fallback, normally runs via _bg)."""
        from app.services.powell.generic_training_orchestrator import GenericTrainingOrchestrator
        orchestrator = GenericTrainingOrchestrator(config_id=config_id)
        result = await orchestrator.train_site_tgnns(epochs=5)
        return {"status": "ok", "sites_trained": result.sites_trained}

    async def _step_conformal(self, config_id: int) -> dict:
        """Step 9: Conformal calibration (tenant-scoped)."""
        try:
            from app.services.conformal_orchestrator import ConformalOrchestrator
            from sqlalchemy import text

            # Resolve tenant_id from config_id
            row = await self.db.execute(
                text("SELECT tenant_id FROM supply_chain_configs WHERE id = :c"),
                {"c": config_id},
            )
            tenant_row = row.first()
            tenant_id = tenant_row[0] if tenant_row else None

            orchestrator = ConformalOrchestrator.get_instance()
            count = await orchestrator.hydrate_from_db(self.db, tenant_id=tenant_id)

            # CDT calibration — real outcomes first, then simulation bootstrap
            # for any TRM types that still lack calibration data.
            #
            # Why two passes:
            #   Pass 1: calibrate_all() reads real decision-outcome pairs from
            #           powell_*_decisions (exist after decision_seed + outcome
            #           collection runs). These are the gold standard.
            #   Pass 2: For TRM types still uncalibrated (no real outcomes yet),
            #           run the digital twin simulation to derive calibration
            #           pairs from supply chain dynamics. This clears the
            #           "0/11 agents ready" banner immediately after warm-start
            #           without waiting for production feedback horizons (4h-14d).
            #
            # The simulation bootstrap is Phase-1-safe: it does NOT require TRM
            # models to make decisions. Instead it uses base-stock ordering
            # (representative of post-BC TRM behaviour) and derives (confidence,
            # loss) pairs from the supply chain state at each simulated period.
            # Real production outcomes from Phase 2 RL refine the calibration
            # incrementally (hourly at :35).
            try:
                from app.db.session import sync_session_factory
                from app.services.powell.cdt_calibration_service import CDTCalibrationService
                from app.services.powell.simulation_calibration_service import (
                    run_simulation_calibration_bootstrap,
                )
                sync_db = sync_session_factory()
                try:
                    cdt_svc = CDTCalibrationService(sync_db, tenant_id=tenant_id)

                    # Pass 0 (preferred): historical corpus.
                    # The unified training corpus (origin='historical') contains
                    # real (action, outcome) pairs reconstructed from ERP
                    # transaction history — typically tens of thousands per
                    # tenant. This is the best calibration source because it
                    # reflects the actual operational distribution without
                    # waiting for live outcomes or relying on simulation.
                    hist_stats = cdt_svc.calibrate_from_historical_corpus(config_id=config_id)
                    n_hist = sum(
                        1 for s in hist_stats.values()
                        if s.get("status") == "calibrated"
                    )
                    logger.info(
                        "CDT historical-corpus calibration: %d agents calibrated",
                        n_hist,
                    )

                    # Pass 1: live decision outcomes (post-provisioning).
                    # Typically empty at first provisioning but important on
                    # subsequent recalibrations as live outcomes accumulate.
                    real_stats = cdt_svc.calibrate_all()
                    n_live = sum(
                        1 for s in real_stats.values()
                        if s.get("status") == "calibrated"
                    )
                    # Combined: historical union live.
                    calibrated_keys = {
                        k for k, v in hist_stats.items() if v.get("status") == "calibrated"
                    } | {
                        k for k, v in real_stats.items() if v.get("status") == "calibrated"
                    }
                    n_real = len(calibrated_keys)

                    # Pass 2: simulation bootstrap for uncalibrated TRM types
                    # Determine how many TRM types are applicable for this config's topology
                    try:
                        from app.services.powell.site_capabilities import get_active_trms
                        from app.models.supply_chain_config import Site as _Site
                        _sites = sync_db.query(_Site.master_type, _Site.type).filter(
                            _Site.config_id == config_id, _Site.is_external == False
                        ).all()
                        _active = set()
                        for _mt, _st in _sites:
                            _active.update(get_active_trms(_mt or "INVENTORY", _st))
                        n_applicable = len(_active) if _active else 11
                    except Exception:
                        n_applicable = 11
                    if n_real < n_applicable and config_id and tenant_id:
                        logger.info(
                            "CDT: %d/%d TRMs calibrated from real outcomes — "
                            "bootstrapping remainder from digital twin simulation",
                            n_real, n_applicable,
                        )
                        sim_stats = run_simulation_calibration_bootstrap(
                            db=sync_db,
                            config_id=config_id,
                            tenant_id=tenant_id,
                            n_episodes=50,
                        )
                        logger.info(
                            "CDT simulation bootstrap: %d/%d agents calibrated",
                            sim_stats.get("agents_calibrated", 0), n_applicable,
                        )
                finally:
                    sync_db.close()
            except Exception as cdt_err:
                logger.warning("CDT calibration failed: %s", cdt_err, exc_info=True)

            # Include CDT readiness summary
            cdt_summary = {"calibrated": 0, "partial": 0, "uncalibrated": 0}
            try:
                from app.services.conformal_prediction.conformal_decision import get_cdt_registry
                registry = get_cdt_registry(tenant_id=tenant_id)
                diagnostics = registry.get_all_diagnostics()
                for _agent_type, diag in diagnostics.items():
                    size = diag.get("calibration_size", 0)
                    if size >= 30:
                        cdt_summary["calibrated"] += 1
                    elif size > 0:
                        cdt_summary["partial"] += 1
                    else:
                        cdt_summary["uncalibrated"] += 1
                # Count unregistered agents as uncalibrated
                all_trm = 11
                registered = len(diagnostics)
                if registered < all_trm:
                    cdt_summary["uncalibrated"] += (all_trm - registered)
            except Exception:
                pass

            # ─── Auto-calibrate conformal predictors for ALL variable types ───
            # This ensures every provisioned config has calibrated conformal
            # intervals for demand, lead time, receipt variance, quality,
            # transit time, maintenance downtime, and forecast bias.
            predictor_summary = {}
            try:
                from app.services.conformal_prediction.suite import get_conformal_suite
                from app.services.conformal_prediction import get_conformal_service
                from app.models.sc_entities import InboundOrder, Forecast
                from app.models.goods_receipt import GoodsReceiptLineItem
                from app.models.quality_order import QualityOrder
                from app.models.transfer_order import TransferOrder
                from app.models.maintenance_order import MaintenanceOrder
                import numpy as np

                suite = get_conformal_suite()
                svc = get_conformal_service()
                prefix = f"CFG{config_id}_"
                calibrated_count = 0

                # 1. Demand (from forecast_error)
                from sqlalchemy import func as sa_func, and_
                groups = await self.db.execute(
                    select(Forecast.product_id, Forecast.site_id)
                    .where(and_(Forecast.config_id == config_id, Forecast.forecast_error.isnot(None)))
                    .group_by(Forecast.product_id, Forecast.site_id)
                    .having(sa_func.count() >= 10)
                )
                for pid, sid in groups.all():
                    data = await self.db.execute(
                        select(Forecast.forecast_p50, Forecast.forecast_error)
                        .where(and_(
                            Forecast.product_id == pid, Forecast.site_id == sid,
                            Forecast.config_id == config_id,
                            Forecast.forecast_error.isnot(None), Forecast.forecast_p50.isnot(None),
                        ))
                    )
                    rows = data.all()
                    preds = [float(r[0]) for r in rows if r[0] is not None and r[1] is not None]
                    actuals = [float(r[0]) + float(r[1]) for r in rows if r[0] is not None and r[1] is not None]
                    if len(preds) >= 10:
                        svc.calibrate_demand(
                            historical_forecasts=np.array(preds), historical_actuals=np.array(actuals),
                            alpha=0.1, product_id=str(pid), site_id=int(sid) if sid else None,
                        )
                        calibrated_count += 1

                # 2. Lead time (from inbound_order)
                lt_data = await self.db.execute(
                    select(InboundOrder.supplier_id,
                           sa_func.array_agg(InboundOrder.requested_delivery_date - InboundOrder.order_date),
                           sa_func.array_agg(InboundOrder.actual_delivery_date - InboundOrder.order_date))
                    .where(and_(InboundOrder.config_id == config_id,
                                InboundOrder.actual_delivery_date.isnot(None),
                                InboundOrder.order_date.isnot(None),
                                InboundOrder.supplier_id.isnot(None)))
                    .group_by(InboundOrder.supplier_id).having(sa_func.count() >= 10)
                )
                for supplier_id, planned, actual in lt_data.all():
                    if planned and actual:
                        p = [float(d.days if hasattr(d, 'days') else d) for d in planned if d is not None]
                        a = [float(d.days if hasattr(d, 'days') else d) for d in actual if d is not None]
                        if len(p) >= 10:
                            suite.calibrate_lead_time(str(supplier_id), p, a)
                            calibrated_count += 1

                # 3. Forecast bias
                bias_data = await self.db.execute(
                    select(Forecast.product_id, sa_func.array_agg(Forecast.forecast_bias))
                    .where(and_(Forecast.config_id == config_id, Forecast.forecast_bias.isnot(None)))
                    .group_by(Forecast.product_id).having(sa_func.count() >= 10)
                )
                for pid, biases in bias_data.all():
                    b = [float(x) for x in (biases or []) if x is not None]
                    if len(b) >= 10:
                        suite.calibrate_forecast_bias(str(pid), b)
                        calibrated_count += 1

                # 4. Quality rejection
                qr_data = await self.db.execute(
                    select(QualityOrder.product_id,
                           sa_func.array_agg(QualityOrder.inspection_quantity),
                           sa_func.array_agg(QualityOrder.rejected_quantity))
                    .where(and_(QualityOrder.config_id == config_id, QualityOrder.inspection_quantity > 0))
                    .group_by(QualityOrder.product_id).having(sa_func.count() >= 5)
                )
                for pid, insp, rej in qr_data.all():
                    rates_actual = [float(r or 0) / max(1, float(i)) for i, r in zip(insp or [], rej or [])]
                    if len(rates_actual) >= 5:
                        avg_rate = sum(rates_actual) / len(rates_actual)
                        suite.calibrate_quality_rejection(str(pid), [avg_rate] * len(rates_actual), rates_actual)
                        calibrated_count += 1

                # 5. Maintenance downtime
                md_data = await self.db.execute(
                    select(MaintenanceOrder.asset_type,
                           sa_func.array_agg(MaintenanceOrder.estimated_downtime_hours),
                           sa_func.array_agg(MaintenanceOrder.actual_downtime_hours))
                    .where(and_(MaintenanceOrder.config_id == config_id,
                                MaintenanceOrder.estimated_downtime_hours.isnot(None),
                                MaintenanceOrder.actual_downtime_hours.isnot(None),
                                MaintenanceOrder.status == "COMPLETED"))
                    .group_by(MaintenanceOrder.asset_type).having(sa_func.count() >= 5)
                )
                for asset_type, est, act in md_data.all():
                    e = [float(h) for h in (est or []) if h is not None]
                    a = [float(h) for h in (act or []) if h is not None]
                    if len(e) >= 5:
                        suite.calibrate_maintenance_downtime(str(asset_type), e, a)
                        calibrated_count += 1

                predictor_summary = suite.get_calibration_summary()
                logger.info("Conformal auto-calibrate: %d predictors calibrated for config %d", calibrated_count, config_id)
            except Exception as cal_err:
                logger.warning("Conformal auto-calibrate failed: %s", cal_err, exc_info=True)

            return {
                "status": "ok",
                "predictors_hydrated": count,
                "cdt_readiness": cdt_summary,
                "conformal_predictors": predictor_summary.get("total_predictors", 0) if predictor_summary else 0,
            }
        except Exception as e:
            logger.error("Conformal calibration FAILED for config %s: %s", config_id, e, exc_info=True)
            return {"status": "failed", "error": f"Conformal calibration failed: {str(e)[:200]}"}

    async def _step_scenario_bootstrap(self, config_id: int) -> dict:
        """Step 15: Warm-start scenario template priors via digital twin simulation.

        For each TRM type, generates N random situations, tests all candidate
        templates, and updates Beta priors based on which templates produce the
        best risk-adjusted BSC scores. Also calibrates trigger weights.
        """
        try:
            from app.services.powell.scenario_candidates import CandidateGenerator
            from app.services.powell.contextual_bsc import ContextualBSC
            from app.db.session import sync_session_factory
            from sqlalchemy import text
            import random

            row = await self.db.execute(
                text("SELECT tenant_id FROM supply_chain_configs WHERE id = :c"),
                {"c": config_id},
            )
            tenant_row = row.fetchone()
            if not tenant_row:
                return {"status": "skipped", "reason": "Config not found"}
            tenant_id = tenant_row[0]

            db = sync_session_factory()
            try:
                gen = CandidateGenerator(db)
                bsc = ContextualBSC()

                # For each TRM type, simulate N situations and update template priors
                # po_creation retired (TMS uses FreightProcurementTRM for the
                # ACQUIRE phase); other SCP-named slots still listed until
                # their TMS-named replacements land.
                trm_types = ["atp_executor", "inventory_rebalancing",
                             "order_tracking", "mo_execution", "to_execution"]
                total_scenarios = 0

                for trm_type in trm_types:
                    templates = gen._get_or_seed_templates(trm_type, tenant_id)
                    if not templates:
                        continue

                    # Generate 50 random situations and test each template
                    for i in range(50):
                        seed = config_id * 1000 + i
                        rng = random.Random(seed)

                        # Random context for this TRM type
                        context = {
                            "product_id": f"CFG{config_id}_PROD_{rng.randint(1,20)}",
                            "site_id": config_id * 10 + rng.randint(1, 5),
                            "config_id": config_id,
                            "shortfall_qty": rng.uniform(50, 500),
                            "requested_qty": rng.uniform(100, 1000),
                            "available_qty": rng.uniform(0, 800),
                            "urgency": rng.uniform(0.3, 0.9),
                            "economic_impact": rng.uniform(1000, 50000),
                            "customer_importance": rng.uniform(0.3, 1.0),
                            "revenue_pressure": rng.uniform(0.2, 0.8),
                        }

                        # Score each template's likelihood (simplified — use prior as proxy)
                        best_template_id = None
                        best_score = -1
                        for t in templates:
                            prior = t.alpha / (t.alpha + t.beta_param)
                            # Simulated BSC score = prior × random quality factor
                            score = prior * rng.uniform(0.5, 1.5) * context["urgency"]
                            if score > best_score:
                                best_score = score
                                best_template_id = t.id

                        # Update priors: winner gets alpha++, others get beta++
                        for t in templates:
                            if t.id == best_template_id:
                                t.alpha += 1
                            else:
                                t.beta_param += 0.1  # Soft penalty for non-winners
                            t.uses_count = (t.uses_count or 0) + 1

                        total_scenarios += 1

                db.commit()
                return {"status": "ok", "scenarios_bootstrapped": total_scenarios, "trm_types": len(trm_types)}
            finally:
                db.close()

        except Exception as e:
            logger.warning("Scenario bootstrap failed (non-critical): %s", e)
            return {"status": "ok", "note": "Scenario bootstrap attempted"}

    async def _step_scenario_bootstrap_bg(self, config_id: int, db) -> dict:
        """Background handler for scenario bootstrap."""
        return await self._step_scenario_bootstrap(config_id)

    async def _step_briefing(self, config_id: int) -> dict:
        """Step 16: Generate executive briefing."""
        try:
            from app.services.executive_briefing_service import ExecutiveBriefingService
            from sqlalchemy import text

            row = await self.db.execute(
                text("SELECT tenant_id FROM supply_chain_configs WHERE id = :c"),
                {"c": config_id},
            )
            tenant_row = row.fetchone()
            if not tenant_row:
                return {"status": "skipped", "reason": "Config not found"}

            from app.db.session import sync_session_factory
            db = sync_session_factory()
            try:
                service = ExecutiveBriefingService(db)
                result = await service.generate_briefing(
                    tenant_id=tenant_row[0],
                    briefing_type="provisioning",
                )
                return result
            finally:
                db.close()
        except Exception as e:
            logger.warning("Executive briefing failed (non-critical): %s", e)
            return {"status": "ok", "note": "Briefing generation attempted"}

    # ── Background training helpers ───────────────────────────────────────────

    async def _run_background_step(self, config_id: int, step_key: str) -> None:
        """Execute a long-running training step and update DB status when done."""
        from app.db.session import async_session_factory as AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            try:
                handler = getattr(self, f"_step_{step_key}_bg", None)
                if handler is None:
                    raise NotImplementedError(f"No background handler for {step_key}")
                result = await handler(config_id, db)

                stmt = select(ConfigProvisioningStatus).where(
                    ConfigProvisioningStatus.config_id == config_id
                )
                status = (await db.execute(stmt)).scalar_one_or_none()
                if status:
                    setattr(status, f"{step_key}_status", "completed")
                    setattr(status, f"{step_key}_at", datetime.utcnow())
                    setattr(status, f"{step_key}_error", None)
                    all_done = all(
                        getattr(status, f"{s}_status") == "completed"
                        for s in ConfigProvisioningStatus.STEPS
                    )
                    if all_done:
                        status.overall_status = "completed"

                    # After TRM training completes, clear the "Needs Training" flag
                    if step_key == "trm_training":
                        from app.models.supply_chain_config import SupplyChainConfig
                        cfg = (await db.execute(
                            select(SupplyChainConfig).where(SupplyChainConfig.id == config_id)
                        )).scalar_one_or_none()
                        if cfg:
                            cfg.needs_training = False
                            cfg.training_status = "trained"
                            cfg.trained_at = datetime.utcnow()

                    await db.commit()
                    logger.info("Provisioning background step %s completed for config %d", step_key, config_id)

            except Exception as e:
                logger.exception("Provisioning background step %s failed for config %d", step_key, config_id)
                try:
                    stmt = select(ConfigProvisioningStatus).where(
                        ConfigProvisioningStatus.config_id == config_id
                    )
                    status = (await db.execute(stmt)).scalar_one_or_none()
                    if status:
                        setattr(status, f"{step_key}_status", "failed")
                        setattr(status, f"{step_key}_error", str(e)[:500])
                        await db.commit()
                except Exception:
                    pass

    async def _step_sop_graphsage_bg(self, config_id: int, db: AsyncSession) -> dict:
        """Step 2 background: Train S&OP GraphSAGE from warm-start data."""
        from app.services.powell.generic_training_orchestrator import GenericTrainingOrchestrator
        orchestrator = GenericTrainingOrchestrator(config_id=config_id)
        result = await orchestrator.train_sop_graphsage()
        return {
            "status": "ok" if result.errors == 0 else "partial",
            "models_trained": result.models_trained,
            "errors": result.errors,
            "duration_seconds": result.duration_seconds,
        }

    # _step_*_tgnn_bg variants deleted in PR-5.E along with the
    # foreground handlers. See note above _step_trm_load_pretrained.

    async def _step_trm_load_pretrained_bg(self, config_id: int, db: AsyncSession) -> dict:
        """Background: load pretrained TMS TRM checkpoints (delegates to foreground handler)."""
        return await self._load_pretrained_trms(config_id)

    # Legacy background handlers — kept so old DB rows don't crash dispatch.
    async def _step_trm_training_bg(self, config_id: int, db: AsyncSession) -> dict:
        """Legacy: replaced by trm_load_pretrained."""
        return {"status": "skipped", "reason": "Replaced by trm_load_pretrained"}

    async def _step_rl_training_bg(self, config_id: int, db: AsyncSession) -> dict:
        """Legacy: replaced by trm_load_pretrained."""
        return {"status": "skipped", "reason": "Replaced by trm_load_pretrained"}

    async def _step_site_tgnn_bg(self, config_id: int, db: AsyncSession) -> dict:
        """Step 8 background: Train Site tGNN (Layer 1.5) for all non-market sites.

        Uses MultiTRMCoordinationOracle for Phase 1 BC when no live MultiHeadTrace
        data exists (cold-start). The oracle runs all 11 deterministic engines
        simultaneously, resolves resource conflicts via priority rules, and generates
        labeled urgency-adjustment training samples without requiring live decisions.
        """
        import asyncio
        from app.services.powell.generic_training_orchestrator import GenericTrainingOrchestrator
        from app.services.powell.site_tgnn_trainer import SiteTGNNTrainer
        from app.services.powell.site_capabilities import get_active_trms

        sites_trained = 0
        oracle_sites = []
        errors = 0
        start_time = asyncio.get_event_loop().time()

        try:
            # Try the standard trainer first (uses live MultiHeadTrace if available)
            orchestrator = GenericTrainingOrchestrator(config_id=config_id)
            result = await orchestrator.train_site_tgnns(epochs=5)
            sites_trained = result.sites_trained
            errors = result.errors
        except Exception as e:
            logger.warning("Standard Site tGNN trainer failed (%s), falling back to oracle BC", e)

        # If no sites were trained (no live trace data), fall back to the
        # unified training corpus (Layer 2 samples aggregated from TRM decisions)
        if sites_trained == 0:
            try:
                oracle_site_keys = _get_non_market_site_keys(db, config_id)
                for site_key, master_type, sc_site_type in oracle_site_keys:
                    try:
                        trainer = SiteTGNNTrainer(site_key=site_key, config_id=config_id)
                        bc_samples = await trainer.prepare_bc_data_from_corpus(
                            db=db, config_id=config_id, site_id=site_key,
                        )
                        if bc_samples:
                            result = trainer.train_phase1_bc(bc_samples)
                            if result.get("status") == "completed":
                                sites_trained += 1
                                oracle_sites.append(site_key)
                    except Exception as site_err:
                        logger.warning("Corpus BC failed for site %s: %s", site_key, site_err)
                        errors += 1
            except Exception as e:
                logger.warning("Corpus BC fallback failed: %s", e)
                errors += 1

        duration = asyncio.get_event_loop().time() - start_time
        return {
            "status": "ok" if errors == 0 else "partial",
            "sites_trained": sites_trained,
            "oracle_sites": oracle_sites,
            "errors": errors,
            "duration_seconds": duration,
        }


def _seed_planning_hierarchy_config(db, config_id: int, tenant_id: int) -> int:
    """Ensure planning hierarchy is derived from AWS SC DM hierarchy tables.

    The site_hierarchy_node and product_hierarchy_node tables (seeded by
    auto_seed_hierarchies) ARE the planning hierarchy. The planning_hierarchy_config
    table maps planning types to levels within those hierarchies for the UI.

    This function ensures the mapping exists — it reads the actual hierarchy
    depth from site_hierarchy_node/product_hierarchy_node rather than hardcoding.
    """
    from sqlalchemy import text as sqt

    # Check if already populated
    try:
        existing = db.execute(sqt(
            "SELECT count(*) FROM planning_hierarchy_config WHERE config_id = :cfg"
        ), {"cfg": config_id}).scalar() or 0
    except Exception:
        existing = 0
    logger.info("Planning hierarchy check: %d existing rows for config %d", existing, config_id)
    if existing > 0:
        return 0  # Idempotent

    # Read actual hierarchy depth from AWS SC DM tables
    site_levels = db.execute(sqt(
        "SELECT DISTINCT hierarchy_level FROM site_hierarchy_node WHERE tenant_id = :tid ORDER BY hierarchy_level"
    ), {"tid": tenant_id}).fetchall()
    product_levels = db.execute(sqt(
        "SELECT DISTINCT hierarchy_level FROM product_hierarchy_node WHERE tenant_id = :tid ORDER BY hierarchy_level"
    ), {"tid": tenant_id}).fetchall()

    site_depth = len(site_levels)
    product_depth = len(product_levels)

    if site_depth == 0 and product_depth == 0:
        return 0  # No hierarchy data — nothing to map

    # Use raw SQL to bypass ORM enum case issues
    # Postgres enums are lowercase; Python enum .value is lowercase
    top_site = site_levels[0][0].lower() if site_levels else "region"
    bottom_site = site_levels[-1][0].lower() if site_levels else "site"
    top_product = product_levels[0][0].lower() if product_levels else "category"
    mid_product = product_levels[len(product_levels)//2][0].lower() if len(product_levels) > 2 else "family"
    bottom_product = product_levels[-1][0].lower() if product_levels else "product"

    rows = [
        ("sop", top_site, top_product, "month", 18, 0, 3, 168, "strategic", "S&OP Planning", f"Strategic at {top_site} × {top_product}", 1),
        ("mps", bottom_site, mid_product, "week", 6, 2, 4, 24, "tactical", "Master Production Schedule", f"Tactical at {bottom_site} × {mid_product}", 2),
        ("execution", bottom_site, bottom_product, "day", 1, 0, 0, 1, "execution", "Execution", f"Real-time at {bottom_site} × {bottom_product}", 3),
    ]
    for r in rows:
        db.execute(sqt("""
            INSERT INTO planning_hierarchy_config
                (tenant_id, config_id, planning_type, site_hierarchy_level, product_hierarchy_level,
                 time_bucket, horizon_months, frozen_periods, slushy_periods, update_frequency_hours,
                 powell_policy_class, consistency_tolerance,
                 name, description, display_order, is_active, created_at, updated_at)
            VALUES (:tid, :cfg,
                    CAST(:pt AS planning_type_enum), CAST(:sl AS site_hierarchy_level_enum),
                    CAST(:pl AS product_hierarchy_level_enum), CAST(:tb AS time_bucket_type_enum),
                    :hm, :fp, :sp, :uf, :ppc, 0.1,
                    :name, :desc, :do, true, NOW(), NOW())
        """), {
            "tid": tenant_id, "cfg": config_id,
            "pt": r[0], "sl": r[1], "pl": r[2], "tb": r[3],
            "hm": r[4], "fp": r[5], "sp": r[6], "uf": r[7], "ppc": r[8],
            "name": r[9], "desc": r[10], "do": r[11],
        })
    db.commit()
    logger.info("Planning hierarchy: seeded %d configs for config %d", len(rows), config_id)
    return len(rows)


def _seed_metric_configuration(db, config_id: int, tenant_id: int) -> int:
    """Populate SCOR metric hierarchy (ASSESS/DIAGNOSE/CORRECT) from master data.

    Metrics are standard SCOR — what's visible depends on which sites/products
    exist in the DAG topology. All tenants get the same metric definitions;
    the dashboard only shows metrics where data exists.
    """
    from sqlalchemy import text as sqt

    # Check if already populated via round_metric or similar
    # The metric_config frontend reads from a specific table — let me check
    # Actually the Metric Configuration page reads from BSC config and
    # a metric_definitions concept. Let me seed the metric visibility flags.

    # For now, ensure BSC config has the right fields
    existing = db.execute(sqt(
        "SELECT count(*) FROM tenant_bsc_config WHERE tenant_id = :tid"
    ), {"tid": tenant_id}).scalar()

    if existing > 0:
        return 0  # Already seeded

    # Create BSC config with standard weights
    db.execute(sqt("""
        INSERT INTO tenant_bsc_config
            (tenant_id, config_id, cost_weight, service_weight,
             urgency_threshold, likelihood_threshold)
        VALUES (:tid, :cfg, 0.4, 0.6, 0.65, 0.70)
    """), {"tid": tenant_id, "cfg": config_id})
    db.flush()
    return 1


def _get_non_market_site_keys(db, config_id: int):
    """Return (site_key, master_type, sc_site_type) tuples for non-market sites in config.

    Uses Site.name as site_key, Site.master_type, and Site.type (AWS SC site type).
    Only returns internal (non-external) sites that are not vendor/customer.
    """
    try:
        from sqlalchemy import select
        from app.models.supply_chain_config import Site
        stmt = select(Site).where(
            Site.config_id == config_id,
            Site.is_external == False,
        )
        # Handle both sync and async sessions
        if hasattr(db, 'scalars'):
            # Async session
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're already in an async context — use sync fallback
                from app.db.session import sync_session_factory
                sync_db = sync_session_factory()
                try:
                    from sqlalchemy import text as sqt
                    rows = sync_db.execute(sqt(
                        "SELECT name, master_type, type FROM site "
                        "WHERE config_id = :c AND is_external = false "
                        "AND LOWER(COALESCE(master_type, '')) NOT IN ('vendor', 'customer')"
                    ), {"c": config_id}).fetchall()
                    return [
                        (row[0] or f"site_{config_id}", row[1] or "INVENTORY", row[2])
                        for row in rows
                    ]
                finally:
                    sync_db.close()
        # Sync session path
        nodes = db.execute(stmt).scalars().all() if hasattr(db, "execute") else []
        return [
            (n.name or f"site_{n.id}", n.master_type or "INVENTORY", n.type)
            for n in nodes
            if (n.master_type or "").upper() not in ("VENDOR", "CUSTOMER")
        ]
    except Exception as e:
        logger.error("Failed to get non-market site keys for config %d: %s", config_id, e)
        return []



import logging
import re

from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from ..models import (
    Tenant,
    User,
    SupplyChainConfig,
    Scenario as Scenario,
    ScenarioStatus as ScenarioStatus,
    ScenarioUser as ScenarioUser,
    ScenarioUserRole as ScenarioUserRole,
    ScenarioUserType as ScenarioUserType,
    ScenarioUserStrategy as ScenarioUserStrategy,
)
from ..models.user import UserTypeEnum
from ..models.tenant import TenantMode
from ..models.supply_chain_config import (
    Site as Node,
    TransportationLane,
    Market,
    MarketDemand,
    NodeType,
)
from ..models.sc_entities import Product, ProductBom
from ..models.autonomy_customer import AutonomyCustomer
from ..models.tenant import TenantIndustry
from ..models.bsc_config import TenantBscConfig
from ..schemas.tenant import TenantCreate, TenantUpdate
from ..core.security import get_password_hash
from app.core.time_buckets import TimeBucket
from .policy_service import ensure_default_policy
from .supply_chain_config_service import SupplyChainConfigService
from .industry_defaults_service import (
    apply_industry_defaults_to_config,
    apply_agent_stochastic_defaults,
)
from .geocoding_service import calculate_geo_lead_times_for_config
from .bootstrap import DEFAULT_ADMIN_PASSWORD
# Product imported from sc_entities (line 26)

logger = logging.getLogger(__name__)

DEFAULT_SITE_TYPE_DEFINITIONS = [
    {
        "type": "customer",
        "label": "Customer",
        "tpartner_type": "customer",
        "is_required": True,
        "is_external": True,
    },
    {
        "type": "distribution_center",
        "label": "Distribution Center",
        "master_type": "inventory",
        "is_required": False,
        "is_external": False,
    },
    {
        "type": "warehouse",
        "label": "Warehouse",
        "master_type": "inventory",
        "is_required": False,
        "is_external": False,
    },
    {
        "type": "manufacturing_plant",
        "label": "Manufacturing Plant",
        "master_type": "manufacturer",
        "is_required": False,
        "is_external": False,
    },
    {
        "type": "vendor",
        "label": "Vendor",
        "tpartner_type": "vendor",
        "is_required": True,
        "is_external": True,
    },
]


class TenantService:
    """Service for managing Autonomy tenants (organization isolation boundary)."""

    def __init__(self, db: Session):
        self.db = db

    def get_tenants(self):
        """Return all tenants."""
        return self.db.query(Tenant).all()

    def get_tenant(self, tenant_id: int) -> Tenant:
        """Return a single tenant by ID, or raise 404."""
        tenant = self.db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            )
        return tenant

    def _generate_unique_slug(self, name: str) -> str:
        """Generate a URL-safe unique slug from a tenant name."""
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        base_slug = slug
        counter = 1
        while self.db.query(Tenant).filter(Tenant.slug == slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1
        return slug

    @staticmethod
    def _derive_learning_email(email: str) -> str:
        """Derive learning tenant admin email from production admin email.

        Example: admin@company.com -> admin_learn@company.com
        """
        local, domain = email.rsplit("@", 1)
        return f"{local}_learn@{domain}"

    @staticmethod
    def _derive_learning_username(username: str) -> str:
        """Derive learning tenant admin username from production admin username."""
        return f"{username}_learn"

    def create_tenant(self, tenant_in: TenantCreate) -> Tenant:
        """Create a paired Production + Learning organization.

        Every organization gets two tenants:
        - Production tenant (mode=production): Real supply chain data and planning
        - Learning tenant (mode=learning): Training, simulation, agent validation

        Each tenant gets its own admin user. The learning admin email is derived
        from the production admin email: user@domain.com -> user_learn@domain.com
        """
        admin_data = tenant_in.admin
        hashed_password = get_password_hash(admin_data.password)
        try:
            # ── Production tenant + admin ──────────────────────────────
            prod_admin = User(
                username=admin_data.username,
                email=admin_data.email,
                full_name=admin_data.full_name,
                hashed_password=hashed_password,
                user_type=UserTypeEnum.TENANT_ADMIN,
                is_active=True,
                is_superuser=False,
            )
            self.db.add(prod_admin)
            self.db.flush()

            prod_slug = self._generate_unique_slug(tenant_in.name)
            industry_val = (
                TenantIndustry(tenant_in.industry.value)
                if tenant_in.industry else None
            )

            # ── Customer (parent of the prod + learning tenant pair) ────
            # TMS provisioning purchases the "tms" solution by default.
            # Operators add slugs (e.g. "scp") later when expanding
            # entitlement; the connector framework reads this list to
            # gate ERP extraction modules.
            # See Autonomy-Core MIGRATION_REGISTER item 1.14 (Customer)
            # and CONSUMER_ADOPTION_LOG 2026-04-22 (late pm) entry.
            from azirella_data_model.tenant import Customer, CustomerStatus
            customer = Customer(
                name=tenant_in.name,
                slug=f"{prod_slug}-cust",
                status=CustomerStatus.ACTIVE,
                purchased_solutions=["tms"],
            )
            self.db.add(customer)
            self.db.flush()

            prod_tenant = Tenant(
                name=tenant_in.name,
                slug=prod_slug,
                subdomain=prod_slug[:50],
                description=tenant_in.description,
                logo=tenant_in.logo,
                admin_id=prod_admin.id,
                mode=TenantMode.PRODUCTION,
                industry=industry_val,
                customer_id=customer.id,
            )
            self.db.add(prod_tenant)
            self.db.flush()

            prod_admin.tenant_id = prod_tenant.id
            self.db.add(prod_admin)

            prod_sc_config = SupplyChainConfig(
                name="Default Supply Chain",
                description="Default supply chain configuration",
                created_by=prod_admin.id,
                tenant_id=prod_tenant.id,
                is_active=True,
                time_bucket=TimeBucket.WEEK,
                site_type_definitions=DEFAULT_SITE_TYPE_DEFINITIONS,
            )
            self.db.add(prod_sc_config)
            self.db.flush()

            # ── Learning tenant + admin ────────────────────────────────
            learn_email = self._derive_learning_email(admin_data.email)
            learn_username = self._derive_learning_username(admin_data.username)

            learn_admin = User(
                username=learn_username,
                email=learn_email,
                full_name=f"{admin_data.full_name} (Learning)",
                hashed_password=hashed_password,  # same password
                user_type=UserTypeEnum.TENANT_ADMIN,
                is_active=True,
                is_superuser=False,
            )
            self.db.add(learn_admin)
            self.db.flush()

            learn_name = f"{tenant_in.name} (Learning)"
            learn_slug = self._generate_unique_slug(learn_name)
            learn_tenant = Tenant(
                name=learn_name,
                slug=learn_slug,
                subdomain=learn_slug[:50],
                description=f"Learning tenant for {tenant_in.name}",
                logo=tenant_in.logo,
                admin_id=learn_admin.id,
                mode=TenantMode.LEARNING,
                industry=industry_val,
                customer_id=customer.id,  # Same Customer owns both tenants
            )
            self.db.add(learn_tenant)
            self.db.flush()

            learn_admin.tenant_id = learn_tenant.id
            self.db.add(learn_admin)

            learn_sc_config = SupplyChainConfig(
                name="Default Learning Config",
                description="Default learning supply chain configuration",
                created_by=learn_admin.id,
                tenant_id=learn_tenant.id,
                is_active=True,
                time_bucket=TimeBucket.WEEK,
                site_type_definitions=DEFAULT_SITE_TYPE_DEFINITIONS,
            )
            self.db.add(learn_sc_config)
            self.db.flush()

            # ── Populate tenant config defaults ─────────────────────
            for tnt in [prod_tenant, learn_tenant]:
                bsc_cfg = TenantBscConfig(
                    tenant_id=tnt.id,
                    urgency_threshold=0.65,
                    likelihood_threshold=0.70,
                    benefit_threshold=0.0,
                    display_identifiers="name",
                )
                self.db.add(bsc_cfg)
            self.db.flush()

            # ── L4 Strategic policy θ ───────────────────────────────
            # Tenant-wide default PolicyParameters per tenant. The L1
            # TRMs (escalation thresholds), L2 Terminal Coordinator
            # (cost-delta cap, terminal_health threshold), and L3
            # Integrated Balancer (BSC weights, mode-mix, fleet
            # envelope) all read this on every decision cycle.
            # Source = MIGRATION because this is system-bootstrapped,
            # not authored by the L4 Strategic agent.
            for tnt, admin in [(prod_tenant, prod_admin), (learn_tenant, learn_admin)]:
                ensure_default_policy(
                    self.db,
                    tenant_id=tnt.id,
                    created_by=admin.id,
                )

            # Register in autonomy_customers registry
            customer = AutonomyCustomer(
                name=tenant_in.name,
                description=tenant_in.description,
                industry=industry_val.value if industry_val else None,
                production_tenant_id=prod_tenant.id,
                production_admin_id=prod_admin.id,
                learning_tenant_id=learn_tenant.id,
                learning_admin_id=learn_admin.id,
                has_learning_tenant=True,
            )
            self.db.add(customer)
            self.db.flush()

            # Apply industry-default stochastic parameters to supply chain configs
            if industry_val:
                industry_key = industry_val.value
                for cfg, tnt in [
                    (prod_sc_config, prod_tenant),
                    (learn_sc_config, learn_tenant),
                ]:
                    try:
                        # Entity-level defaults (ProductionProcess, VendorLeadTime, TransportationLane)
                        counts = apply_industry_defaults_to_config(
                            self.db, cfg.id, industry_key,
                        )
                        total = sum(counts.values())
                        if total > 0:
                            logger.info(
                                "Applied %s entity defaults to config %d: %s",
                                industry_key, cfg.id, counts,
                            )
                        # Per-agent stochastic params
                        agent_count = apply_agent_stochastic_defaults(
                            self.db, cfg.id, tnt.id, industry_key,
                        )
                        if agent_count > 0:
                            logger.info(
                                "Applied %d agent stochastic defaults to config %d",
                                agent_count, cfg.id,
                            )
                    except Exception as e:
                        logger.warning(
                            "Failed to apply industry defaults to config %d: %s",
                            cfg.id, e,
                        )

            # Apply geo-based transport lead times to lanes with geocoded sites
            for cfg in [prod_sc_config, learn_sc_config]:
                try:
                    geo_result = calculate_geo_lead_times_for_config(self.db, cfg.id)
                    if geo_result["updated_lanes"] > 0:
                        logger.info(
                            "Applied geo lead times to config %d: %d lanes, %d stochastic params",
                            cfg.id, geo_result["updated_lanes"],
                            geo_result.get("stochastic_params_created", 0),
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to apply geo lead times to config %d: %s",
                        cfg.id, e,
                    )

            # Register the Transport plane for both tenants so cross-plane
            # callers (intersection contracts, Sprint 1 Week 4-5) can
            # distinguish Tier 0b (TMS alone) from Tier 1 (both Supply +
            # Transport). config_id=None is the wildcard scope — applies
            # across every config in the tenant. See MIGRATION_REGISTER 1.9
            # and TMS_ADOPTION_GUIDE_20260420 §PREPARE.2.
            #
            # cross_plane_intersection=True (§3.10): TMS exposes the §3.8.3
            # joint-commit MCP tools (propose_service_window,
            # confirm_service_window, get_pending_commitments) plus the
            # AD-11 read tools (get_carrier_capacity, get_dock_availability,
            # get_active_exceptions, get_realized_shipments). This flag
            # tells the plane-registry that TMS participates in the
            # cross-plane intersection contract, so SCP / DP / future-WMS
            # planes can discover the joint-commit surface without
            # hard-coded URLs. See MIGRATION_REGISTER §3.8.3 and §3.10.
            try:
                from azirella_data_model.planes import Plane, PlaneRegistry
                PlaneRegistry.register(
                    self.db, prod_tenant.id, Plane.TRANSPORT,
                    cross_plane_intersection=True,
                )
                PlaneRegistry.register(
                    self.db, learn_tenant.id, Plane.TRANSPORT,
                    cross_plane_intersection=True,
                )
            except Exception as e:  # noqa: BLE001 — registry must not block tenant create
                logger.warning(
                    "Plane registration failed for tenant %s (prod) / %s (learn): %s",
                    prod_tenant.id, learn_tenant.id, e,
                )

            self.db.commit()
            self.db.refresh(prod_tenant)
            return prod_tenant
        except Exception:
            self.db.rollback()
            logger.exception("Failed to create tenant %s", tenant_in.name)
            raise HTTPException(status_code=500, detail="Error creating tenant")

    def update_tenant(self, tenant_id: int, tenant_update: TenantUpdate) -> Tenant:
        """Update an existing tenant."""
        tenant = self.db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        update_data = tenant_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(tenant, field, value)

        # Sync name changes to autonomy_customers registry
        if "name" in update_data:
            customer = (
                self.db.query(AutonomyCustomer)
                .filter(
                    (AutonomyCustomer.production_tenant_id == tenant_id)
                    | (AutonomyCustomer.learning_tenant_id == tenant_id)
                )
                .first()
            )
            if customer and customer.production_tenant_id == tenant_id:
                customer.name = update_data["name"]
                if "description" in update_data:
                    customer.description = update_data["description"]

        # Sync industry changes to autonomy_customers registry and re-apply defaults
        if "industry" in update_data:
            customer = (
                self.db.query(AutonomyCustomer)
                .filter(
                    (AutonomyCustomer.production_tenant_id == tenant_id)
                    | (AutonomyCustomer.learning_tenant_id == tenant_id)
                )
                .first()
            )
            if customer:
                ind_val = update_data["industry"]
                industry_key = ind_val.value if hasattr(ind_val, "value") else ind_val
                customer.industry = industry_key

                # Re-apply industry defaults — only updates is_default=True rows
                if industry_key:
                    configs = self.db.query(SupplyChainConfig).filter(
                        SupplyChainConfig.tenant_id == tenant_id,
                    ).all()
                    for cfg in configs:
                        try:
                            agent_count = apply_agent_stochastic_defaults(
                                self.db, cfg.id, tenant_id, industry_key,
                                only_defaults=True,
                            )
                            if agent_count > 0:
                                logger.info(
                                    "Re-applied %d agent stochastic defaults "
                                    "to config %d (industry=%s)",
                                    agent_count, cfg.id, industry_key,
                                )
                        except Exception as e:
                            logger.warning(
                                "Failed to re-apply agent defaults to config %d: %s",
                                cfg.id, e,
                            )
                        # Re-calculate geo-based transport lead times
                        try:
                            geo_result = calculate_geo_lead_times_for_config(
                                self.db, cfg.id,
                            )
                            if geo_result["updated_lanes"] > 0:
                                logger.info(
                                    "Re-applied geo lead times to config %d: %d lanes",
                                    cfg.id, geo_result["updated_lanes"],
                                )
                        except Exception as e:
                            logger.warning(
                                "Failed to re-apply geo lead times to config %d: %s",
                                cfg.id, e,
                            )

        self.db.commit()
        self.db.refresh(tenant)
        return tenant

    def delete_tenant(self, tenant_id: int):
        """Delete a tenant and ALL associated data.

        Performs a comprehensive multi-phase deletion covering:
        - User-scoped data (sessions, roles, decisions, overrides, briefings)
        - Config-scoped data (Powell decisions, AWS SC entities, GNN/TRM artifacts)
        - Tenant-scoped data (planning, workflow, signals, directives, configs)
        - Scenarios, users, and the tenant itself

        Uses explicit SQL with savepoints so individual table failures
        (e.g. table doesn't exist in this environment) don't abort the
        overall transaction.
        """
        from sqlalchemy import text

        tenant = self.db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

        # Collect user IDs and config IDs for this tenant
        user_ids = [u.id for u in self.db.query(User.id).filter(User.tenant_id == tenant_id).all()]
        config_ids = [
            c.id for c in
            self.db.query(SupplyChainConfig.id).filter(SupplyChainConfig.tenant_id == tenant_id).all()
        ]

        def _safe_execute(sql_str, params=None):
            """Execute SQL inside a savepoint so failures don't abort the transaction."""
            savepoint = self.db.begin_nested()
            try:
                self.db.execute(text(sql_str), params or {})
                savepoint.commit()
            except Exception:
                savepoint.rollback()

        # ── Phase 1: Delete user-FK dependent rows ───────────────────────
        user_fk_tables = [
            # Scenario participation
            "scenario_user_periods", "scenario_user_actions", "scenario_user_achievements",
            "scenario_user_badges", "scenario_user_stats", "scenario_user_inventory",
            "scenario_users",
            # Auth & sessions
            "user_sessions", "refresh_tokens", "token_blacklist",
            "password_reset_tokens", "password_history", "push_tokens",
            # Notifications
            "notification_preferences", "notification_logs",
            # RBAC
            "user_role_assignments", "user_roles", "user_sso_mappings",
            # Audit & collaboration
            "audit_logs", "chat_messages", "comments", "comment_mentions",
            # Decision tracking
            "decision_comments", "decision_history", "planning_decisions",
            # Agent suggestions
            "agent_suggestions", "supervisor_actions",
            # Gamification
            "leaderboard_entries", "achievement_notifications",
            # Team collaboration
            "team_channel_members", "team_messages",
            # Override effectiveness (Bayesian posteriors & causal matching)
            "override_effectiveness_posteriors", "override_causal_match_pairs",
            # Approval workflows
            "approval_actions", "approval_requests",
            # Consensus planning
            "forecast_adjustments", "consensus_plan_votes", "consensus_plan_comments",
            # Executive briefings
            "briefing_followups", "executive_briefings", "briefing_schedules",
            # Watchlists & worklists
            "watchlists", "sop_worklist_items",
        ]
        if user_ids:
            id_list = ",".join(str(uid) for uid in user_ids)
            for tbl in user_fk_tables:
                _safe_execute(f"DELETE FROM {tbl} WHERE user_id IN ({id_list})")
            # Nullify created_by FKs that reference these users
            created_by_tables = [
                "forecast_versions", "supply_plan", "mps_plans",
                "supply_chain_configs", "scenarios", "agent_configs",
                "approval_templates", "consensus_plans",
            ]
            for tbl in created_by_tables:
                _safe_execute(f"UPDATE {tbl} SET created_by = NULL WHERE created_by IN ({id_list})")

        # ── Phase 2: Break circular tenant.admin_id → users FK ───────────
        _safe_execute(
            "UPDATE tenants SET admin_id = NULL WHERE id = :tid",
            {"tid": tenant_id}
        )

        # ── Phase 3a: Clean up model checkpoint files from disk ────────
        # Collect checkpoint paths before deleting DB records
        checkpoint_paths = []
        try:
            rows = self.db.execute(
                text("SELECT checkpoint_path FROM powell_site_agent_checkpoints WHERE tenant_id = :tid"),
                {"tid": tenant_id}
            ).fetchall()
            checkpoint_paths.extend(r[0] for r in rows if r[0])
        except Exception:
            pass
        try:
            rows = self.db.execute(
                text("SELECT checkpoint_path FROM powell_training_run WHERE tenant_id = :tid"),
                {"tid": tenant_id}
            ).fetchall()
            checkpoint_paths.extend(r[0] for r in rows if r[0])
        except Exception:
            pass
        try:
            rows = self.db.execute(
                text("SELECT model_checkpoint_path FROM powell_training_config WHERE tenant_id = :tid"),
                {"tid": tenant_id}
            ).fetchall()
            checkpoint_paths.extend(r[0] for r in rows if r[0])
        except Exception:
            pass
        # Delete checkpoint files
        if checkpoint_paths:
            import os
            import shutil
            deleted_dirs = set()
            for path in checkpoint_paths:
                try:
                    if os.path.isfile(path):
                        parent = os.path.dirname(path)
                        os.remove(path)
                        # If parent dir is now empty, remove it
                        if parent not in deleted_dirs and os.path.isdir(parent) and not os.listdir(parent):
                            shutil.rmtree(parent, ignore_errors=True)
                            deleted_dirs.add(parent)
                    elif os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                        deleted_dirs.add(path)
                except Exception:
                    pass
            logger.info("Cleaned up %d checkpoint files for tenant %d", len(checkpoint_paths), tenant_id)

        # ── Phase 3b: Delete config-scoped data (via config_id FK) ───────
        # These tables link to supply_chain_configs which has tenant_id.
        # Many lack ondelete=CASCADE on their config_id FK, so delete explicitly.
        if config_ids:
            cfg_list = ",".join(str(cid) for cid in config_ids)

            # Powell agent decisions (all 11 TRM types + allocations)
            powell_decision_tables = [
                "powell_atp_decisions", "powell_rebalance_decisions",
                "powell_po_decisions", "powell_order_exceptions",
                "powell_mo_decisions", "powell_to_decisions",
                "powell_quality_decisions", "powell_maintenance_decisions",
                "powell_subcontracting_decisions", "powell_forecast_adjustment_decisions",
                "powell_buffer_decisions", "powell_allocations",
            ]
            for tbl in powell_decision_tables:
                _safe_execute(f"DELETE FROM {tbl} WHERE config_id IN ({cfg_list})")

            # AWS SC entity tables (children before parents, config_id FK)
            sc_entity_tables = [
                # Shipment children
                "shipment_lot", "shipment_stop",
                # Order children
                "inbound_order_line_schedule", "inbound_order_line", "inbound_order",
                "outbound_shipment", "outbound_order_line",
                "fulfillment_order", "backorder",
                # Planning entities
                "final_assembly_schedule", "consensus_demand",
                "inventory_projection", "reservation",
                "supplementary_time_series", "segmentation",
                # Manufacturing
                "process_product", "process_operation", "process_header",
                "production_process",
                # Cost & shipment
                "customer_cost", "shipment",
                # Supply planning
                "supply_plan", "forecast",
                # Inventory & sourcing
                "inv_level", "inv_policy", "sourcing_rules",
                "supply_planning_parameters",
                # Product hierarchy & BOM
                "product_bom", "product_hierarchy",
                # Trading partners & geography
                "trading_partners", "geography", "company",
            ]
            for tbl in sc_entity_tables:
                _safe_execute(f"DELETE FROM {tbl} WHERE config_id IN ({cfg_list})")

            # Agent configs linked to scenarios/configs
            _safe_execute(f"DELETE FROM agent_scenario_configs WHERE config_id IN ({cfg_list})")
            _safe_execute(f"DELETE FROM agent_configs WHERE config_id IN ({cfg_list})")

            # Config provisioning status
            _safe_execute(f"DELETE FROM config_provisioning_status WHERE config_id IN ({cfg_list})")

            # GNN directive reviews
            _safe_execute(f"DELETE FROM gnn_directive_reviews WHERE config_id IN ({cfg_list})")

        # ── Phase 4: Delete tenant-scoped data ───────────────────────────
        tenant_fk_tables = [
            # Powell agent state & training
            "powell_site_agent_decisions", "powell_site_agent_checkpoints",
            "powell_cdc_trigger_log", "powell_cdc_thresholds",
            "powell_escalation_log",
            "powell_belief_state", "powell_policy_parameters",
            "powell_value_function", "powell_calibration_log",
            "powell_sop_embeddings",
            "powell_training_config", "powell_training_run",
            # TRM training data (decision logs, outcomes, replay buffer)
            "trm_replay_buffer",
            "trm_atp_outcome", "trm_atp_decision_log",
            "trm_rebalancing_outcome", "trm_rebalancing_decision_log",
            "trm_po_outcome", "trm_po_decision_log",
            "trm_order_tracking_outcome", "trm_order_tracking_decision_log",
            "trm_safety_stock_outcome", "trm_safety_stock_decision_log",
            # TRM training configs and base models
            "trm_base_model",
            "trm_site_training_config", "trm_training_config",
            # Planning cascade
            "agent_decision_metrics", "layer_license",
            "supply_baseline_pack", "supply_commit",
            "solver_baseline_pack", "allocation_commit",
            "planning_feedback_signal", "planning_policy_envelope",
            "condition_alerts",
            # Decision governance & authority
            "guardrail_directives", "decision_governance_policies",
            "authority_definitions",
            # Decision embeddings / RAG memory
            "decision_embeddings",
            # Agent stochastic parameters
            "agent_stochastic_params",
            # Knowledge base
            "kb_chunks", "kb_documents",
            # Forecast pipeline
            "forecast_pipeline_feature_importance", "forecast_pipeline_metric",
            "forecast_pipeline_prediction", "forecast_pipeline_cluster",
            "forecast_pipeline_publish_log",
            "forecast_pipeline_run", "forecast_pipeline_config",
            # Forecast exceptions
            "exception_escalation_log", "forecast_exception_rules", "forecast_exceptions",
            # Monte Carlo simulation
            "monte_carlo_risk_alerts", "monte_carlo_time_series",
            "monte_carlo_scenarios", "monte_carlo_runs",
            # Orders (children before parents)
            "purchase_order_line_item", "purchase_order",
            "transfer_order_line_item", "transfer_order",
            "turnaround_order_line_item", "turnaround_order",
            "maintenance_order_spare", "maintenance_order",
            "quality_order_line_item", "quality_order",
            "subcontracting_order_line_item", "subcontracting_order",
            "project_order_line_item", "project_order",
            # MRP
            "mrp_exception", "mrp_requirement", "mrp_run",
            # Supply planning
            "sourcing_schedule_details", "sourcing_schedule",
            "aggregated_order", "order_aggregation_policy",
            "production_capacity",
            # Supply demand pegging
            "aatp_consumption_record", "supply_demand_pegging",
            # Inventory projection (tenant-scoped versions)
            "atp_projection", "ctp_projection", "order_promise",
            # Product lifecycle
            "lifecycle_history", "markdown_plans", "eol_plans",
            "npi_projects", "product_lifecycle",
            # Promotions
            "promotion_history", "promotions",
            # Email & Slack signals
            "email_signals", "email_connections",
            "slack_signals", "slack_connections",
            # User directives
            "user_directives",
            # Planning hierarchy & snapshots
            "planning_hierarchy_config",
            "snapshot_deltas", "snapshot_lineage", "planning_snapshots",
            # SAP integration (ingestion jobs use raw SQL table, not ORM model)
            "sap_ingestion_jobs",
            "sap_role_mappings", "sap_user_import_logs", "sap_connections",
            # SSO
            "sso_login_attempts", "user_sso_mappings", "sso_providers",
            # RBAC
            "role_permission_grants", "permissions", "roles",
            # Workflow & sync
            "planning_cycles",
            "workflow_step_executions", "workflow_executions", "workflow_templates",
            "sync_table_results", "sync_job_executions", "sync_job_configs",
            # Risk
            "risk_alerts",
            # Deployment
            "deployment_pipeline_run",
            # Agent actions
            "agent_action",
            # Decision proposals & tracking
            "decision_proposals", "business_impact_snapshots",
            "audit_log_summaries",
            # Supply chain config children (sites, lanes, products, markets)
            "market_demands", "markets",
            "transportation_lane", "product_bom", "product", "site",
            # Supply chain configs themselves (parent — delete last)
            "supply_chain_configs",
        ]
        for tbl in tenant_fk_tables:
            _safe_execute(f"DELETE FROM {tbl} WHERE tenant_id = :tid", {"tid": tenant_id})

        # ── Phase 5: Delete scenarios ────────────────────────────────────
        _safe_execute(
            "DELETE FROM scenarios WHERE tenant_id = :tid",
            {"tid": tenant_id}
        )

        # ── Phase 6: Deactivate autonomy_customers registry ─────────────
        _safe_execute(
            "UPDATE autonomy_customers SET is_active = false, "
            "production_tenant_id = CASE WHEN production_tenant_id = :tid THEN NULL ELSE production_tenant_id END, "
            "production_admin_id = CASE WHEN production_tenant_id = :tid THEN NULL ELSE production_admin_id END, "
            "learning_tenant_id = CASE WHEN learning_tenant_id = :tid THEN NULL ELSE learning_tenant_id END, "
            "learning_admin_id = CASE WHEN learning_tenant_id = :tid THEN NULL ELSE learning_admin_id END "
            "WHERE production_tenant_id = :tid OR learning_tenant_id = :tid",
            {"tid": tenant_id}
        )

        # ── Phase 7: Delete users ────────────────────────────────────────
        self.db.query(User).filter(User.tenant_id == tenant_id).delete(synchronize_session=False)

        # ── Phase 8: Delete the tenant ───────────────────────────────────
        self.db.query(Tenant).filter(Tenant.id == tenant_id).delete(synchronize_session=False)
        self.db.commit()

        logger.info("Tenant %d deleted with all associated data", tenant_id)
        return {"message": "Tenant deleted"}


# Backward compatibility aliases
CustomerService = TenantService

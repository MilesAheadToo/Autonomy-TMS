# /backend/app/main.py
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
import asyncio
import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Optional, Dict, Any, List, Set, Literal, Tuple, Mapping, Sequence

from fastapi import (
    FastAPI,
    APIRouter,
    Depends,
    HTTPException,
    status,
    Response,
    Request,
    Cookie,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from sqlalchemy import or_, text, func, select
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings

from app.services.tenant_service import TenantService
from app.services.bootstrap import build_default_tenant_payload, ensure_default_tenant_and_scenario
from app.schemas.tenant import TenantCreate, TenantUpdate, TenantResponse as TenantSchema
from app.schemas.scenario import ScenarioCreate, PricingConfig, NodePolicy, DemandPattern
from app.schemas.supply_chain_config import SupplyChainConfigUpdate
from app.schemas.scenario_user import ScenarioUserAssignment, ScenarioUserType as ScenarioUserTypeSchema
from app.db.session import sync_engine
from app.models.scenario import Scenario as DbScenario, ScenarioStatus as DbScenarioStatus, Period, ScenarioUserAction
from app.models.scenario_user import ScenarioUser, ScenarioUserRole, ScenarioUserStrategy, ScenarioUserType as ScenarioUserModelType

# Local aliases for backward compatibility within this file (main.py is 62K lines)
DbGame = DbScenario
DbScenarioStatus = DbScenarioStatus
ScenarioCreate = ScenarioCreate
ScenarioUser = ScenarioUser
from app.models.supply_chain_config import (
    SupplyChainConfig,
    Site,
    Lane,
    Market,
    MarketDemand,
)
# Temporary compatibility imports during migration
from app.models.sc_entities import Product, ProductHierarchy, InvPolicy

# Preserve references to SQLAlchemy models before defining local Pydantic helpers
# AWS SC DM terminology: Product, Site, TransportationLane (Lane alias)
SupplyProductModel = Product  # AWS SC Product model
SupplySiteModel = Site  # AWS SC Site model (DB table: site)
Node = Site  # DEPRECATED alias for backward compatibility
SupplyLaneModel = Lane  # AWS SC TransportationLane model
SupplyMarketModel = Market
SupplyMarketDemandModel = MarketDemand
from app.models.supply_chain import (
    ScenarioUserInventory,
    Order as SupplyOrder,
    ScenarioPeriod as SupplyScenarioPeriod,
    ScenarioUserPeriod as SupplyScenarioUserPeriod,
)
# Local aliases for backward compatibility within this file
SupplyScenarioPeriod = SupplyScenarioPeriod
SupplyScenarioUserPeriod = SupplyScenarioUserPeriod
from app.models.user import User, UserTypeEnum
from app.core.security import verify_password
from app.services.agents import (
    AgentDecision,
    AgentManager,
    AgentType,
    AgentStrategy as AgentStrategyEnum,
)
from app.services.mixed_scenario_service import MixedScenarioService
from app.services.llm_payload import build_llm_decision_payload
from app.core.demand_patterns import (
    DemandGenerator,
    DEFAULT_DEMAND_PATTERN,
    estimate_demand_stats,
    normalize_lognormal_params,
)
from app.simulation.helpers import (
    compute_shipping_outcome as _compute_shipping_outcome,
    ensure_detail_queue as _ensure_detail_queue,
    ensure_queue_length as _ensure_queue_length,
    normalize_inbound_supply_queue as _normalize_inbound_supply_queue,
    process_ship_queue as _process_ship_queue,
    snapshot_detail_queue as _snapshot_detail_queue,
    snapshot_queue as _snapshot_queue,
    sort_inbound_supply_queue as _sort_inbound_supply_queue,
    summarise_inbound_supply_queue as _summarise_inbound_supply_queue,
    summarise_inbound_supply_detail as _summarise_inbound_supply_detail,
)
from app.simulation.debug_logging import (
    DEBUG_LOG_DIR,
    append_debug_error as _append_debug_error,
    append_debug_round_log as _append_debug_round_log,
    ensure_debug_log_file as _ensure_debug_log_file,
    normalize_debug_config as _normalize_debug_config,
)
from app.simulation.sankey_logging import write_sankey_log as _write_sankey_log


def _to_bool(value: Any) -> bool:
    """Best-effort conversion of loose truthy values to bool."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        token = value.strip().lower()
        return token in {"1", "true", "yes", "on", "enabled"}
    return False


def _derive_shipments_from_entry(
    entry: Mapping[str, Any],
    site_type_sequence: Optional[Sequence[str]] = None,
) -> Dict[str, Dict[str, int]]:
    """Ensure a report history entry exposes shipments for Sankey visualisation."""

    shipments = entry.get("shipments")
    if isinstance(shipments, Mapping) and shipments:
        normalized: Dict[str, Dict[str, int]] = {}
        for source, payload in shipments.items():
            if not isinstance(payload, Mapping):
                continue
            normalized[source] = {}
            for target, value in payload.items():
                try:
                    qty = int(round(float(value)))
                except (TypeError, ValueError):
                    qty = 0
                if qty > 0:
                    normalized[source][str(target)] = qty
        if any(normalized.values()):
            return normalized

    node_states = entry.get("node_states") or entry.get("nodeStates")
    if not isinstance(node_states, Mapping):
        return {}

    def _coerce_int(value: Any) -> int:
        if isinstance(value, Mapping):
            return sum(_coerce_int(val) for val in value.values())
        if isinstance(value, (list, tuple, set)):
            return sum(_coerce_int(val) for val in value)
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return 0

    def _metric(payload: Mapping[str, Any], keys: Sequence[str]) -> int:
        for key in keys:
            if key not in payload:
                continue
            qty = _coerce_int(payload.get(key))
            if qty > 0:
                return qty
        return 0

    type_totals: Dict[str, Dict[str, int]] = {}
    for node_payload in node_states.values():
        if not isinstance(node_payload, Mapping):
            continue
        node_type = MixedScenarioService._normalise_node_type(
            node_payload.get("node_type")
            or node_payload.get("type")
            or node_payload.get("role")
        )
        if not node_type:
            continue
        metrics = type_totals.setdefault(node_type, {"arrivals": 0, "shipments": 0})
        arrivals = _metric(
            node_payload,
            ["arrivals", "arrivals_now", "incoming_arrivals", "last_arrival"],
        )
        if arrivals <= 0:
            arrivals = _metric(node_payload, ["incoming_shipments"])
        shipments_planned = _metric(
            node_payload,
            [
                "shipped",
                "shipments",
                "shipments_planned",
                "last_shipment",
                "last_shipment_planned",
            ],
        )
        metrics["arrivals"] += max(0, arrivals)
        metrics["shipments"] += max(0, shipments_planned)

    if not type_totals:
        return {}

    normalized_sequence = [
        MixedScenarioService._normalise_node_type(token)
        for token in (site_type_sequence or [])
        if MixedScenarioService._normalise_node_type(token)
    ]
    if not normalized_sequence:
        raise ValueError("site_type_sequence is required to derive shipments from history")

    interior_types = [
        token
        for token in normalized_sequence
        if token not in {"vendor", "customer"}
    ]

    ordered_types: List[str] = []
    seen: Set[str] = set()

    def _append(token: str) -> None:
        if token in type_totals and token not in seen:
            ordered_types.append(token)
            seen.add(token)

    _append("vendor")
    for token in reversed(interior_types):
        _append(token)
    _append("customer")

    if len(ordered_types) < 2:
        raise ValueError("Unable to traverse supply chain DAG to derive shipments")

    derived: Dict[str, Dict[str, int]] = {}

    def register(source: str, target: str, value: int) -> None:
        if value <= 0:
            return
        bucket = derived.setdefault(source, {})
        bucket[target] = bucket.get(target, 0) + value

    for idx in range(len(ordered_types) - 1):
        upstream = ordered_types[idx]
        downstream = ordered_types[idx + 1]
        downstream_arrivals = type_totals[downstream]["arrivals"]
        upstream_shipments = type_totals[upstream]["shipments"]
        value = downstream_arrivals or upstream_shipments
        register(upstream, downstream, value)

    return derived

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------
API_PREFIX = os.getenv("API_PREFIX", "/api/v1")
# Allow common local frontend ports so CORS works out of the box
FRONTEND_ORIGINS = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:8080,http://127.0.0.1:8080",
)
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-secret-key-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

ACCESS_COOKIE_NAME = os.getenv("ACCESS_COOKIE_NAME", "access_token")
REFRESH_COOKIE_NAME = os.getenv("REFRESH_COOKIE_NAME", "refresh_token")

# In dev on http, cookies cannot be "secure=True". Use samesite="lax".
COOKIE_COMMON_KWARGS = dict(httponly=True, samesite="lax", secure=False, path="/")

# Filesystem roots used for derived training metadata
BACKEND_ROOT = Path(__file__).resolve().parent
CHECKPOINT_ROOT = BACKEND_ROOT / "checkpoints" / "supply_chain_configs"

# Username alias map for convenience login (e.g. "systemadmin" → "systemadmin@autonomy.ai")
_USERNAME_ALIASES = {
    "systemadmin": "systemadmin@autonomy.ai",
    "superadmin": "systemadmin@autonomy.ai",
    "tenantadmin": "tenantadmin@autonomy.ai",
    "defaultadmin": "tenantadmin@autonomy.ai",
}

# Logger used across helpers/routes
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Models
# ------------------------------------------------------------------------------
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: Optional[str] = None

class MeResponse(BaseModel):
    id: int
    email: str
    name: str
    role: str
    tenant_id: Optional[int] = None
    user_type: Optional[str] = None
    is_superuser: bool = False
    default_config_id: Optional[int] = None
    decision_level: Optional[str] = None
    capabilities: List[str] = []
    roles: List[str] = []
    tenant_logo: Optional[str] = None


class OrderSubmission(BaseModel):
    scenario_user_id: int
    quantity: int
    comment: Optional[str] = None


class GameUpdatePayload(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    is_public: Optional[bool] = None
    max_rounds: Optional[int] = Field(None, ge=1, le=1000)
    progression_mode: Optional[Literal["supervised", "unsupervised"]] = None
    demand_pattern: Optional[DemandPattern] = None
    pricing_config: Optional[PricingConfig] = None
    node_policies: Optional[Dict[str, NodePolicy]] = None
    system_config: Optional[Dict[str, Any]] = None
    global_policy: Optional[Dict[str, Any]] = None
    player_assignments: Optional[List[ScenarioUserAssignment]] = None

# ------------------------------------------------------------------------------
# JWT utils
# ------------------------------------------------------------------------------
def _create_token(data: Dict[str, Any], expires_delta: timedelta) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_access_token(sub: str, extra: Dict[str, Any]) -> str:
    payload = {"sub": sub, **extra}
    return _create_token(payload, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))

def create_refresh_token(sub: str, extra: Dict[str, Any]) -> str:
    payload = {"sub": sub, "typ": "refresh", **extra}
    return _create_token(payload, timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))

def decode_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

# ------------------------------------------------------------------------------
# Auth helpers
# ------------------------------------------------------------------------------
def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Authenticate against the database."""

    lookup = (username or "").strip()
    if not lookup:
        return None

    # Map common aliases (e.g. "systemadmin") onto their canonical emails
    if "@" not in lookup:
        canonical = _USERNAME_ALIASES.get(lookup.lower(), lookup.lower())
    else:
        canonical = lookup.lower()

    # Authenticate against persisted users (case-insensitive)
    session: Optional[Session] = None
    try:
        session = SessionLocal()
        db_user = session.query(User).filter(
            or_(func.lower(User.email) == canonical, func.lower(User.username) == canonical)
        ).first()
        if db_user and getattr(db_user, "hashed_password", None):
            try:
                if verify_password(password, db_user.hashed_password):
                    return _build_user_payload_from_model(db_user)
            except Exception:
                logger.warning(f"Password verification error for {canonical}")
    finally:
        if session is not None:
            session.close()

    return None

def extract_bearer_from_cookie(cookie_val: Optional[str]) -> Optional[str]:
    if not cookie_val:
        return None
    # We accept raw token or "Bearer <token>"
    if cookie_val.lower().startswith("bearer "):
        return cookie_val.split(" ", 1)[1]
    return cookie_val

async def get_current_user(
    request: Request,
    access_cookie: Optional[str] = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
) -> Dict[str, Any]:
    # 1) Try Authorization header first
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    token: Optional[str] = None
    if auth and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
    # 2) Fallback to cookie
    if not token:
        token = extract_bearer_from_cookie(access_cookie)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(token)
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user: Optional[Dict[str, Any]] = None
    session: Optional[Session] = None
    try:
        session = SessionLocal()
        db_user: Optional[User] = None
        if str(sub).isdigit():
            db_user = session.get(User, int(sub))
        if db_user is None:
            email_hint = payload.get("email")
            if email_hint:
                db_user = session.query(User).filter(User.email == email_hint).first()
        if db_user is None and sub:
            db_user = session.query(User).filter(User.email == str(sub)).first()
        if db_user is not None:
            user = _build_user_payload_from_model(db_user)
    finally:
        if session is not None:
            session.close()

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user

# ------------------------------------------------------------------------------
# App & Middleware
# ------------------------------------------------------------------------------
app = FastAPI(title="Autonomy API", version="1.0.0")

# CORS (allow cookies/credentials from the frontend)
# When subdomain routing is enabled, use regex to match *.azirella.com
if settings.SUBDOMAIN_ROUTING_ENABLED:
    import re as _re
    _domain_escaped = _re.escape(settings.APP_DOMAIN)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=rf"^https?://([a-z0-9\-]+\.)?{_domain_escaped}(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    origins = [o.strip() for o in FRONTEND_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Correlation ID middleware for request tracing
try:
    from app.core.structured_logging import CorrelationIdMiddleware
    app.add_middleware(CorrelationIdMiddleware)
except ImportError:
    # Structured logging module not available, skip middleware
    pass

# Security headers middleware (X-Content-Type-Options, X-Frame-Options, CSP, etc.)
try:
    from app.middleware.security_headers import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)
except ImportError:
    pass

# CSRF protection middleware (opt-in via ENABLE_CSRF_PROTECTION env var)
if os.environ.get("ENABLE_CSRF_PROTECTION", "").lower() in ("true", "1"):
    try:
        from app.middleware.csrf import CSRFMiddleware
        app.add_middleware(CSRFMiddleware)
    except ImportError:
        pass

# Tenant isolation middleware for multi-tenancy (opt-in via ENABLE_TENANT_MIDDLEWARE env var)
if os.environ.get("ENABLE_TENANT_MIDDLEWARE", "").lower() in ("true", "1"):
    try:
        from app.middleware.tenant_middleware import TenantMiddleware
        app.add_middleware(TenantMiddleware, default_subdomain="default")
    except ImportError:
        pass

# ------------------------------------------------------------------------------
# Scheduler Startup/Shutdown Events (SAP Data Import Cadence)
# ------------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    """Initialize services on application startup."""
    import logging
    logger = logging.getLogger(__name__)

    try:
        # Import models package first to ensure proper loading order
        # This prevents relationship resolution errors and runs configure_mappers()
        import app.models  # noqa: F401 - ensures all models are loaded and configured

        # Ensure session_timeout_minutes column exists on tenants table
        try:
            from app.db.session import sync_session_factory as _stf
            from sqlalchemy import text as _sa_text
            _patch_db = _stf()
            try:
                _patch_db.execute(_sa_text(
                    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS "
                    "session_timeout_minutes INTEGER DEFAULT 5 NOT NULL"
                ))
                _patch_db.commit()
            except Exception:
                _patch_db.rollback()
            finally:
                _patch_db.close()
        except Exception as _col_err:
            logger.warning("session_timeout_minutes column patch skipped: %s", _col_err)

        # Initialize Knowledge Base database engine (separate pgvector DB)
        try:
            from app.db.kb_session import init_kb_engine
            init_kb_engine()
        except Exception as kb_err:
            logger.warning("KB engine initialization failed (non-fatal): %s", kb_err)

        # Now import services that depend on models
        from app.services.sync_scheduler_service import SyncSchedulerService
        from app.services.retention_jobs import register_retention_jobs
        from app.services.powell.relearning_jobs import register_relearning_jobs
        from app.services.powell.governance_jobs import register_governance_jobs
        from app.db.session import sync_session_factory
        from app.models import SyncJobConfig
        from app.core.config import settings

        # Initialize scheduler with database URL
        scheduler_service = SyncSchedulerService.get_instance(settings.DATABASE_URL)

        if scheduler_service:
            # Initialize the scheduler first (creates _scheduler object)
            scheduler_service.initialize()

            # Register retention jobs (HOT->WARM, WARM->COLD, monthly collapse)
            register_retention_jobs(scheduler_service)

            # Register Powell relearning loop jobs (outcome collection, CDT calibration, CDC retraining)
            register_relearning_jobs(scheduler_service)

            # Register governance sweeper jobs (auto-apply, escalation, directive expiry)
            register_governance_jobs(scheduler_service)

            # Register planning cascade jobs (S&OP weekly, MPS daily, execution hourly)
            from app.services.planning_cascade_jobs import register_planning_cascade_jobs
            register_planning_cascade_jobs(scheduler_service)

            # Register L3 transport cascade (daily 5am — MovementPlanner → IntegratedBalancer)
            from app.services.powell.l3_cascade_jobs import register_l3_cascade_jobs
            register_l3_cascade_jobs(scheduler_service)

            # Register executive briefing scheduler (hourly check for scheduled generation)
            from app.services.executive_briefing_jobs import register_executive_briefing_jobs
            register_executive_briefing_jobs(scheduler_service)

            # Register TMS extraction jobs (shipment sync, load sync, carrier refresh, port intel)
            from app.services.tms_extraction_jobs import register_tms_extraction_jobs
            register_tms_extraction_jobs(scheduler_service)

            # Register SAP data staging jobs (incremental sync every 6h, daily reconciliation)
            from app.services.sap_staging_jobs import register_sap_staging_jobs
            register_sap_staging_jobs(scheduler_service)

            # Register unified training corpus maintenance jobs
            # - Daily weight decay at 3am
            # - Weekly aggregator refresh on Sunday at 4am
            try:
                from app.services.training_corpus.corpus_jobs import register_corpus_jobs
                register_corpus_jobs(scheduler_service)
            except Exception as e:
                logger.debug(f"Corpus job registration skipped: {e}")

            # Register MCP polling jobs for tenants with MCP server configs
            try:
                from app.integrations.mcp.scheduler import register_mcp_jobs
                from app.db.session import async_session_factory
                import asyncio
                async def _register_mcp():
                    async with async_session_factory() as mcp_db:
                        count = await register_mcp_jobs(scheduler_service, mcp_db)
                        if count > 0:
                            logger.info(f"MCP: registered {count} polling jobs")
                asyncio.get_event_loop().create_task(_register_mcp())
            except Exception as e:
                logger.debug(f"MCP job registration skipped: {e}")

            # Register MCP pending writeback executor (every 1 minute)
            try:
                from app.integrations.mcp.writeback_service import process_pending_writebacks
                from app.integrations.mcp.client import mcp_pool
                async def _process_writebacks():
                    from app.db.session import async_session_factory
                    async with async_session_factory() as wb_db:
                        await process_pending_writebacks(wb_db, mcp_pool)
                scheduler_service.add_interval_job(
                    func=_process_writebacks,
                    job_id="mcp_pending_writebacks",
                    seconds=60,
                    replace_existing=True,
                )
            except Exception as e:
                logger.debug(f"MCP writeback executor registration skipped: {e}")

            # CDT startup: load existing calibration from DB into memory.
            # Fast (< 5s) — reads decision-outcome pairs already stored.
            # Simulation bootstrap only runs during provisioning (conformal step)
            # for the specific tenant being provisioned, not at startup.
            try:
                from app.services.powell.cdt_calibration_service import CDTCalibrationService
                from sqlalchemy import text as sa_text
                cdt_db = sync_session_factory()
                try:
                    # Global calibration from DB
                    cdt_svc = CDTCalibrationService(cdt_db)
                    cdt_stats = cdt_svc.calibrate_all()
                    calibrated = sum(1 for s in cdt_stats.values() if s.get("status") == "calibrated")
                    logger.info(f"CDT startup calibration (global): {calibrated}/11 agents calibrated from DB")

                    # Per-tenant DB calibration (no simulation — just loads existing data)
                    tenant_rows = cdt_db.execute(
                        sa_text(
                            "SELECT DISTINCT sc.tenant_id "
                            "FROM supply_chain_configs sc "
                            "WHERE sc.tenant_id IS NOT NULL AND sc.is_active = true "
                            "ORDER BY sc.tenant_id"
                        )
                    ).fetchall()
                    for (tid,) in tenant_rows:
                        try:
                            tenant_svc = CDTCalibrationService(cdt_db, tenant_id=tid)
                            tenant_stats = tenant_svc.calibrate_all()
                            t_cal = sum(1 for s in tenant_stats.values() if s.get("status") == "calibrated")
                            logger.info(f"CDT startup calibration (tenant {tid}): {t_cal}/11 from DB")
                        except Exception as te:
                            logger.debug(f"CDT tenant {tid}: {te}")
                finally:
                    cdt_db.close()
            except Exception as e:
                logger.warning(f"CDT startup calibration failed (non-fatal): {e}")

            # Register conformal prediction recalibration jobs
            from app.services.conformal_orchestrator import register_conformal_jobs
            register_conformal_jobs(scheduler_service)

            # Load and register existing enabled sync configs using sync session
            db = sync_session_factory()
            try:
                configs = db.query(SyncJobConfig).filter(
                    SyncJobConfig.is_enabled == True
                ).all()

                for config in configs:
                    scheduler_service.register_job(db, config)

                logger.info(f"Registered {len(configs)} sync job(s) with scheduler")
            finally:
                db.close()

            # Start the scheduler
            scheduler_service.start()
            logger.info("APScheduler started successfully")
        else:
            logger.warning("Sync scheduler service not available")

        # Initialize KB (RAG) tables in separate pgvector database
        try:
            from app.db.kb_session import init_kb_tables
            await init_kb_tables()
            logger.info("KB tables initialized successfully")
        except Exception as e:
            logger.warning(f"KB table initialization failed (non-critical): {e}")

        # Re-index any SC configs missing from the knowledge base
        try:
            from app.db.kb_session import get_kb_session
            from app.db.session import sync_session_factory
            from app.models.supply_chain_config import SupplyChainConfig
            from app.services.sc_config_indexer import ScConfigIndexer

            sync_db = sync_session_factory()
            try:
                all_configs = sync_db.query(
                    SupplyChainConfig.id, SupplyChainConfig.name, SupplyChainConfig.tenant_id
                ).all()
            finally:
                sync_db.close()

            if all_configs:
                async with get_kb_session() as kb_db:
                    from sqlalchemy import text as sa_text
                    result = await kb_db.execute(sa_text(
                        "SELECT DISTINCT tags::text FROM kb_documents "
                        "WHERE category = 'supply_chain_config' AND tags IS NOT NULL"
                    ))
                    indexed_config_ids = set()
                    for (tags_str,) in result:
                        import json as _json
                        for tag in _json.loads(tags_str):
                            if tag.startswith("config_id:"):
                                indexed_config_ids.add(int(tag.split(":")[1]))

                    missing = [c for c in all_configs if c.id not in indexed_config_ids]
                    if missing:
                        logger.info(f"KB missing {len(missing)} SC config(s) — re-indexing")
                        for cfg_id, cfg_name, tenant_id in missing:
                            try:
                                indexer = ScConfigIndexer(kb_db=kb_db, tenant_id=tenant_id)
                                await indexer.index_config(cfg_id)
                                logger.info(f"  Indexed '{cfg_name}' (id={cfg_id})")
                            except Exception as idx_err:
                                logger.warning(f"  Failed to index '{cfg_name}': {idx_err}")
                    else:
                        logger.info(f"KB has all {len(all_configs)} SC config(s) indexed")
        except Exception as e:
            logger.warning(f"SC config KB sync check failed (non-critical): {e}")

        # Hydrate conformal suite from persisted belief states
        try:
            from app.services.conformal_orchestrator import ConformalOrchestrator
            from app.db.session import async_session_factory
            if async_session_factory is not None:
                async with async_session_factory() as async_db:
                    orchestrator = ConformalOrchestrator.get_instance()
                    count = await orchestrator.hydrate_from_db(async_db)
                    logger.info(f"Hydrated {count} conformal predictors from DB")
        except Exception as e:
            logger.warning(f"Conformal suite hydration failed (non-critical): {e}")

    except Exception as e:
        # Scheduler initialization is optional - app can run without it
        # These errors are typically due to SQLAlchemy relationship issues
        # that need to be resolved in the models
        logger.warning(f"Scheduler not initialized (non-critical): {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup services on application shutdown."""
    import logging
    logger = logging.getLogger(__name__)

    try:
        from app.services.sync_scheduler_service import SyncSchedulerService

        scheduler_service = SyncSchedulerService.get_instance()
        if scheduler_service:
            scheduler_service.shutdown()
            logger.info("APScheduler shutdown completed")
    except Exception as e:
        logger.error(f"Error during scheduler shutdown: {e}")


api = APIRouter(prefix=API_PREFIX, tags=["api"])

# ------------------------------------------------------------------------------
# Health - Legacy endpoints (replaced by comprehensive health monitoring in Phase 6 Sprint 3)
# ------------------------------------------------------------------------------
# @api.get("/health")
# def health():
#     return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}

# Safety alias that some frontends ping - kept for backwards compatibility
@app.get("/api/health")
def health_alias():
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}

# ------------------------------------------------------------------------------
# Client config — public, no auth required. Frontend fetches on app init.
# Exposes only non-secret settings needed for subdomain routing & UI.
# ------------------------------------------------------------------------------
@api.get("/config/client", tags=["config"])
def client_config():
    """Public endpoint returning frontend configuration for subdomain routing."""
    from app.core.config import settings as _s
    port_suffix = f":{_s.APP_PORT}" if _s.APP_PORT else ""
    return {
        "APP_DOMAIN": _s.APP_DOMAIN,
        "APP_SCHEME": _s.APP_SCHEME,
        "APP_PORT": _s.APP_PORT,
        "SUBDOMAIN_ROUTING_ENABLED": _s.SUBDOMAIN_ROUTING_ENABLED,
        "LOGIN_SUBDOMAIN": _s.LOGIN_SUBDOMAIN,
        "DEFAULT_SUBDOMAIN": _s.DEFAULT_SUBDOMAIN,
        "LOGIN_URL": f"{_s.APP_SCHEME}://{_s.LOGIN_SUBDOMAIN}.{_s.APP_DOMAIN}{port_suffix}",
        "DEFAULT_APP_URL": f"{_s.APP_SCHEME}://{_s.DEFAULT_SUBDOMAIN}.{_s.APP_DOMAIN}{port_suffix}",
    }


@api.get("/tenants/resolve/{slug}", tags=["tenants"])
async def resolve_tenant_by_slug(slug: str):
    """Public endpoint to resolve a tenant by slug. Used by login portal
    to validate tenant existence and build redirect URL."""
    from app.db.session import async_session_factory
    from app.models.tenant import Tenant as TenantModel
    async with async_session_factory() as db:
        result = await db.execute(
            select(TenantModel).where(TenantModel.slug == slug)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        return {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "subdomain": tenant.subdomain,
            "mode": tenant.mode.value if tenant.mode else "production",
        }


# ------------------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------------------
@api.post("/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
):
    """
    Accepts form-encoded:
      - username
      - password
      - grant_type=password (ignored but accepted for compatibility)
    """
    user = authenticate_user(form.username, form.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Use user id as subject
    sub = str(user["id"])
    extra = {"email": user["email"], "role": user["role"]}

    access_token = create_access_token(sub=sub, extra=extra)
    refresh_token = create_refresh_token(sub=sub, extra={"email": user["email"]})

    # Set HttpOnly cookies so the frontend can rely on cookies if desired
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=f"Bearer {access_token}",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **COOKIE_COMMON_KWARGS,
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        **COOKIE_COMMON_KWARGS,
    )

    # Also return tokens in the body to support your current frontend
    return TokenResponse(access_token=access_token, token_type="bearer", refresh_token=refresh_token)

@api.post("/auth/logout", tags=["auth"])
async def logout(response: Response):
    # Clear cookies
    response.delete_cookie(key=ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/")
    return {"status": "ok"}

@api.get("/auth/me", response_model=MeResponse, tags=["auth"])
async def me(user: Dict[str, Any] = Depends(get_current_user)):
    display_name = user.get("name") or user.get("full_name") or user.get("username") or user["email"]
    # Extract decision_level — may be an enum or string
    pr = user.get("decision_level")
    if pr is not None and hasattr(pr, "value"):
        pr = pr.value

    # Load capabilities and roles from DB via RBACService
    capabilities = []
    role_names = []
    try:
        from app.db.session import sync_session_factory as _sync_sf
        from app.services.rbac_service import RBACService
        from app.models.user import User as _UserModel
        session = _sync_sf()
        try:
            db_user = session.query(_UserModel).filter(_UserModel.id == user["id"]).first()
            if db_user:
                svc = RBACService(session)
                capabilities = svc.get_user_capabilities(db_user)
                role_names = [r.name for r in db_user.roles] if db_user.roles else []
        finally:
            session.close()
    except Exception:
        pass  # Non-fatal — return empty capabilities

    # Fetch tenant logo for frontend branding
    tenant_logo = None
    tid = user.get("tenant_id")
    if tid:
        try:
            from app.models.tenant import Tenant as _TenantModel
            session2 = _sync_sf()
            try:
                logo_row = session2.execute(
                    select(_TenantModel.logo).where(_TenantModel.id == tid)
                ).scalar_one_or_none()
                tenant_logo = logo_row if logo_row else None
            finally:
                session2.close()
        except Exception:
            pass

    return MeResponse(
        id=user["id"],
        email=user["email"],
        name=display_name,
        role=user.get("role", "user"),
        tenant_id=tid,
        user_type=user.get("user_type"),
        is_superuser=bool(user.get("is_superuser", False)),
        default_config_id=user.get("default_config_id"),
        decision_level=pr,
        capabilities=capabilities,
        roles=role_names,
        tenant_logo=tenant_logo,
    )

@api.post("/auth/refresh", response_model=TokenResponse, tags=["auth"])
async def refresh(
    response: Response,
    refresh_cookie: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
):
    """
    Very minimal refresh implementation:
    - Reads refresh token from cookie
    - Issues a new access token
    """
    if not refresh_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    try:
        payload = decode_token(refresh_cookie)
        if payload.get("typ") != "refresh":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    # Find user in database
    user = None
    session: Optional[Session] = None
    try:
        session = SessionLocal()
        db_user: Optional[User] = None
        if str(sub).isdigit():
            db_user = session.get(User, int(sub))
        if db_user is None:
            db_user = session.query(User).filter(User.email == str(sub)).first()
        if db_user is not None:
            user = _build_user_payload_from_model(db_user)
    finally:
        if session is not None:
            session.close()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    new_access = create_access_token(sub=str(user["id"]), extra={"email": user["email"], "role": user["role"]})
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=f"Bearer {new_access}",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **COOKIE_COMMON_KWARGS,
    )
    return TokenResponse(access_token=new_access, token_type="bearer")

# Alias to match frontend expectation '/auth/refresh-token'
@api.post("/auth/refresh-token", response_model=TokenResponse, tags=["auth"])
async def refresh_alias(response: Response, refresh_cookie: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME)):
    return await refresh(response=response, refresh_cookie=refresh_cookie)

# ------------------------------------------------------------------------------
# Lightweight user & supply chain helpers for the dev backend
# ------------------------------------------------------------------------------

@api.get("/users")
async def list_users(
    limit: int = 250,
    offset: int = 0,
    user_type: Optional[str] = None,
    tenant_id: Optional[int] = None,
    search: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    db = SyncSessionLocal()
    try:
        query = db.query(User).order_by(User.created_at.desc())

        if not _is_system_admin_user(current_user):
            tenant_filter = _extract_tenant_id(current_user)
            if not tenant_filter:
                return []
            query = query.filter(User.tenant_id == tenant_filter)
        elif tenant_id is not None:
            query = query.filter(User.tenant_id == tenant_id)

        if user_type:
            normalized = user_type.strip().upper()
            try:
                enum_value = UserTypeEnum[normalized]
                query = query.filter(User.user_type == enum_value.value)
            except KeyError:
                query = query.filter(User.user_type == user_type)

        if search:
            token = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    User.email.ilike(token),
                    User.username.ilike(token),
                    User.full_name.ilike(token),
                )
            )

        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)

        rows = query.all()
        return [_serialize_user_record(row) for row in rows]
    finally:
        db.close()


@api.get("/supply-chain-config/")
async def list_supply_chain_configs(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    db = SyncSessionLocal()
    try:
        query = db.query(SupplyChainConfig).order_by(SupplyChainConfig.created_at.desc())
        if not _is_system_admin_user(current_user):
            tenant_filter = _extract_tenant_id(current_user)
            if not tenant_filter:
                return []
            query = query.filter(SupplyChainConfig.tenant_id == tenant_filter)

        configs = query.all()
        return [_serialize_supply_chain_config(cfg) for cfg in configs]
    finally:
        db.close()


@api.get("/supply-chain-config/active")
async def get_active_supply_chain_config(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Return the active SC config for the current user.

    Resolution order:
    1. User's default_config_id (set by tenant admin).
    2. Tenant's is_active=True BASELINE config.
    """
    db = SyncSessionLocal()
    try:
        from app.models.user import User as UserModel
        user_obj = db.query(UserModel).filter(UserModel.id == current_user.get("id")).first()
        if user_obj and user_obj.default_config_id:
            cfg = db.query(SupplyChainConfig).filter(
                SupplyChainConfig.id == user_obj.default_config_id,
                SupplyChainConfig.is_active == True,
            ).first()
            if cfg:
                return _serialize_supply_chain_config(cfg)

        # Fallback: tenant's active baseline
        tenant_id = _extract_tenant_id(current_user)
        if not tenant_id:
            return {}
        cfg = db.query(SupplyChainConfig).filter(
            SupplyChainConfig.tenant_id == tenant_id,
            SupplyChainConfig.is_active == True,
            SupplyChainConfig.scenario_type == "BASELINE",
        ).first()
        if not cfg:
            return {}
        return _serialize_supply_chain_config(cfg)
    finally:
        db.close()


@api.put("/users/me/active-config")
async def set_user_active_config(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Allow tenant admin to switch their active SC config (production ↔ learning)."""
    config_id = payload.get("config_id")
    if not config_id:
        raise HTTPException(status_code=400, detail="config_id is required")
    db = SyncSessionLocal()
    try:
        from app.models.user import User as UserModel
        cfg = db.query(SupplyChainConfig).filter(SupplyChainConfig.id == config_id).first()
        if not cfg:
            raise HTTPException(status_code=404, detail="Config not found")
        # Validate config belongs to user's tenant
        tenant_id = _extract_tenant_id(current_user)
        is_admin = _is_system_admin_user(current_user)
        if not is_admin and cfg.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="Config does not belong to your tenant")
        user_obj = db.query(UserModel).filter(UserModel.id == current_user.get("id")).first()
        if not user_obj:
            raise HTTPException(status_code=404, detail="User not found")
        user_obj.default_config_id = config_id
        db.commit()
        return {"default_config_id": config_id, "mode": cfg.mode}
    finally:
        db.close()


@api.put("/admin/users/{user_id}/default-config")
async def admin_set_user_default_config(
    user_id: int,
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Tenant admin sets a user's default config (learning vs production assignment)."""
    config_id = payload.get("config_id")
    if not config_id:
        raise HTTPException(status_code=400, detail="config_id is required")
    db = SyncSessionLocal()
    try:
        from app.models.user import User as UserModel
        cfg = db.query(SupplyChainConfig).filter(SupplyChainConfig.id == config_id).first()
        if not cfg:
            raise HTTPException(status_code=404, detail="Config not found")
        tenant_id = _extract_tenant_id(current_user)
        is_admin = _is_system_admin_user(current_user)
        if not is_admin and cfg.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="Config does not belong to your tenant")
        target_user = db.query(UserModel).filter(UserModel.id == user_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")
        if not is_admin and target_user.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="User does not belong to your tenant")
        target_user.default_config_id = config_id
        db.commit()
        return {"user_id": user_id, "default_config_id": config_id, "mode": cfg.mode}
    finally:
        db.close()


@api.get("/supply-chain-config/{config_id}")
def read_supply_chain_config_detail(
    config_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    db = SyncSessionLocal()
    try:
        config = _get_supply_chain_config_or_404(db, config_id)
        _ensure_can_view_supply_chain_config(current_user, config)
        payload = _serialize_supply_chain_config_detail(config)

        sites = (
            db.query(SupplySiteModel)
            .options(joinedload(SupplySiteModel.geography))
            .filter(SupplySiteModel.config_id == config.id)
            .order_by(SupplySiteModel.id.asc())
            .all()
        )
        lanes = (
            db.query(SupplyLaneModel)
            .filter(SupplyLaneModel.config_id == config.id)
            .order_by(SupplyLaneModel.id.asc())
            .all()
        )
        # product_site_configs migrated to AWS SC models (InvPolicy, ProductBom, VendorProduct)
        product_site_configs = []
        # markets and market_demands tables dropped (TBG legacy).
        # Demand data now comes from Forecast table (AWS SC DM).
        markets = []
        market_demands = []

        # AWS SC DM: sites (DB: site), transportation_lanes (DB: transportation_lane)
        site_payloads = [_serialize_site(site) for site in sites]
        transportation_lane_payloads = [_serialize_transportation_lane(lane) for lane in lanes]
        product_site_payloads = [_serialize_product_site_config(entry) for entry in product_site_configs]
        market_payloads = [_serialize_market(market) for market in markets]
        market_demand_payloads = [_serialize_market_demand(demand) for demand in market_demands]

        def _accumulate(stats: Dict[str, Any], key: str, value: Optional[float]) -> None:
            if value is None:
                return
            stats[key] = (stats.get(key) or 0.0) + value

        site_stats: Dict[int, Dict[str, Any]] = {}
        for entry in product_site_payloads:
            site_id = entry.get("site_id") or entry.get("node_id")
            bucket = site_stats.setdefault(site_id, {})
            _accumulate(bucket, "inventory_capacity_min", entry.get("inventory_target_min"))
            _accumulate(bucket, "inventory_capacity_max", entry.get("inventory_target_max"))
            _accumulate(bucket, "initial_inventory_min", entry.get("initial_inventory_min"))
            _accumulate(bucket, "initial_inventory_max", entry.get("initial_inventory_max"))
            _accumulate(bucket, "holding_cost_min", entry.get("holding_cost_min"))
            _accumulate(bucket, "holding_cost_max", entry.get("holding_cost_max"))
            _accumulate(bucket, "backlog_cost_min", entry.get("backlog_cost_min"))
            _accumulate(bucket, "backlog_cost_max", entry.get("backlog_cost_max"))

        for site_payload in site_payloads:
            stats = site_stats.get(site_payload["id"])
            if stats:
                site_payload.update(stats)
                capacity = stats.get("inventory_capacity_max") or stats.get("inventory_capacity_min")
                site_payload["inventory_capacity"] = capacity

        # AWS SC DM terminology: sites, transportation_lanes, product_site_configs
        payload.update(
            {
                "sites": site_payloads,
                "product_site_configs": product_site_payloads,
                "transportation_lanes": transportation_lane_payloads,  # AWS SC DM standard
                "markets": market_payloads,
                "market_demands": market_demand_payloads,
                "customer_demands": [],  # AWS SC DM terminology (markets/market_demands tables dropped)
            }
        )
        return payload
    finally:
        db.close()


@api.get("/supply-chain-config/{config_id}/products")
def read_supply_chain_products(
    config_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """AWS SC compliant - returns products with hierarchy_path from product_hierarchy table."""
    db = SyncSessionLocal()
    try:
        config = _get_supply_chain_config_or_404(db, config_id)
        _ensure_can_view_supply_chain_config(current_user, config)
        products = (
            db.query(SupplyProductModel)
            .filter(SupplyProductModel.config_id == config.id)
            .order_by(SupplyProductModel.id.asc())
            .all()
        )
        return [_serialize_product(product, db) for product in products]
    finally:
        db.close()


@api.get("/supply-chain-config/{config_id}/sites")
def read_supply_chain_sites(
    config_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """AWS SC DM: Returns sites (DB table: nodes)"""
    db = SyncSessionLocal()
    try:
        config = _get_supply_chain_config_or_404(db, config_id)
        _ensure_can_view_supply_chain_config(current_user, config)
        sites = (
            db.query(SupplySiteModel)
            .options(joinedload(SupplySiteModel.geography))
            .filter(SupplySiteModel.config_id == config.id)
            .order_by(SupplySiteModel.id.asc())
            .all()
        )
        # Build geo_id → region lookup from geography parent chain
        region_map = _build_site_region_map(db, sites)
        return [_serialize_site(site, region_map=region_map) for site in sites]
    finally:
        db.close()


@api.put("/supply-chain-config/{config_id}")
def update_supply_chain_config_basic(
    config_id: int,
    payload: SupplyChainConfigUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    db = SyncSessionLocal()
    try:
        config = _get_supply_chain_config_or_404(db, config_id)
        _ensure_can_manage_supply_chain_config(current_user, config)

        if hasattr(payload, "model_dump"):
            update_data = payload.model_dump(exclude_unset=True)
        else:
            update_data = payload.dict(exclude_unset=True)
        if not update_data:
            return _serialize_supply_chain_config_detail(config)

        if "tenant_id" in update_data:
            if not _is_system_admin_user(current_user):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only system admins can reassign configurations to another tenant",
                )
            config.tenant_id = update_data["tenant_id"]

        if "name" in update_data:
            config.name = (update_data["name"] or "").strip()
        if "description" in update_data:
            config.description = update_data["description"]
        if "is_active" in update_data:
            config.is_active = bool(update_data["is_active"])
        if "time_bucket" in update_data and update_data["time_bucket"] is not None:
            config.time_bucket = update_data["time_bucket"]

        if "site_type_definitions" in update_data and update_data["site_type_definitions"] is not None:
            raw_definitions = []
            for definition in update_data["site_type_definitions"]:
                if hasattr(definition, "model_dump"):
                    raw_definitions.append(definition.model_dump())
                elif hasattr(definition, "dict"):
                    raw_definitions.append(definition.dict())
                else:
                    raw_definitions.append(dict(definition))
            normalized_definitions, _ = _ensure_site_type_definitions(
                {"site_type_definitions": raw_definitions}
            )
            config.site_type_definitions = normalized_definitions

        config.updated_at = datetime.utcnow()
        db.add(config)
        db.commit()
        db.refresh(config)
        return _serialize_supply_chain_config_detail(config)
    finally:
        db.close()


def _cascade_delete_config(db, config_id: int):
    """Delete a config and ALL dependent records using dynamic FK introspection.

    Strategy:
    1. Query information_schema to discover every table with an FK to product,
       site, or transportation_lane — these are the tables that block deletion
       of core entities.
    2. Also maintain an explicit list of config_id-bearing tables to catch
       tables that only reference the config (not product/site).
    3. Use SAVEPOINTs (begin_nested) so a missing table doesn't abort the
       whole transaction.
    4. Delete in correct dependency order: FK children → core entities → config.
    """
    from sqlalchemy import text

    cid = {"cid": config_id}

    def _safe_execute(stmt, params=None):
        """Execute inside a savepoint so failures don't abort the transaction."""
        nested = db.begin_nested()
        try:
            db.execute(text(stmt), params or cid)
            nested.commit()
        except Exception:
            nested.rollback()

    # ── 0. Discover every FK dependency on product / site / transportation_lane ──
    fk_query = text("""
        SELECT DISTINCT tc.table_schema, tc.table_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.constraint_column_usage AS ccu
            ON ccu.constraint_name = tc.constraint_name
            AND ccu.table_schema   = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND ccu.table_name IN ('product', 'site', 'transportation_lane')
          AND tc.table_name NOT IN ('product', 'site', 'transportation_lane',
                                     'supply_chain_configs')
    """)
    fk_rows = db.execute(fk_query).fetchall()

    # For each discovered table, try to delete rows belonging to this config.
    for schema, table in fk_rows:
        qualified = f'"{schema}"."{table}"' if schema != "public" else f'"{table}"'
        # Most tables have config_id directly
        _safe_execute(f"DELETE FROM {qualified} WHERE config_id = :cid")
        # Some use supply_chain_config_id
        _safe_execute(f"DELETE FROM {qualified} WHERE supply_chain_config_id = :cid")
        # Fallback: delete via subquery on product/site belonging to this config
        for ref_col, ref_tbl in [
            ("product_id", "product"), ("site_id", "site"),
            ("from_site_id", "site"), ("to_site_id", "site"),
            ("item_id", "product"), ("component_product_id", "product"),
            ("ship_from_site_id", "site"), ("ship_to_site_id", "site"),
            ("origin_site_id", "site"), ("customer_site_id", "site"),
            ("supplier_site_id", "site"), ("destination_site_id", "site"),
            ("source_site_id", "site"), ("refurbishment_site_id", "site"),
            ("subcontractor_site_id", "site"), ("receiving_site_id", "site"),
            ("market_demand_site_id", "site"),
        ]:
            _safe_execute(
                f"DELETE FROM {qualified} WHERE {ref_col} IN "
                f"(SELECT id FROM {ref_tbl} WHERE config_id = :cid)"
            )

    # ── 1. Explicit config_id tables (catches tables without product/site FK) ──
    # Ordered: children-with-FKs-to-other-children first, then parents.
    explicit_tables = [
        # Powell / TRM decision & training
        "powell_atp_decisions", "powell_buffer_decisions", "powell_exception_resolution",
        "powell_forecast_adjustment_decisions", "powell_hierarchical_constraints",
        "powell_maintenance_decisions", "powell_mo_decisions", "powell_order_exceptions",
        "powell_po_decisions", "powell_policy_parameters", "powell_quality_decisions",
        "powell_rebalance_decisions", "powell_stochastic_solution", "powell_subcontracting_decisions",
        "powell_to_decisions", "powell_training_config", "powell_value_function", "powell_allocations",
        "powell_training_run",
        "trm_atp_decision_log", "trm_order_tracking_decision_log", "trm_po_decision_log",
        "trm_rebalancing_decision_log", "trm_replay_buffer", "trm_safety_stock_decision_log",
        "trm_training_config", "trm_site_training_config", "trm_base_model",
        # Forecast / exceptions
        "forecast_exception_comment", "exception_escalation_log",
        "forecast_exception", "forecast_exception_rule",
        "forecast_pipeline_run", "forecast_pipeline_config", "forecast_versions", "forecast",
        # Supply plan
        "supply_plan_results", "supply_plan_comparisons", "supply_plan_exports",
        "supply_plan_requests", "supply_plan",
        "supply_commit", "supply_demand_pegging", "supply_baseline_pack", "solver_baseline_pack",
        # Sourcing
        "sourcing_schedule_details", "sourcing_schedule", "sourcing_rules",
        # Inventory
        "inv_level", "inv_policy", "inv_projection",
        # Orders — line items before headers
        "outbound_order_line_schedule", "outbound_order_line_status",
        "outbound_order_line", "outbound_order_status", "outbound_order",
        "inbound_order_line", "inbound_order",
        "purchase_order_line_item", "purchase_order",
        "transfer_order_line_item", "transfer_order",
        "production_order_components", "production_orders",
        "turnaround_order_line_item", "turnaround_order",
        "subcontracting_order_line_item", "subcontracting_order",
        "shipment_lot", "shipment_stop", "shipment",
        "goods_receipt_line_item", "goods_receipt",
        "maintenance_order", "quality_order", "service_order", "project_order",
        # ATP / CTP
        "atp_projection", "ctp_projection", "aatp_consumption_record",
        # Production / BOM
        "product_bom", "production_capacity", "production_process",
        "reservation",
        # MPS — items before plans
        "mps_key_material_requirements", "mps_capacity_checks", "mps_plan_items",
        # Planning / MRP / S&OP / RCCP
        "planning_cycles", "planning_feedback_signal", "planning_hierarchy_config",
        "planning_policy_envelope",
        "scenario_decision_record", "planning_scenario",
        "consensus_plan_comments", "consensus_plan_votes", "consensus_plan_versions",
        "consensus_plans",
        "mrp_net_requirements", "mrp_run", "order_aggregation_policy",
        "bill_of_resources", "rccp_runs",
        # Agent / training / governance
        "agent_decision_metrics", "agent_stochastic_params", "agent_action",
        "supply_chain_training_artifacts", "tactical_tgnn_checkpoints",
        "data_drift_alerts", "data_drift_records",
        "decision_governance_policies", "guardrail_directives",
        "decision_embeddings",
        # Powell decision tables (all 12 TRMs)
        "powell_atp_allocation_decisions", "powell_po_decisions",
        "powell_mo_decisions", "powell_to_decisions",
        "powell_inventory_rebalancing_decisions", "powell_quality_decisions",
        "powell_maintenance_decisions", "powell_subcontracting_decisions",
        "powell_order_tracking_decisions", "powell_buffer_decisions",
        "powell_forecast_adjustment_decisions", "powell_forecast_baseline_decisions",
        "powell_site_agent_decisions", "powell_cdc_trigger_log",
        "powell_site_agent_checkpoints",
        # MCP tables
        "mcp_server_config", "mcp_delta_state", "mcp_pending_writeback",
        # Capacity / site planning
        "resource_capacity_constraint", "resource_capacity",
        "site_planning_config", "capacity_resource_plans",
        # Suppliers
        "supplier_performance", "vendor_lead_times", "vendor_products",
        "vendor_lead_time", "vendor_product", "trading_partners",
        "supply_planning_parameters",
        # Product lifecycle
        "product_lifecycle",
        # Demand collaboration
        "demand_collaboration_event", "demand_collaboration",
        # Demand plan
        "demand_plan", "demand_plan_version",
        # Risk / misc
        "risk_predictions", "risk_alerts", "watchlists",
        "round_metric",
        "aggregated_order", "allocation_commit", "authority_definitions",
        "collaboration_scenarios", "config_deltas", "config_lineage",
        "config_provisioning_status", "exception_workflow_template",
        "items", "market_demands", "markets",
        "sap_ingestion_jobs", "user_directives",
        # Monte Carlo — children first
        "monte_carlo_risk_alerts", "monte_carlo_time_series", "monte_carlo_scenarios",
        # Chat / KB
        "chat_messages", "agent_suggestions", "what_if_analyses",
        "kb_chunks", "kb_documents",
        # Comments
        "comment_attachments", "comment_mentions", "comments",
        # Analytics
        "agent_decisions", "performance_metrics", "sop_worklist_items",
    ]
    explicit_alt = [
        ("capacity_plans", "supply_chain_config_id"),
        ("monte_carlo_runs", "supply_chain_config_id"),
        ("mps_plans", "supply_chain_config_id"),
        ("scenarios", "supply_chain_config_id"),
    ]

    for table in explicit_tables:
        _safe_execute(f'DELETE FROM "{table}" WHERE config_id = :cid')
    for table, col in explicit_alt:
        _safe_execute(f'DELETE FROM "{table}" WHERE "{col}" = :cid')

    # ── 2. Self-referencing / cross-table FKs on the config ──
    db.execute(text("UPDATE supply_chain_configs SET base_config_id = NULL WHERE base_config_id = :cid"), cid)
    db.execute(text("UPDATE supply_chain_configs SET parent_config_id = NULL WHERE parent_config_id = :cid"), cid)
    db.execute(text("UPDATE users SET default_config_id = NULL WHERE default_config_id = :cid"), cid)

    # ── 3. Core entities (product/site/lane) ──
    db.execute(text("DELETE FROM transportation_lane WHERE config_id = :cid"), cid)
    db.execute(text("DELETE FROM product WHERE config_id = :cid"), cid)
    db.execute(text("DELETE FROM site WHERE config_id = :cid"), cid)

    # ── 4. The config itself ──
    db.execute(text("DELETE FROM supply_chain_configs WHERE id = :cid"), cid)


@api.delete("/supply-chain-config/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_supply_chain_config(
    config_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Delete a supply chain configuration and all its child entities."""
    db = SyncSessionLocal()
    try:
        config = _get_supply_chain_config_or_404(db, config_id)
        _ensure_can_manage_supply_chain_config(current_user, config)

        _cascade_delete_config(db, config_id)

        db.commit()
        return None
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting config {config_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete configuration: {e}")
    finally:
        db.close()


@api.get("/supply-chain-config/{config_id}/transportation-lanes")
@api.get("/supply-chain-config/{config_id}/lanes")  # DEPRECATED: Use /transportation-lanes
def read_supply_chain_transportation_lanes(
    config_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Get all transportation lanes for a supply chain config (AWS SC DM)."""
    db = SyncSessionLocal()
    try:
        config = _get_supply_chain_config_or_404(db, config_id)
        _ensure_can_view_supply_chain_config(current_user, config)
        lanes = (
            db.query(SupplyLaneModel)
            .filter(SupplyLaneModel.config_id == config.id)
            .order_by(SupplyLaneModel.id.asc())
            .all()
        )
        return [_serialize_transportation_lane(lane) for lane in lanes]
    finally:
        db.close()


@api.get("/supply-chain-config/{config_id}/trading-partners")
def read_supply_chain_trading_partners(
    config_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """List trading partners referenced by lanes in this config.

    Returns vendors (from_partner_id on inbound lanes) and customers
    (to_partner_id on outbound lanes) with their names and geo data.
    """
    from app.models.sc_entities import TradingPartner

    db = SyncSessionLocal()
    try:
        config = _get_supply_chain_config_or_404(db, config_id)
        _ensure_can_view_supply_chain_config(current_user, config)

        # Find all partner _ids referenced by lanes in this config
        lanes = (
            db.query(SupplyLaneModel)
            .filter(SupplyLaneModel.config_id == config_id)
            .all()
        )
        partner_ids = set()
        for lane in lanes:
            if lane.from_partner_id:
                partner_ids.add(lane.from_partner_id)
            if lane.to_partner_id:
                partner_ids.add(lane.to_partner_id)

        if not partner_ids:
            return []

        partners = (
            db.query(TradingPartner)
            .filter(TradingPartner._id.in_(partner_ids))
            .all()
        )

        return [
            {
                "_id": p._id,
                "id": p.id,
                "tpartner_type": p.tpartner_type,
                "description": p.description,
                "country": p.country,
                "city": p.city,
                "state_prov": p.state_prov,
                "geo_id": p.geo_id,
                "is_active": p.is_active,
            }
            for p in partners
        ]
    finally:
        db.close()


@api.get("/supply-chain-config/{config_id}/product-site-configs")
def read_supply_chain_product_site_configs(
    config_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Get product-site configurations (inv_policy rows) for a supply chain config."""
    db = SyncSessionLocal()
    try:
        config = _get_supply_chain_config_or_404(db, config_id)
        _ensure_can_view_supply_chain_config(current_user, config)
        entries = (
            db.query(InvPolicy)
            .filter(InvPolicy.config_id == config.id)
            .order_by(InvPolicy.id.asc())
            .all()
        )
        return [_serialize_product_site_config(e) for e in entries]
    finally:
        db.close()


@api.get("/supply-chain-config/{config_id}/markets")
def read_supply_chain_markets(
    config_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    db = SyncSessionLocal()
    try:
        config = _get_supply_chain_config_or_404(db, config_id)
        _ensure_can_view_supply_chain_config(current_user, config)
        # markets table dropped (TBG legacy) — return empty list
        return []
    finally:
        db.close()


@api.get("/supply-chain-config/{config_id}/customer-demands")
def read_supply_chain_customer_demands(
    config_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """AWS SC DM: customer demands (market_demands table dropped — returns empty list)."""
    db = SyncSessionLocal()
    try:
        config = _get_supply_chain_config_or_404(db, config_id)
        _ensure_can_view_supply_chain_config(current_user, config)
        return []
    finally:
        db.close()


@api.get("/supply-chain-config/{config_id}/market-demands", deprecated=True)
def read_supply_chain_market_demands(
    config_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """DEPRECATED: Use /customer-demands instead."""
    return read_supply_chain_customer_demands(config_id, current_user)


@api.post("/supply-chain-config/{config_id}/validate")
def validate_supply_chain_config(
    config_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Validate a supply chain configuration for priority conflicts.

    Checks that each product-site combination has unique supplier priorities.
    """
    from app.services.supply_chain_config_service import SupplyChainConfigService

    db = SyncSessionLocal()
    try:
        config = _get_supply_chain_config_or_404(db, config_id)
        _ensure_can_view_supply_chain_config(current_user, config)

        service = SupplyChainConfigService(db)
        is_valid, errors = service.validate_config(config_id)

        return {
            "is_valid": is_valid,
            "validation_status": "valid" if is_valid else "invalid",
            "errors": errors,
            "validated_at": config.validated_at.isoformat() if config.validated_at else None,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to validate configuration: {str(e)}"
        )
    finally:
        db.close()


# CSRF token endpoint to satisfy frontend interceptor
@api.get("/auth/csrf-token", tags=["auth"])
async def csrf_token(response: Response):
    import secrets
    token = secrets.token_urlsafe(32)
    # Set a non-HttpOnly cookie accessible to JS for header echo
    response.set_cookie(
        key="csrf_token",
        value=token,
        max_age=7 * 24 * 60 * 60,
        httponly=False,
        samesite="lax",
        secure=False,
        path="/",
    )
    return {"csrf_token": token}

# ------------------------------------------------------------------------------
# Example protected route (replace with your real routers)
# ------------------------------------------------------------------------------
@api.get("/secure/ping")
async def secure_ping(user: Dict[str, Any] = Depends(get_current_user)):
    return {"message": f"pong, {user['email']}", "role": user["role"]}

# ------------------------------------------------------------------------------
# System config (master ranges) and Model config (scenario model setup)
# ------------------------------------------------------------------------------
from pydantic import Field, validator
from typing import List, Optional, Mapping
import json
from sqlalchemy import create_engine, Column, Integer, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.core.db_urls import resolve_sync_database_url


DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
SYSTEM_CONFIG_PATH = os.path.join(DATA_DIR, "system_config.json")
MODEL_CONFIG_PATH = os.path.join(DATA_DIR, "model_config.json")
os.makedirs(DATA_DIR, exist_ok=True)

def _build_engine():
    return create_engine(resolve_sync_database_url())


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()
SyncSessionLocal = sessionmaker(bind=sync_engine, autocommit=False, autoflush=False)

# Reuse the core sync engine session factory everywhere so we benefit from
# the pooled connection settings (pre-ping, recycle, etc.) configured in
# app.db.session. This patch keeps the existing import paths working while
# avoiding duplicate engines that were prone to stale connections.
SessionLocal = SyncSessionLocal


def get_sync_session():
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_system_admin(user: Dict[str, Any]):
    role = (user.get("role") or "").lower()
    if role not in {"systemadmin", "superadmin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")


def _default_tenant_payload() -> TenantCreate:
    return build_default_tenant_payload()




def _serialize_user_record(user: User) -> Dict[str, Any]:
    if hasattr(user.user_type, "value"):
        user_type = user.user_type.value
    else:
        user_type = str(user.user_type) if user.user_type is not None else None

    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "full_name": getattr(user, "full_name", None),
        "tenant_id": user.tenant_id,
        "user_type": user_type,
        "is_active": bool(getattr(user, "is_active", True)),
        "is_superuser": bool(getattr(user, "is_superuser", False)),
        "created_at": _iso(getattr(user, "created_at", None)),
        "updated_at": _iso(getattr(user, "updated_at", None)),
        "last_login": _iso(getattr(user, "last_login", None)),
    }


def _normalize_role_from_user(user_type: Optional[str], is_superuser: bool) -> str:
    token = (user_type or "").strip().lower()
    if is_superuser or token in {"system_admin", "systemadmin", "superadmin"}:
        return "systemadmin"
    if token in {"tenant_admin", "tenantadmin"}:
        return "tenantadmin"
    return "user"


def _build_user_payload_from_model(user: User) -> Dict[str, Any]:
    data = _serialize_user_record(user)
    name = data.get("full_name") or data.get("username") or data.get("email")
    user_type = (data.get("user_type") or "")
    role = _normalize_role_from_user(user_type, bool(data.get("is_superuser")))
    # Extract decision_level (may be enum or string)
    pr = getattr(user, "decision_level", None)
    if pr is not None and hasattr(pr, "value"):
        pr = pr.value

    payload = {
        "id": data["id"],
        "email": data["email"],
        "name": name,
        "role": role,
        "tenant_id": data.get("tenant_id"),
        "is_superuser": bool(data.get("is_superuser")),
        "user_type": user_type,
        "default_config_id": getattr(user, "default_config_id", None),
        "decision_level": pr,
    }
    return payload


def _supply_chain_checkpoint_path(config_id: int) -> Path:
    return CHECKPOINT_ROOT / f"config_{config_id}" / "temporal_gnn.pt"


def _serialize_supply_chain_config(cfg: SupplyChainConfig) -> Dict[str, Any]:
    model_path = cfg.trained_model_path or None
    derived_path: Optional[str] = None
    if model_path and Path(model_path).exists():
        derived_path = model_path
    else:
        candidate = _supply_chain_checkpoint_path(cfg.id)
        if candidate.exists():
            derived_path = str(candidate)

    training_status = (cfg.training_status or "").strip()
    normalized_status = training_status.lower()
    needs_training = bool(cfg.needs_training)
    trained_at_iso = _iso(getattr(cfg, "trained_at", None))

    if derived_path and normalized_status in {"", "pending", "in_progress", "needs_training"}:
        training_status = "trained"
    if derived_path and needs_training:
        needs_training = False
    if derived_path and not trained_at_iso:
        try:
            trained_at_iso = datetime.utcfromtimestamp(Path(derived_path).stat().st_mtime).isoformat() + "Z"
        except OSError:
            trained_at_iso = None

    return {
        "id": cfg.id,
        "name": cfg.name,
        "description": cfg.description,
        "is_active": bool(cfg.is_active),
        "mode": getattr(cfg, "mode", "production") or "production",
        "tenant_id": cfg.tenant_id,
        "parent_config_id": cfg.parent_config_id,
        "base_config_id": getattr(cfg, "base_config_id", None),
        "scenario_type": getattr(cfg, "scenario_type", "BASELINE") or "BASELINE",
        "created_at": _iso(getattr(cfg, "created_at", None)),
        "updated_at": _iso(getattr(cfg, "updated_at", None)),
        "needs_training": needs_training,
        "training_status": training_status or ("trained" if derived_path else "pending"),
        "trained_at": trained_at_iso,
        "trained_model_path": derived_path,
    }


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_range(payload: Any) -> Dict[str, Optional[float]]:
    if isinstance(payload, dict):
        min_val = _as_float(payload.get("min"))
        max_val = _as_float(payload.get("max"))
        # Fallbacks to keep values sensible
        if min_val is None and max_val is not None:
            min_val = max_val
        if max_val is None and min_val is not None:
            max_val = min_val
        return {"min": min_val, "max": max_val}
    if payload is None:
        return {"min": None, "max": None}
    value = _as_float(payload)
    return {"min": value, "max": value}


def _get_supply_chain_config_or_404(db: Session, config_id: int) -> SupplyChainConfig:
    config = db.query(SupplyChainConfig).filter(SupplyChainConfig.id == config_id).first()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )
    return config


def _ensure_can_view_supply_chain_config(user: Dict[str, Any], config: SupplyChainConfig) -> None:
    if user.get("is_superuser"):
        return

    config_tenant_id = getattr(config, "tenant_id", None)
    if config_tenant_id is None:
        return

    user_tenant_id = _as_int(user.get("tenant_id"))
    if user_tenant_id is not None and user_tenant_id == int(config_tenant_id):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to view this configuration",
    )


def _ensure_can_manage_supply_chain_config(user: Dict[str, Any], config: SupplyChainConfig) -> None:
    if user.get("is_superuser"):
        return

    config_tenant_id = getattr(config, "tenant_id", None)
    user_tenant_id = _as_int(user.get("tenant_id"))
    if config_tenant_id is None or user_tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this configuration",
        )

    role_token = str(user.get("role") or "").strip().lower()
    user_type_token = str(user.get("user_type") or "").strip().lower()
    if user_tenant_id == int(config_tenant_id) and (
        role_token == "tenantadmin" or user_type_token in {"tenant_admin", "tenantadmin"}
    ):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have access to this configuration",
    )


def _serialize_supply_chain_config_detail(cfg: SupplyChainConfig) -> Dict[str, Any]:
    payload = _serialize_supply_chain_config(cfg)
    payload["time_bucket"] = cfg.time_bucket.value if getattr(cfg, "time_bucket", None) else None
    payload["description"] = cfg.description
    payload["tenant_id"] = cfg.tenant_id
    payload["is_active"] = bool(cfg.is_active)
    definitions, labels = _ensure_site_type_definitions(
        {
            "site_type_definitions": getattr(cfg, "site_type_definitions", None)
            or [],
        }
    )
    payload["site_type_definitions"] = definitions
    payload["site_type_labels"] = labels
    return payload


def _compute_hierarchy_path(db: Session, product_group_id: Optional[str]) -> Optional[str]:
    """Compute hierarchy breadcrumb from product_hierarchy table."""
    if not product_group_id:
        return None
    path_parts = []
    current_id = product_group_id
    visited = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        node = db.query(ProductHierarchy).filter(ProductHierarchy.id == current_id).first()
        if not node:
            break
        path_parts.append(node.description or node.id)
        current_id = node.parent_product_group_id
    if path_parts:
        path_parts.reverse()
        return " > ".join(path_parts)
    return None


def _serialize_product(product: SupplyProductModel, db: Optional[Session] = None) -> Dict[str, Any]:
    """Serialize Product (AWS SC model) with optional hierarchy_path."""
    result = {
        "id": product.id,
        "config_id": product.config_id,
        "name": product.id,  # Product.id is the product name/SKU
        "description": product.description or "",
        "unit_cost_range": product.unit_cost_range or {"min": product.unit_cost or 0, "max": product.unit_price or 0},
        "category": getattr(product, "category", None),
        "family": getattr(product, "family", None),
        "product_group_name": getattr(product, "product_group_name", None),
    }
    # Add hierarchy_path if db session is available and product has product_group_id
    if db and hasattr(product, 'product_group_id') and product.product_group_id:
        result["hierarchy_path"] = _compute_hierarchy_path(db, product.product_group_id)
    return result


def _build_site_region_map(db, sites) -> Dict[str, str]:
    """Build a geo_id → region_name lookup by walking the geography parent chain.

    Geography hierarchy: Country → Region → State → City/Site
    Sites link to city-level geography. We walk up 2 levels (city → state → region)
    to find the region description.
    """
    from app.models.sc_entities import Geography as GeoModel

    geo_ids = [s.geo_id for s in sites if getattr(s, "geo_id", None)]
    if not geo_ids:
        return {}

    # Load all site-level geography records
    all_geos = db.query(GeoModel).filter(GeoModel.id.in_(geo_ids)).all()
    geo_map = {g.id: g for g in all_geos}

    # Load parents (state level)
    parent_ids = [g.parent_geo_id for g in all_geos if g.parent_geo_id]
    if parent_ids:
        parents = db.query(GeoModel).filter(GeoModel.id.in_(parent_ids)).all()
        for p in parents:
            geo_map[p.id] = p

    # Load grandparents (region level)
    grandparent_ids = [
        geo_map[pid].parent_geo_id
        for pid in parent_ids
        if pid in geo_map and geo_map[pid].parent_geo_id
    ]
    if grandparent_ids:
        grandparents = db.query(GeoModel).filter(GeoModel.id.in_(grandparent_ids)).all()
        for gp in grandparents:
            geo_map[gp.id] = gp

    # Build geo_id → region name for each site-level geo
    region_map = {}
    for geo_id in geo_ids:
        geo = geo_map.get(geo_id)
        if not geo:
            continue
        state_geo = geo_map.get(geo.parent_geo_id) if geo.parent_geo_id else None
        region_geo = geo_map.get(state_geo.parent_geo_id) if state_geo and state_geo.parent_geo_id else None
        if region_geo and region_geo.description:
            name = region_geo.description
            if name.endswith(" Region"):
                name = name[:-7]
            region_map[geo_id] = name
        elif state_geo and state_geo.description:
            region_map[geo_id] = state_geo.description
    return region_map


def _serialize_site(site: SupplySiteModel, region_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Serialize Site (AWS SC model) - nodes are called sites in AWS SC DM."""
    site_type = str(getattr(site, "dag_type", None) or getattr(site, "type", "")).lower()
    master_type = str(getattr(site, "master_node_type", None) or getattr(site, "master_type", "")).upper()

    # Include geography data if the relationship is loaded
    geography = getattr(site, "geography", None)
    geo_id = getattr(site, "geo_id", None)
    geo_data = None
    attrs = getattr(site, "attributes", None) or {}
    if geography:
        geo_data = {
            "id": geography.id,
            "city": geography.city,
            "state_prov": geography.state_prov,
            "region": region_map.get(geo_id) if region_map and geo_id else None,
            "country": geography.country,
            "latitude": geography.latitude or getattr(site, "latitude", None),
            "longitude": geography.longitude or getattr(site, "longitude", None),
        }
    elif getattr(site, "latitude", None) is not None and getattr(site, "longitude", None) is not None:
        geo_data = {
            "id": None,
            "city": attrs.get("city") if isinstance(attrs, dict) else None,
            "state_prov": attrs.get("state") if isinstance(attrs, dict) else None,
            "region": None,
            "country": attrs.get("country") if isinstance(attrs, dict) else None,
            "latitude": site.latitude,
            "longitude": site.longitude,
        }

    return {
        "id": site.id,
        "config_id": site.config_id,
        "name": site.name,
        "type": site_type,
        "master_type": master_type,
        "dag_type": site_type,
        "geo_id": geo_id,
        "geography": geo_data,
        "latitude": getattr(site, "latitude", None),
        "longitude": getattr(site, "longitude", None),
        "attributes": attrs if isinstance(attrs, dict) else {},
        "inventory_capacity_min": None,
        "inventory_capacity_max": None,
        "initial_inventory_min": None,
        "initial_inventory_max": None,
        "holding_cost_min": None,
        "holding_cost_max": None,
        "backlog_cost_min": None,
        "backlog_cost_max": None,
    }


def _serialize_transportation_lane(lane: SupplyLaneModel) -> Dict[str, Any]:
    """Serialize TransportationLane (AWS SC DM) - DB table: transportation_lane."""
    lead_time = lane.lead_time_days or {}
    lead_time_range = _extract_range(lead_time)
    capacity_value = _as_float(lane.capacity)
    # Resolve partner names and coordinates for partner-endpoint lanes
    from_partner_name = None
    to_partner_name = None
    from_partner_lat = None
    from_partner_lon = None
    to_partner_lat = None
    to_partner_lon = None
    if lane.from_partner_id:
        try:
            p = lane.upstream_partner
            if p:
                from_partner_name = p.description
                from_partner_lat = getattr(p, "latitude", None)
                from_partner_lon = getattr(p, "longitude", None)
        except Exception:
            pass
    if lane.to_partner_id:
        try:
            p = lane.downstream_partner
            if p:
                to_partner_name = p.description
                to_partner_lat = getattr(p, "latitude", None)
                to_partner_lon = getattr(p, "longitude", None)
        except Exception:
            pass

    supply_lt = lane.supply_lead_time or {}
    demand_lt = lane.demand_lead_time or {}

    return {
        "id": lane.id,
        "config_id": lane.config_id,
        "from_site_id": lane.from_site_id,
        "to_site_id": lane.to_site_id,
        "from_partner_id": lane.from_partner_id,
        "to_partner_id": lane.to_partner_id,
        "from_partner_name": from_partner_name,
        "to_partner_name": to_partner_name,
        "from_partner_lat": from_partner_lat,
        "from_partner_lon": from_partner_lon,
        "to_partner_lat": to_partner_lat,
        "to_partner_lon": to_partner_lon,
        "capacity": capacity_value,
        "capacity_int": int(capacity_value) if capacity_value is not None else None,
        "lead_time_days": lead_time,
        "lead_time_min": lead_time_range["min"],
        "lead_time_max": lead_time_range["max"],
        "supply_lead_time": supply_lt,
        "demand_lead_time": demand_lt,
    }


# DEPRECATED: Use _serialize_transportation_lane
_serialize_lane = _serialize_transportation_lane


def _serialize_product_site_config(entry) -> Dict[str, Any]:
    """Serialize an InvPolicy row as a product-site config."""
    inv_target = _extract_range(entry.inventory_target_range)
    init_inventory = _extract_range(entry.initial_inventory_range)
    holding_cost = _extract_range(entry.holding_cost_range)
    backlog_cost = _extract_range(entry.backlog_cost_range)
    selling_price = _extract_range(entry.selling_price_range)
    return {
        "id": entry.id,
        "product_id": entry.product_id,
        "site_id": entry.site_id,
        "inventory_target_range": entry.inventory_target_range or {"min": 0, "max": 0},
        "inventory_target_min": inv_target["min"],
        "inventory_target_max": inv_target["max"],
        "initial_inventory_range": entry.initial_inventory_range or {"min": 0, "max": 0},
        "initial_inventory_min": init_inventory["min"],
        "initial_inventory_max": init_inventory["max"],
        "holding_cost_range": entry.holding_cost_range or {"min": 0, "max": 0},
        "holding_cost_min": holding_cost["min"],
        "holding_cost_max": holding_cost["max"],
        "backlog_cost_range": entry.backlog_cost_range or {"min": 0, "max": 0},
        "backlog_cost_min": backlog_cost["min"],
        "backlog_cost_max": backlog_cost["max"],
        "selling_price_range": entry.selling_price_range or {"min": 0, "max": 0},
        "selling_price_min": selling_price["min"],
        "selling_price_max": selling_price["max"],
    }


def _serialize_market(market: SupplyMarketModel) -> Dict[str, Any]:
    return {
        "id": market.id,
        "config_id": market.config_id,
        "name": market.name,
        "description": market.description,
    }


def _serialize_market_demand(demand: SupplyMarketDemandModel) -> Dict[str, Any]:
    return {
        "id": demand.id,
        "config_id": demand.config_id,
        "product_id": demand.product_id,
        "market_id": demand.market_id,
        "demand_pattern": demand.demand_pattern or {},
    }


class SystemConfigRow(Base):
    __tablename__ = "system_config"
    id = Column(Integer, primary_key=True)
    version = Column(Integer, default=1)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ModelConfigRow(Base):
    __tablename__ = "model_config"
    id = Column(Integer, primary_key=True)
    version = Column(Integer, default=1)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


try:
    Base.metadata.create_all(bind=engine)
except Exception:
    pass


class Range(BaseModel):
    min: float
    max: float


class SystemConfigModel(BaseModel):
    name: str = Field(default="Default System Config")
    order_leadtime: Range = Field(default=Range(min=0, max=8))
    supply_leadtime: Range = Field(default=Range(min=0, max=8))
    init_inventory: Range = Field(default=Range(min=0, max=1000))
    holding_cost: Range = Field(default=Range(min=0, max=100))
    backlog_cost: Range = Field(default=Range(min=0, max=200))
    max_inbound_per_link: Range = Field(default=Range(min=10, max=2000))
    max_order: Range = Field(default=Range(min=10, max=2000))
    price: Range = Field(default=Range(min=0, max=10000))
    standard_cost: Range = Field(default=Range(min=0, max=10000))
    min_order_qty: Range = Field(default=Range(min=0, max=1000))

    class Config:
        allow_population_by_field_name = True


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _read_system_cfg() -> SystemConfigModel:
    # Try DB first
    try:
        db = SessionLocal()
        row = db.query(SystemConfigRow).order_by(SystemConfigRow.updated_at.desc()).first()
        if row and row.payload:
            return SystemConfigModel(**json.loads(row.payload))
    except Exception:
        pass
    finally:
        try:
            db.close()
        except Exception:
            pass
    # Fallback: read file and seed DB
    try:
        if os.path.exists(SYSTEM_CONFIG_PATH):
            with open(SYSTEM_CONFIG_PATH, "r") as f:
                data = json.load(f)
                try:
                    db = SessionLocal()
                    seed = SystemConfigRow(id=1, version=1, payload=json.dumps(data), created_at=datetime.utcnow(), updated_at=datetime.utcnow())
                    db.add(seed)
                    db.commit()
                except Exception:
                    pass
                finally:
                    try:
                        db.close()
                    except Exception:
                        pass
                return SystemConfigModel(**data)
    except Exception:
        pass
    return SystemConfigModel()


@api.get("/config/system", response_model=SystemConfigModel)
def get_system_config():
    return _read_system_cfg()


@api.put("/config/system", response_model=SystemConfigModel)
def put_system_config(cfg: SystemConfigModel):
    # Save to DB (single row upsert)
    try:
        payload = json.dumps(cfg.dict())
        db = SessionLocal()
        row = db.query(SystemConfigRow).filter(SystemConfigRow.id == 1).first()
        now = datetime.utcnow()
        if row:
            row.payload = payload
            row.updated_at = now
        else:
            row = SystemConfigRow(id=1, version=1, payload=payload, created_at=now, updated_at=now)
            db.add(row)
        db.commit()
        return cfg
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save system config: {e}")
    finally:
        try:
            db.close()
        except Exception:
            pass


# ---------------------- LLM Settings (runtime, no restart required) ----------------------

import os as _os
from typing import Literal as _Literal

_LLM_SETTINGS_PATH = _os.path.abspath(
    _os.path.join(_os.path.dirname(__file__), "data", "llm_settings.json")
)


class LLMSettings(BaseModel):
    """Runtime LLM routing. Changes take effect immediately — no restart needed."""
    briefing_provider: _Literal["auto", "claude", "vllm"] = "auto"
    skills_provider: _Literal["auto", "claude", "vllm"] = "auto"


def _read_llm_settings() -> LLMSettings:
    try:
        if _os.path.exists(_LLM_SETTINGS_PATH):
            with open(_LLM_SETTINGS_PATH) as f:
                return LLMSettings(**json.load(f))
    except Exception:
        pass
    return LLMSettings()


@api.get("/config/llm", response_model=LLMSettings, tags=["config"])
def get_llm_settings(current_user=Depends(get_current_user)):
    """Get current LLM provider routing settings."""
    return _read_llm_settings()


@api.put("/config/llm", response_model=LLMSettings, tags=["config"])
def put_llm_settings(settings: LLMSettings, current_user=Depends(get_current_user)):
    """Update LLM provider routing. Takes effect immediately — no restart required."""
    try:
        _os.makedirs(_os.path.dirname(_LLM_SETTINGS_PATH), exist_ok=True)
        with open(_LLM_SETTINGS_PATH, "w") as f:
            json.dump(settings.dict(), f, indent=2)
        return settings
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save LLM settings: {e}")


# ---------------------- Model Config ----------------------
class Item(BaseModel):
    id: str
    name: str


class Site(BaseModel):
    id: str
    type: str  # manufacturer|distributor|wholesaler|retailer
    name: str
    items_sold: List[str] = Field(default_factory=list)


class SiteItemSettings(BaseModel):
    inventory_target: float
    holding_cost: float
    backorder_cost: float
    avg_selling_price: float
    standard_cost: float
    moq: float


class Lane(BaseModel):
    from_site_id: str
    to_site_id: str
    product_id: str
    lead_time: float
    capacity: Optional[float] = None
    otif_target: Optional[float] = Field(default=None, description="0-1 fraction or 0-100 percent")

    @validator("otif_target")
    def _normalize_otif(cls, v):
        if v is None:
            return v
        # Accept 0-1 or 0-100; normalize to 0-1
        return v / 100.0 if v > 1 else v


class RetailerDemand(BaseModel):
    distribution: str = Field(default="profile")  # profile|poisson|normal
    params: Mapping[str, float] = Field(default_factory=dict)
    expected_delivery_offset: Optional[float] = 0.0


class ModelConfig(BaseModel):
    version: int = 1
    items: List[Item]
    sites: List[Site]
    # site_item_settings[siteId][itemId] = SiteItemSettings
    site_item_settings: Mapping[str, Mapping[str, SiteItemSettings]]
    lanes: List[Lane]
    retailer_demand: RetailerDemand
    manufacturer_lead_times: Mapping[str, float] = Field(default_factory=dict)


def _read_model_cfg() -> ModelConfig:
    # Try DB first
    try:
        db = SessionLocal()
        row = db.query(ModelConfigRow).order_by(ModelConfigRow.updated_at.desc()).first()
        if row and row.payload:
            return ModelConfig(**json.loads(row.payload))
    except Exception:
        pass
    finally:
        try:
            db.close()
        except Exception:
            pass
    # Fallback to file and seed DB
    if os.path.exists(MODEL_CONFIG_PATH):
        with open(MODEL_CONFIG_PATH, "r") as f:
            data = json.load(f)
            try:
                db = SessionLocal()
                seed = ModelConfigRow(id=1, version=1, payload=json.dumps(data), created_at=datetime.utcnow(), updated_at=datetime.utcnow())
                db.add(seed)
                db.commit()
            except Exception:
                pass
            finally:
                try:
                    db.close()
                except Exception:
                    pass
            return ModelConfig(**data)
    # Default classic demand pattern
    default = ModelConfig(
        items=[Item(id="item_1", name="Item 1")],
        sites=[
            Site(id="manufacturer_1", type="manufacturer", name="Manufacturer 1", items_sold=["item_1"]),
            Site(id="distributor_1", type="distributor", name="Distributor 1", items_sold=["item_1"]),
            Site(id="wholesaler_1", type="wholesaler", name="Wholesaler 1", items_sold=["item_1"]),
            Site(id="retailer_1", type="retailer", name="Retailer 1", items_sold=["item_1"]),
        ],
        site_item_settings={
            "manufacturer_1": {"item_1": SiteItemSettings(inventory_target=20, holding_cost=0.5, backorder_cost=1.0, avg_selling_price=7.0, standard_cost=5.0, moq=0)},
            "distributor_1": {"item_1": SiteItemSettings(inventory_target=20, holding_cost=0.5, backorder_cost=1.0, avg_selling_price=7.0, standard_cost=5.0, moq=0)},
            "wholesaler_1": {"item_1": SiteItemSettings(inventory_target=20, holding_cost=0.5, backorder_cost=1.0, avg_selling_price=7.0, standard_cost=5.0, moq=0)},
            "retailer_1": {"item_1": SiteItemSettings(inventory_target=20, holding_cost=0.5, backorder_cost=1.0, avg_selling_price=7.0, standard_cost=5.0, moq=0)},
        },
        lanes=[
            Lane(from_site_id="manufacturer_1", to_site_id="distributor_1", product_id="item_1", lead_time=2, capacity=None, otif_target=0.95),
            Lane(from_site_id="distributor_1", to_site_id="wholesaler_1", product_id="item_1", lead_time=2, capacity=None, otif_target=0.95),
            Lane(from_site_id="wholesaler_1", to_site_id="retailer_1", product_id="item_1", lead_time=2, capacity=None, otif_target=0.95),
        ],
        retailer_demand=RetailerDemand(distribution="profile", params={"week1_4": 4, "week5_plus": 8}, expected_delivery_offset=1),
        manufacturer_lead_times={"item_1": 2},
    )
    return default


def _validate_model_config(cfg: ModelConfig, ranges: SystemConfigModel):
    # Validate site-item settings against ranges
    errors = []
    for site_id, item_map in cfg.site_item_settings.items():
        for item_id, s in item_map.items():
            if not (ranges.init_inventory.min <= s.inventory_target <= ranges.init_inventory.max):
                errors.append(f"site {site_id} item {item_id}: inventory_target {s.inventory_target} not in [{ranges.init_inventory.min},{ranges.init_inventory.max}]")
            if not (ranges.holding_cost.min <= s.holding_cost <= ranges.holding_cost.max):
                errors.append(f"site {site_id} item {item_id}: holding_cost {s.holding_cost} not in [{ranges.holding_cost.min},{ranges.holding_cost.max}]")
            if not (ranges.backlog_cost.min <= s.backorder_cost <= ranges.backlog_cost.max):
                errors.append(f"site {site_id} item {item_id}: backorder_cost {s.backorder_cost} not in [{ranges.backlog_cost.min},{ranges.backlog_cost.max}]")
            if not (ranges.price.min <= s.avg_selling_price <= ranges.price.max):
                errors.append(f"site {site_id} item {item_id}: avg_selling_price {s.avg_selling_price} not in [{ranges.price.min},{ranges.price.max}]")
            if not (ranges.standard_cost.min <= s.standard_cost <= ranges.standard_cost.max):
                errors.append(f"site {site_id} item {item_id}: standard_cost {s.standard_cost} not in [{ranges.standard_cost.min},{ranges.standard_cost.max}]")
            if not (ranges.min_order_qty.min <= s.moq <= ranges.min_order_qty.max):
                errors.append(f"site {site_id} item {item_id}: moq {s.moq} not in [{ranges.min_order_qty.min},{ranges.min_order_qty.max}]")

    # Validate transportation lanes
    for lane in cfg.transportation_lanes:
        if not (ranges.supply_leadtime.min <= lane.lead_time <= ranges.supply_leadtime.max):
            errors.append(
                f"transportation_lane {lane.from_site_id}->{lane.to_site_id} product {lane.product_id}: lead_time {lane.lead_time} not in [{ranges.supply_leadtime.min},{ranges.supply_leadtime.max}]"
            )
        if lane.capacity is not None and not (ranges.max_inbound_per_link.min <= lane.capacity <= ranges.max_inbound_per_link.max):
            errors.append(f"transportation_lane {lane.from_site_id}->{lane.to_site_id} product {lane.product_id}: capacity {lane.capacity} not in [{ranges.max_inbound_per_link.min},{ranges.max_inbound_per_link.max}]")

    if errors:
        raise HTTPException(status_code=422, detail={"message": "Model config out of bounds", "errors": errors})


@api.get("/config/model", response_model=ModelConfig)
def get_model_config(user: Dict[str, Any] = Depends(get_current_user)):
    return _read_model_cfg()


@api.put("/config/model", response_model=ModelConfig)
def put_model_config(cfg: ModelConfig, user: Dict[str, Any] = Depends(get_current_user)):
    # Validate against system ranges
    ranges = _read_system_cfg()
    _validate_model_config(cfg, ranges)
    # Save to DB (single row upsert)
    try:
        payload = json.dumps(cfg.dict())
        db = SessionLocal()
        row = db.query(ModelConfigRow).filter(ModelConfigRow.id == 1).first()
        now = datetime.utcnow()
        if row:
            row.payload = payload
            row.updated_at = now
        else:
            row = ModelConfigRow(id=1, version=1, payload=payload, created_at=now, updated_at=now)
            db.add(row)
        db.commit()
        return cfg
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save model config: {e}")
    finally:
        try:
            db.close()
        except Exception:
            pass

# ------------------------------------------------------------------------------
# Minimal in-memory Mixed Games API to support the UI
# ------------------------------------------------------------------------------


from app.models.scenario import Scenario as DbScenario, ScenarioStatus as DbScenarioStatus
from app.models.user import UserTypeEnum

# Aliases for backward compatibility within this file
DbGame = DbScenario
DbScenarioStatus = DbScenarioStatus


def _is_system_admin_user(user: Any) -> bool:
    if isinstance(user, dict):
        if user.get("is_superuser"):
            return True
        token = str(user.get("role") or user.get("user_type") or "").lower()
        return token in {"systemadmin", "system_admin", "superadmin", "systemadministrator"}

    if getattr(user, "is_superuser", False):
        return True

    user_type = getattr(user, "user_type", None)
    if isinstance(user_type, UserTypeEnum):
        return user_type == UserTypeEnum.SYSTEM_ADMIN
    if isinstance(user_type, str):
        token = user_type.lower()
        return token in {"systemadmin", "system_admin", "superadmin", "systemadministrator"}
    return False


def _extract_tenant_id(user: Any) -> Optional[int]:
    gid = user.get("tenant_id") if isinstance(user, dict) else getattr(user, "tenant_id", None)
    try:
        return int(gid) if gid is not None else None
    except (TypeError, ValueError):
        return None


# Backward-compatible alias
_extract_customer_id = _extract_tenant_id


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


STATUS_REMAPPING = {
    DbScenarioStatus.CREATED: "CREATED",
    DbScenarioStatus.STARTED: "IN_PROGRESS",
    DbScenarioStatus.PERIOD_IN_PROGRESS: "IN_PROGRESS",
    DbScenarioStatus.PERIOD_COMPLETED: "PAUSED",
    DbScenarioStatus.FINISHED: "COMPLETED",
}

PROGRESSION_SUPERVISED = "supervised"
PROGRESSION_UNSUPERVISED = "unsupervised"

ROLE_TO_AGENT_TYPE = {
    "retailer": AgentType.RETAILER,
    "wholesaler": AgentType.WHOLESALER,
    "distributor": AgentType.DISTRIBUTOR,
    "manufacturer": AgentType.MANUFACTURER,
}


def _ordered_nodes_from_lane_views(
    lane_views: Dict[str, Any],
    fallback_nodes: Sequence[str],
) -> List[str]:
    """Return a deduplicated node order prioritising the DAG traversal."""

    ordered: List[str] = []
    seen: Set[str] = set()
    node_sequence = lane_views.get("node_sequence") or []
    for bucket in (node_sequence, fallback_nodes):
        for raw in bucket:
            node = MixedScenarioService._normalise_key(raw) if isinstance(raw, str) else raw
            if not node or node in seen:
                continue
            ordered.append(node)
            seen.add(node)
    return ordered


def _actor_nodes_from_order(
    ordered_nodes: Sequence[str],
    node_types: Mapping[str, Any],
) -> List[str]:
    """Filter nodes that represent playable positions (exclude external trading partners)."""

    # External TradingPartner node types — not managed by internal planners.
    # "vendor"/"customer" are the current names; legacy aliases kept for backward compat.
    skip_types = {"vendor", "customer"}
    actors: List[str] = []
    for node in ordered_nodes:
        node_type = str(node_types.get(node, "") or "").lower()
        if node_type in skip_types:
            continue
        actors.append(node)
    return actors or list(ordered_nodes)

# Canonical site type definitions aligned with AWS SC Data Model.
# External parties (Vendor / Customer) are TradingPartner records — not internal sites.
DEFAULT_SITE_TYPE_DEFINITIONS: List[Dict[str, Any]] = [
    {"type": "CUSTOMER", "label": "Customer", "order": 0, "is_required": True, "is_external": True, "tpartner_type": "customer"},
    {"type": "RETAILER", "label": "Retailer", "order": 1, "is_required": False, "is_external": False, "master_type": "inventory"},
    {"type": "WHOLESALER", "label": "Wholesaler", "order": 2, "is_required": False, "is_external": False, "master_type": "inventory"},
    {"type": "DISTRIBUTOR", "label": "Distributor", "order": 3, "is_required": False, "is_external": False, "master_type": "inventory"},
    {"type": "MANUFACTURER", "label": "Manufacturer", "order": 4, "is_required": False, "is_external": False, "master_type": "manufacturer"},
    {"type": "VENDOR", "label": "Vendor", "order": 5, "is_required": True, "is_external": True, "tpartner_type": "vendor"},
]


def _ensure_site_type_definitions(config: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    raw_definitions = config.get("site_type_definitions") or []
    if not isinstance(raw_definitions, list) or not raw_definitions:
        raw_definitions = list(DEFAULT_SITE_TYPE_DEFINITIONS)

    stored_master_types: Dict[str, str] = config.get("site_master_types") or {}
    master_type_map: Dict[str, str] = {}

    def _canonical_type(value: Any) -> str:
        slug = MixedScenarioService._normalise_node_type(value)
        if slug == "market":
            return "customer"
        return slug

    def _master_type(value: Any) -> str:
        canonical = MixedScenarioService._canonical_role(value)
        if canonical in {"customer", "market_demand", "market"}:
            return "customer"
        if canonical in {"vendor", "market_supply"}:
            return "vendor"
        if canonical == "manufacturer":
            return "manufacturer"
        return canonical

    label_map: Dict[str, str] = {}
    order_hints: Dict[str, int] = {}
    observed_types: Set[str] = set()
    defined_types: Set[str] = set()

    default_label_map: Dict[str, str] = {}
    default_order_map: Dict[str, int] = {}
    for entry in DEFAULT_SITE_TYPE_DEFINITIONS:
        slug = _canonical_type(entry.get("type"))
        if not slug:
            continue
        default_label_map[slug] = entry.get("label") or slug.replace("_", " ").title()
        default_order_map[slug] = entry.get("order", 0)

    for index, entry in enumerate(raw_definitions):
        payload = entry if isinstance(entry, dict) else getattr(entry, "model_dump", lambda: entry)()
        node_type = _canonical_type(payload.get("type"))
        if not node_type:
            continue
        defined_types.add(node_type)
        label = str(payload.get("label") or "").strip()
        if not label:
            label = node_type.replace("_", " ").title()
        hint = payload.get("sequence")
        if not isinstance(hint, int):
            hint = payload.get("order")
        if not isinstance(hint, int):
            hint = index + 1
        label_map[node_type] = label
        order_hints[node_type] = hint
        master_value = payload.get("master_type")
        if master_value is None:
            master_value = stored_master_types.get(node_type)
        if master_value is not None:
            canonical_master = _master_type(master_value)
            if canonical_master:
                master_type_map[node_type] = canonical_master

    for node in config.get("nodes") or []:
        node_type = _canonical_type(node.get("type"))
        if not node_type:
            continue
        observed_types.add(node_type)
        if node_type not in label_map:
            label_map[node_type] = str(
                node.get("label")
                or node.get("display_name")
                or node.get("name")
                or node.get("id")
                or node_type.replace("_", " ").title()
            )
    if not observed_types:
        observed_types.update(defined_types)
    if not observed_types:
        observed_types.update(default_label_map.keys())

    observed_types.add("customer")
    observed_types.add("vendor")
    label_map.setdefault("customer", default_label_map.get("customer", "Customer"))
    label_map.setdefault("vendor", default_label_map.get("vendor", "Vendor"))

    fallback_start = max(order_hints.values(), default=0) + 1
    for slug in sorted(observed_types):
        if slug not in label_map:
            label_map[slug] = default_label_map.get(slug, slug.replace("_", " ").title())
        if slug not in order_hints and slug not in {"customer", "vendor"}:
            order_hints[slug] = fallback_start
            fallback_start += 1

    interior_types = [
        node_type
        for node_type in observed_types
        if node_type not in {"customer", "vendor"} and node_type
    ]
    interior_types.sort(key=lambda slug: (order_hints.get(slug, default_order_map.get(slug, 0)), slug))

    definitions: List[Dict[str, Any]] = []
    def _definition_master(slug: str, default_hint: Optional[str] = None) -> Optional[str]:
        if slug in master_type_map:
            return master_type_map.get(slug)
        return default_hint

    definitions.append(
        {
            "type": "customer",
            "label": label_map.get("customer", "Customer"),
            "sequence": 0,
            "is_required": True,
            "is_external": True,
            "tpartner_type": "customer",
        }
    )
    sequence_counter = 1
    for slug in interior_types:
        definitions.append(
            {
                "type": slug,
                "label": label_map.get(slug, slug.replace("_", " ").title()),
                "sequence": sequence_counter,
                "is_required": False,
                "is_external": False,
                "master_type": _definition_master(slug),
            }
        )
        sequence_counter += 1
    definitions.append(
        {
            "type": "vendor",
            "label": label_map.get("vendor", "Vendor"),
            "sequence": sequence_counter,
            "is_required": True,
            "is_external": True,
            "tpartner_type": "vendor",
        }
    )

    config["site_type_definitions"] = definitions
    if master_type_map:
        config["site_master_types"] = master_type_map
    elif "site_master_types" in config:
        del config["site_master_types"]
    config.setdefault(
        "site_type_labels",
        {slug: label for slug, label in label_map.items() if slug in observed_types},
    )

    return definitions, label_map


_AUTO_ADVANCE_TASKS: Dict[int, asyncio.Task] = {}
_AUTO_TASKS_LOCK = threading.Lock()


def _role_key(scenario_user: ScenarioUser) -> str:
    return str(scenario_user.role.value if hasattr(scenario_user.role, "value") else scenario_user.role).lower()


def _agent_type_for_role(role: str) -> AgentType:
    mapping = {
        "retailer": AgentType.RETAILER,
        "wholesaler": AgentType.WHOLESALER,
        "distributor": AgentType.DISTRIBUTOR,
        "manufacturer": AgentType.MANUFACTURER,
        "factory": AgentType.MANUFACTURER,
    }
    if role not in mapping:
        raise ValueError(f"Unsupported role '{role}'")
    return mapping[role]


def _strategy_for_player(scenario_user: ScenarioUser) -> AgentStrategyEnum:
    raw = (scenario_user.ai_strategy or "autonomy_dtce").lower()
    try:
        return AgentStrategyEnum(raw)
    except ValueError:
        if raw.startswith("llm"):
            return AgentStrategyEnum.LLM
        return AgentStrategyEnum.AUTONOMY_DTCE


def _coerce_scenario_config(scenario: DbGame) -> Dict[str, Any]:
    raw = scenario.config or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _debug_note(
    scenario: DbGame,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> None:
    """Write a diagnostic note into the scenario's debug log (best-effort)."""
    try:
        cfg = dict(config or _coerce_scenario_config(scenario) or {})
        cfg = _normalize_debug_config(cfg)
        # Force-enable for diagnostic notes so we always get a breadcrumb.
        cfg["enabled"] = True
        # Ensure the log file exists and path is set before writing
        _ensure_debug_log_file(cfg, scenario)
        _append_debug_error(cfg, scenario, message, details=details)
    except Exception:  # noqa: BLE001
        logger.debug("Unable to write debug note for scenario %s", getattr(scenario, "id", "?"))


def _normalize_legacy_engine_queues(config: Dict[str, Any]) -> bool:
    """Remove legacy queue fields so only inbound_demand/inbound_supply remain."""

    changed = False

    # Prefer canonical engine_state; migrate legacy "engine" if needed.
    engine_state = config.get("engine_state")
    legacy_engine = config.get("engine")

    if not isinstance(engine_state, dict) and isinstance(legacy_engine, dict):
        config["engine_state"] = dict(legacy_engine)
        engine_state = config["engine_state"]
        changed = True

    if isinstance(engine_state, dict):
        for node, state in engine_state.items():
            if not isinstance(state, dict):
                continue
            removed_keys = False
            for key in (
                "backlog_orders",
                "order_queue",
                "incoming_shipments",
                "incoming_supply",
                "ship_queue",
                "info_queue",
                "info_detail_queue",
                "incoming_orders",
            ):
                if key in state:
                    state.pop(key, None)
                    removed_keys = True
            if removed_keys:
                changed = True

    # Remove stale legacy container now that engine_state holds the canonical data.
    if "engine" in config and (config.get("engine") != config.get("engine_state")):
        config.pop("engine", None)
        changed = True

    return changed


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _save_scenario_config(db: Session, scenario: DbGame, config: Dict[str, Any]) -> None:
    # Assign a shallow copy and flag the attribute as modified so SQLAlchemy
    # persists JSON changes even when only nested keys are updated.
    scenario.config = dict(config)
    flag_modified(scenario, "config")
    db.add(scenario)


def _get_progression_mode(scenario: DbGame) -> str:
    config = _coerce_scenario_config(scenario)
    mode = config.get("progression_mode", PROGRESSION_SUPERVISED)
    if mode not in {PROGRESSION_SUPERVISED, PROGRESSION_UNSUPERVISED}:
        return PROGRESSION_SUPERVISED
    return mode


def _ensure_round(db: Session, scenario: DbGame, round_number: Optional[int] = None) -> Period:
    number = round_number or (scenario.current_round or 1)
    existing = (
        db.query(Period)
        .filter(Period.scenario_id == scenario.id, Period.round_number == number)
        .first()
    )
    if existing:
        return existing
    round_record = Period(
        scenario_id=scenario.id,
        round_number=number,
        status="in_progress",
        started_at=datetime.utcnow(),
        config={},
    )
    db.add(round_record)
    db.flush()
    return round_record


def _pending_orders(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.setdefault("pending_orders", {})


def _simulation_parameters(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.get("simulation_parameters", {})


def _all_players_submitted(db: Session, scenario: DbGame, round_record: Period) -> bool:
    player_roles = {
        str(scenario_user.role.value if hasattr(scenario_user.role, "value") else scenario_user.role).lower()
        for scenario_user in db.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
    }
    if not player_roles:
        return False

    actions = (
        db.query(ScenarioUserAction, ScenarioUser)
        .join(ScenarioUser, ScenarioUser.id == ScenarioUserAction.scenario_user_id)
        .filter(
            ScenarioUserAction.scenario_id == scenario.id,
            ScenarioUserAction.period_id == round_record.id,
            ScenarioUserAction.action_type == "order",
        )
        .all()
    )
    submitted_roles = {
        str(scenario_user.role.value if hasattr(scenario_user.role, "value") else scenario_user.role).lower()
        for _, scenario_user in actions
        if _.quantity is not None
    }
    return player_roles.issubset(submitted_roles)


def _compute_customer_demand(scenario: DbGame, round_number: int) -> int:
    config = _coerce_scenario_config(scenario)
    params = _simulation_parameters(config)
    initial = int(params.get("initial_demand", 4))
    change_week = int(params.get("demand_change_week", params.get("change_week", 20)))
    new_demand = int(params.get("new_demand", params.get("final_demand", initial)))

    pattern = config.get("demand_pattern", {})
    pattern_type = str(pattern.get("type", "classic")).lower()
    pattern_params = pattern.get("params", {})

    if pattern_type == "constant":
        return int(pattern_params.get("value", initial))

    if pattern_type == "classic":
        initial = int(pattern_params.get("initial_demand", initial))
        change_week = int(pattern_params.get("change_week", change_week))
        new_demand = int(pattern_params.get("final_demand", new_demand))
        return initial if round_number < change_week else new_demand

    if pattern_type == "seasonal":
        period = max(1, int(pattern_params.get("period", 4)))
        amplitude = float(pattern_params.get("amplitude", 2))
        base = float(pattern_params.get("base", initial))
        import math

        return max(0, int(base + amplitude * math.sin(2 * math.pi * round_number / period)))

    if pattern_type == "lognormal":
        normalized = normalize_lognormal_params(pattern_params)
        raw_seed = normalized.get("seed")
        try:
            seed = int(raw_seed) if raw_seed is not None else None
        except (TypeError, ValueError):
            seed = None

        if seed is None:
            seed = secrets.randbits(32)
            normalized["seed"] = seed

        draw_seed = seed + max(0, round_number - 1)

        try:
            samples = DemandGenerator.generate_lognormal(
                num_rounds=1,
                mean=normalized.get("mean", float(initial)),
                cov=normalized.get("cov", 1.0),
                min_demand=normalized.get("min_demand"),
                max_demand=normalized.get("max_demand"),
                stddev=normalized.get("stddev"),
                seed=draw_seed,
            )
        except Exception:
            samples = []

        demand_value = samples[0] if samples else (initial if round_number < change_week else new_demand)

        if pattern.get("params") != normalized:
            pattern = dict(pattern)
            pattern["params"] = normalized
        if config.get("demand_pattern") != pattern:
            config = dict(config)
            config["demand_pattern"] = pattern
        try:
            scenario.demand_pattern = pattern
            flag_modified(scenario, "demand_pattern")
        except Exception:
            pass
        try:
            scenario.config = config
            flag_modified(scenario, "config")
        except Exception:
            pass

        return int(demand_value)

    return initial if round_number < change_week else new_demand


def _ensure_simulation_state(config: Dict[str, Any]) -> Dict[str, Any]:
    node_policies = config.get("node_policies", {})
    lane_views = MixedScenarioService._build_lane_views(node_policies, config)

    engine = config.setdefault("engine_state", {})
    # Require at least one item to seed shipment pipelines
    cfg_items = config.get("items") or []
    primary_item_id: Optional[str] = None
    for entry in cfg_items:
        if isinstance(entry, dict):
            candidate = entry.get("id") or entry.get("item_id")
        else:
            candidate = getattr(entry, "id", None)
        if candidate is not None:
            primary_item_id = str(candidate)
            break
    if primary_item_id is None:
        raise ValueError("Supply-chain config must define at least one item id to seed pipelines")

    node_types_map = lane_views.get("node_types", {})
    orders_map = lane_views.get("orders_map", {})
    shipments_map = lane_views.get("shipments_map", {})

    all_nodes = lane_views.get("all_nodes") or []
    if not all_nodes:
        policy_nodes = [MixedScenarioService._normalise_key(name) for name in node_policies.keys()]
        engine_nodes = [MixedScenarioService._normalise_key(name) for name in engine.keys()]
        combined: List[str] = []
        seen: Set[str] = set()
        for name in policy_nodes + engine_nodes:
            if not name or name in seen:
                continue
            combined.append(name)
            seen.add(name)
        all_nodes = combined


    # Establish baseline demand statistics so every node uses the same steady-state assumptions.
    pattern = config.get("demand_pattern") or DEFAULT_DEMAND_PATTERN
    try:
        mean_demand, variance = estimate_demand_stats(pattern)
    except Exception:  # noqa: BLE001
        mean_demand, variance = 0.0, 0.0

    steady_quantity = MixedScenarioService._baseline_flow(mean_demand)
    config.setdefault("demand_statistics", {"mean": mean_demand, "variance": variance})
    config.setdefault("initial_pipeline_shipment", steady_quantity)
    config.setdefault("initial_pipeline_orders", steady_quantity)

    def _requires_pipeline(node_label: str) -> bool:
        if steady_quantity <= 0:
            return False
        node_type = node_types_map.get(node_label)
        if node_type in {"vendor", "customer"}:
            return False
        policy = MixedScenarioService._policy_for_node(node_policies, node_label)
        if isinstance(policy, dict):
            try:
                return int(policy.get("supply_leadtime", 0)) > 0
            except (TypeError, ValueError):
                return False
        return False

    def _has_positive_shipments(state: Dict[str, Any]) -> bool:
        if not isinstance(state, dict):
            return False
        shipments = state.get("incoming_shipments")
        if not isinstance(shipments, list) or not shipments:
            shipments = state.get("ship_queue")
        if not isinstance(shipments, list):
            return False
        for entry in shipments:
            try:
                if int(entry) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    pipelines_valid = True
    if engine and steady_quantity > 0:
        for node in all_nodes:
            if not _requires_pipeline(node):
                continue
            if not _has_positive_shipments(engine.get(node) or {}):
                pipelines_valid = False
                break

    needs_seed = (not engine) or (config.get("_engine_seeded") is None) or (not pipelines_valid)

    if needs_seed:
        config["demand_statistics"] = {"mean": mean_demand, "variance": variance}
        config["initial_pipeline_shipment"] = steady_quantity
        config["initial_pipeline_orders"] = steady_quantity

        orders_map = lane_views.get("orders_map", {})
        shipments_map = lane_views.get("shipments_map", {})

        for node in all_nodes:
            policy = MixedScenarioService._policy_for_node(node_policies, node)
            if not isinstance(policy, dict):
                policy = {}
                node_policies[node] = policy

            supply_leadtime = max(0, int(policy.get("supply_leadtime", 0)))
            order_leadtime = max(0, int(policy.get("order_leadtime", 0)))

            node_type = (node_types_map or {}).get(node)
            enforce_defaults = node_type not in {"vendor", "customer"}
            if enforce_defaults and order_leadtime <= 0:
                order_leadtime = 1
                policy["order_leadtime"] = order_leadtime
            if enforce_defaults and supply_leadtime <= 0:
                supply_leadtime = 2
                policy["supply_leadtime"] = supply_leadtime

            initial_conditions = MixedScenarioService._compute_initial_conditions(
                mean_demand,
                variance,
                order_leadtime=order_leadtime,
                supply_leadtime=supply_leadtime,
            )

            policy["init_inventory"] = initial_conditions["initial_inventory"]
            policy["base_stock"] = initial_conditions["base_stock"]

            state = engine.setdefault(node, {})
            state["current_step"] = 0
            state["inventory"] = initial_conditions["initial_inventory"]
            state["on_order"] = initial_conditions["on_order"]
            state["base_stock"] = initial_conditions["base_stock"]
            state["backlog"] = int(state.get("backlog", 0))

            MixedScenarioService._seed_order_queue(
                state,
                current_step=0,
                order_leadtime=order_leadtime,
                quantity=initial_conditions["steady_quantity"],
            )
            MixedScenarioService._initialise_shipment_pipeline(
                state,
                supply_leadtime=supply_leadtime,
                default_quantity=initial_conditions["steady_quantity"],
                product_id=primary_item_id,
            )
            MixedScenarioService._initialise_order_pipeline(
                state,
                order_leadtime=order_leadtime,
                default_quantity=initial_conditions["steady_quantity"],
            )

            # Seed per-item ledgers so agents see correct starting inventory/on-order
            state["inventory_by_item"] = {primary_item_id: state["inventory"]}
            on_order_total = max(0, int(state.get("on_order", 0)))
            state["on_order_by_item"] = {primary_item_id: on_order_total} if on_order_total else {}
            state["backlog_by_item"] = state.get("backlog_by_item") or {}

            upstream_sources = [u for u, downs in shipments_map.items() if node in downs]
            default_upstream = upstream_sources[0] if upstream_sources else "__upstream__"
            downstream_customers = [downstream for downstream, upstreams in orders_map.items() if node in upstreams]
            if not downstream_customers:
                downstream_customers = shipments_map.get(node, [])
            default_downstream = downstream_customers[0] if downstream_customers else None

            inbound_supply: List[Dict[str, Any]] = []
            for offset in range(max(0, supply_leadtime)):
                arrival_round = state["current_step"] + offset + 1
                inbound_supply.append(
                    {
                        "arrival_round": arrival_round,
                        "step_number": arrival_round,
                        "quantity": initial_conditions["steady_quantity"],
                        "product_id": primary_item_id,
                        "source": default_upstream,
                        "destination": node,
                    }
                )
            state["inbound_supply"] = inbound_supply

            inbound_demand: List[Dict[str, Any]] = []
            if node_type not in {"customer", "vendor"} and order_leadtime > 0 and default_downstream:
                for offset in range(order_leadtime):
                    due_round = state["current_step"] + offset + 1
                    inbound_demand.append(
                        {
                            "due_round": due_round,
                            "step_number": due_round,
                            "quantity": initial_conditions["steady_quantity"],
                            "product_id": primary_item_id,
                            "source": default_downstream,
                        }
                    )
            state["inbound_demand"] = inbound_demand

            if node_type == "vendor":
                state["inventory"] = 0
                state["on_order"] = 0

        config["_engine_seeded"] = True

    for node in all_nodes:
        state = engine.setdefault(node, {})

        if "inbound_supply" not in state:
            state["inbound_supply"] = []

        if "inbound_demand" not in state:
            state["inbound_demand"] = []

        upstream_sources = [u for u, downs in shipments_map.items() if node in downs]
        default_upstream = upstream_sources[0] if upstream_sources else "__upstream__"

        MixedScenarioService._ensure_engine_node(
            engine,
            node_policies,
            node,
            default_item_id=primary_item_id,
            default_source=default_upstream,
        )

    state = config.setdefault("simulation_state", {})
    state.setdefault("inventory", {})
    state.setdefault("backlog", {})
    state.setdefault("last_orders", {})
    state.setdefault("incoming_shipments", {})
    state.setdefault("pending_orders", {})

    # Remove legacy pipeline keys that are no longer used
    state.pop("ship_pipeline", None)
    state.pop("order_pipeline", None)
    state.pop("production_pipeline", None)

    ordered_nodes = _ordered_nodes_from_lane_views(lane_views, all_nodes)
    actor_nodes = _actor_nodes_from_order(ordered_nodes, node_types_map)

    if "initial_state" not in config:
        config["initial_state"] = {
            role: {
                "inventory": int(engine.get(role, {}).get("inventory", 0)),
                "backlog": int(engine.get(role, {}).get("backlog", 0)),
            }
            for role in actor_nodes
        }

    return state


def _log_initialisation_debug(config: Dict[str, Any], scenario: DbGame, db: Session) -> None:
    """Persist the initial state of each node to the debug log."""

    debug_cfg = _normalize_debug_config(config)
    if not debug_cfg.get("enabled"):
        config["debug_logging"] = debug_cfg
        return

    config["debug_logging"] = debug_cfg
    path = _ensure_debug_log_file(config, scenario)
    if not path:
        return

    engine_state = config.get("engine_state") or {}
    if not engine_state:
        return

    node_policies = config.get("node_policies", {})
    lane_views = MixedScenarioService._build_lane_views(node_policies, config)
    node_types = lane_views.get("node_types", {})
    all_nodes = lane_views.get("all_nodes") or list(engine_state.keys())

    stats_cfg_raw = config.get("demand_statistics")
    stats_cfg = stats_cfg_raw if isinstance(stats_cfg_raw, dict) else {}
    mean = stats_cfg.get("mean")
    variance = stats_cfg.get("variance")
    if mean is None or variance is None:
        pattern = config.get("demand_pattern") or getattr(scenario, "demand_pattern", None) or DEFAULT_DEMAND_PATTERN
        try:
            mean, variance = estimate_demand_stats(pattern)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Falling back to zeroed demand stats for scenario %s: %s", getattr(scenario, "id", "?"), exc)
            mean, variance = 0.0, 0.0

    items = config.get("items") or []
    product_name_by_id: Dict[str, str] = {}
    product_catalog_payload: List[Dict[str, Any]] = []

    def _coerce_product_entry(payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, dict):
            return {key: value for key, value in payload.items()}
        if isinstance(payload, SupplyProductModel):
            return _serialize_product(payload)
        return None

    for product in items:  # items is the input parameter name
        product_id = None
        product_name = None
        if isinstance(product, dict):
            product_id = product.get("id") or product.get("product_id") or product.get("item_id")
            product_name = product.get("name") or product.get("description")
            entry_payload = _coerce_product_entry(product)
            if entry_payload:
                product_catalog_payload.append(entry_payload)
        else:
            product_id = getattr(product, "id", None)
            product_name = getattr(product, "name", None) or getattr(product, "description", None)
            entry_payload = _coerce_product_entry(product)
            if entry_payload:
                product_catalog_payload.append(entry_payload)
        if product_id is not None and product_name:
            product_name_by_id[str(product_id)] = str(product_name)

    supply_chain_config_id = getattr(scenario, "supply_chain_config_id", None)
    if supply_chain_config_id is not None and not product_catalog_payload:
        db_products = (
            db.query(SupplyProductModel)
            .filter(SupplyProductModel.config_id == supply_chain_config_id)
            .order_by(SupplyProductModel.id.asc())
            .all()
        )
        for db_product in db_products:
            serialized = _serialize_product(db_product)
            product_catalog_payload.append(serialized)
            entry_id = serialized.get("id")
            entry_name = serialized.get("name")
            if entry_id is not None and entry_name:
                product_name_by_id.setdefault(str(entry_id), str(entry_name))

    if product_catalog_payload and not config.get("product_catalog"):
        config["product_catalog"] = product_catalog_payload

    def _annotate(queue: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        if not isinstance(queue, list):
            return []
        enriched: List[Dict[str, Any]] = []
        for entry in queue:
            if not isinstance(entry, dict):
                continue
            enriched_entry = dict(entry)
            product_id = enriched_entry.get("product_id") or enriched_entry.get("item_id")
            if product_id is not None:
                enriched_entry["product_name"] = product_name_by_id.get(str(product_id))
            enriched.append(enriched_entry)
        return enriched

    for node in all_nodes:
        state = engine_state.get(node, {})
        calc_debug: Dict[str, Any] = {}
        policy = MixedScenarioService._policy_for_node(node_policies, node)

        try:
            MixedScenarioService._compute_initial_conditions(
                mean,
                variance,
                order_leadtime=max(0, int(policy.get("order_leadtime", 0))),
                supply_leadtime=max(0, int(policy.get("supply_leadtime", 0))),
                debug_hook=lambda details: calc_debug.update(details) if isinstance(details, dict) else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Unable to compute initial condition details for scenario %s node %s: %s",
                getattr(scenario, "id", "?"),
                node,
                exc,
            )

        payload = {
            "node_type": node_types.get(node),
            "calculations": calc_debug,
            "inbound_demand": _annotate(state.get("inbound_demand") or []),
            "inbound_supply": _annotate(state.get("inbound_supply") or []),
            "starting_state": {
                "inventory": state.get("inventory"),
                "on_order": state.get("on_order"),
                "base_stock": state.get("base_stock"),
                "current_step": state.get("current_step"),
                "backlog": state.get("backlog"),
            },
        }

        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"Initial Conditions — Node '{node}'\n")
                handle.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
                handle.write("\n\n")
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Failed writing debug initialisation data for scenario %s node %s: %s",
                getattr(scenario, "id", "?"),
                node,
                exc,
            )
            break


def _finalize_round_if_ready(
    db: Session,
    scenario: DbGame,
    config: Dict[str, Any],
    round_record: Period,
    *,
    force: bool = False,
) -> bool:
    pending = _pending_orders(config)
    if not force and _get_progression_mode(scenario) != PROGRESSION_UNSUPERVISED:
        return False

    if not force and not _all_players_submitted(db, scenario, round_record):
        return False

    timestamp = datetime.utcnow()
    round_number = round_record.round_number
    external_demand = _compute_customer_demand(scenario, round_number)

    scenario_users = db.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
    players_by_role = {
        str(scenario_user.role.value if hasattr(scenario_user.role, "value") else scenario_user.role).lower(): scenario_user
        for scenario_user in scenario_users
    }

    agent_manager = AgentManager()
    configured_agents: Set[str] = set()
    overrides = config.get("autonomy_overrides") or {}
    info_sharing_cfg = config.get("info_sharing") or {}
    full_visibility = str(info_sharing_cfg.get("visibility", "")).lower() == "full"

    actions = (
        db.query(ScenarioUserAction, ScenarioUser)
        .join(ScenarioUser, ScenarioUser.id == ScenarioUserAction.scenario_user_id)
        .filter(
            ScenarioUserAction.scenario_id == scenario.id,
            ScenarioUserAction.period_id == round_record.id,
            ScenarioUserAction.action_type == "order",
        )
        .all()
    )

    orders_by_role = {}
    actions_by_role = {}
    for action, scenario_user in actions:
        role_key = str(scenario_user.role.value if hasattr(scenario_user.role, "value") else scenario_user.role).lower()
        pending_entry = pending.get(role_key, {}) if pending else {}
        orders_by_role[role_key] = {
            "scenario_user_id": scenario_user.id,
            "quantity": int(action.quantity or 0),
            "comment": pending_entry.get("comment"),
            "submitted_at": pending_entry.get("submitted_at") or action.created_at.isoformat() + "Z",
        }
        actions_by_role[role_key] = action

    state = _ensure_simulation_state(config)
    node_policies = config.get("node_policies", {})
    global_policy = config.get("global_policy", {})
    lane_views = MixedScenarioService._build_lane_views(node_policies, config)
    node_types_map = lane_views.get("node_types", {})
    all_nodes = lane_views.get("all_nodes") or [MixedScenarioService._normalise_key(name) for name in node_policies.keys()]

    display_name_map: Dict[str, str] = dict(config.get("node_display_names") or {})
    for raw_name, policy in (node_policies or {}).items():
        canonical = MixedScenarioService._normalise_key(raw_name)
        candidate: Optional[str] = None
        if isinstance(policy, dict):
            candidate = (
                policy.get("display_name")
                or policy.get("label")
                or policy.get("name")
            )
        else:
            candidate = (
                getattr(policy, "display_name", None)
                or getattr(policy, "label", None)
                or getattr(policy, "name", None)
            )
        if not candidate:
            candidate = str(raw_name).replace("_", " ").title()
        display_name_map.setdefault(canonical, str(candidate))

    # Ensure canonical market node names are present for downstream reporting
    display_name_map.setdefault("vendor", "Vendor")
    display_name_map.setdefault("customer", "Customer")
    display_name_map.setdefault("market", "Customer")

    resolved_node_types: Dict[str, str] = {}
    for node in all_nodes:
        node_type = str(node_types_map.get(node, "") or "").lower()
        if not node_type:
            if node == "vendor":
                node_type = "vendor"
            elif node in {"market", "customer"}:
                node_type = "customer"
            elif "supplier" in node:
                node_type = "supplier"
            else:
                node_type = node
        resolved_node_types[node] = node_type

    config.setdefault("node_display_names", {}).update(display_name_map)
    config.setdefault("node_types_normalized", {}).update(resolved_node_types)

    # Derive a primary item id for seeding pipelines; require at least one item.
    cfg_items = config.get("items") or []
    primary_item_id: Optional[str] = None
    for entry in cfg_items:
        if isinstance(entry, dict):
            candidate = entry.get("id") or entry.get("item_id")
        else:
            candidate = getattr(entry, "id", None)
        if candidate is not None:
            primary_item_id = str(candidate)
            break
    if primary_item_id is None:
        raise ValueError("Supply-chain config must define at least one item id to seed pipelines")

    engine = config.setdefault("engine_state", {})
    for node in all_nodes:
        MixedScenarioService._ensure_engine_node(engine, node_policies, node, default_item_id=primary_item_id)

    market_demand_nodes_types = {
        node
        for node, node_type in node_types_map.items()
        if str(node_type or "").lower() == "customer" and node in engine
    }
    market_demand_nodes_config = {
        n for n in lane_views.get("market_nodes", []) if n in engine
    }
    market_demand_nodes = market_demand_nodes_types or market_demand_nodes_config
    if not market_demand_nodes and all_nodes:
        market_demand_nodes = {all_nodes[-1]}

    pre_inventory = {node: engine[node].get("inventory", 0) for node in all_nodes}
    pre_backlog = {node: engine[node].get("backlog", 0) for node in all_nodes}
    pre_costs = {
        node: {
            "holding_cost": engine[node].get("holding_cost", 0.0),
            "backorder_cost": engine[node].get("backorder_cost", 0.0),
        }
        for node in all_nodes
    }
    previous_orders_by_node = {node: engine[node].get("last_order", 0) for node in all_nodes}
    pre_ship_queues = {
        node: _snapshot_queue(engine[node].get("ship_queue")) for node in all_nodes
    }
    pre_info_queues = {
        node: _snapshot_queue(engine[node].get("info_queue")) for node in all_nodes
    }
    pre_info_detail_queues = {
        node: _snapshot_detail_queue(engine[node].get("info_detail_queue"))
        for node in all_nodes
    }

    # Step 1: resolve inbound shipments that arrive this round.
    arrivals_map: Dict[str, int] = {}
    ship_queue_after_receipt: Dict[str, List[int]] = {}
    for node in all_nodes:
        state_node = engine[node]
        policy = MixedScenarioService._policy_for_node(node_policies, node)
        arriving, queue_snapshot = _process_ship_queue(
            state_node,
            policy,
            current_step=round_number,
        )
        arrivals_map[node] = int(arriving)
        ship_queue_after_receipt[node] = queue_snapshot

    arrivals_this_step = dict(arrivals_map)

    # Step 2: observe inbound orders (after shifting information queues).
    incoming_orders_map: Dict[str, int] = {}
    order_queue_after_receipt: Dict[str, List[int]] = {}
    order_detail_after_receipt: Dict[str, List[Dict[str, int]]] = {}
    orders_pulled_detail: Dict[str, Dict[str, int]] = {}
    for node in all_nodes:
        state_node = engine[node]
        policy = MixedScenarioService._policy_for_node(node_policies, node)
        order_leadtime = max(0, int(policy.get("order_leadtime", 0)))
        queue = _ensure_queue_length(state_node.get("info_queue"), order_leadtime)
        detail_queue = _ensure_detail_queue(state_node.get("info_detail_queue"), order_leadtime)
        observed = 0
        detail_observed: Dict[str, int] = {}
        if node in market_demand_nodes:
            observed = int(external_demand)
            detail_observed = {"external_demand": observed}
        elif order_leadtime > 0 and queue:
            observed = int(queue.pop(0))
            detail_observed = detail_queue.pop(0) if detail_queue else {}
            queue.append(0)
            detail_queue.append({})
        else:
            observed = 0
            if detail_queue:
                detail_queue = detail_queue[-order_leadtime:] if order_leadtime > 0 else []
        state_node["info_queue"] = queue
        state_node["info_detail_queue"] = detail_queue
        state_node["incoming_orders"] = observed
        incoming_orders_map[node] = observed
        orders_pulled_detail[node] = detail_observed
        order_queue_after_receipt[node] = _snapshot_queue(queue)
        order_detail_after_receipt[node] = [dict(slot) for slot in detail_queue]

    post_info_queues: Dict[str, List[int]] = {
        node: list(order_queue_after_receipt.get(node, [])) for node in all_nodes
    }
    post_info_detail_queues: Dict[str, List[Dict[str, int]]] = {
        node: list(order_detail_after_receipt.get(node, [])) for node in all_nodes
    }

    orders_due_by_source: Dict[str, Dict[str, int]] = {
        node: dict(orders_pulled_detail.get(node, {})) for node in all_nodes
    }

    hold_cost = float(global_policy.get("holding_cost", 0.5))
    back_cost = float(global_policy.get("backlog_cost", 1.0))

    node_sequence = lane_views.get("node_sequence") or all_nodes
    config["node_sequence"] = list(node_sequence)
    ordered_nodes = _ordered_nodes_from_lane_views(lane_views, all_nodes)
    actor_nodes = _actor_nodes_from_order(ordered_nodes, resolved_node_types)
    lanes_by_upstream = lane_views.get("lanes_by_upstream", {})
    shipments_map = lane_views.get("shipments_map", {})
    orders_map = lane_views.get("orders_map", {})

    orders_timestamp_iso = timestamp.isoformat() + "Z"

    round_debug_entries: Dict[str, Dict[str, Any]] = {}
    round_debug_order: List[str] = []

    node_orders_new: Dict[str, int] = {}
    shipments_planned: Dict[str, int] = defaultdict(int)
    shipments_inbound: Dict[str, int] = defaultdict(int)
    shipment_allocations: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    shipment_sources: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    demand_totals: Dict[str, int] = {}
    inventory_after_shipping: Dict[str, int] = {}
    backlog_after_shipping: Dict[str, int] = {}
    available_before_shipping: Dict[str, int] = {}
    shipping_inputs: Dict[str, Dict[str, int]] = {}

    order_queue_impacts: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    processing_nodes: List[str] = []
    for node in reversed(node_sequence):
        if node not in processing_nodes:
            processing_nodes.append(node)
    for node in all_nodes:
        if node not in processing_nodes:
            processing_nodes.append(node)

    for node_key in processing_nodes:
        state_node = engine[node_key]
        node_type = node_types_map.get(node_key, "")
        arrivals_now = int(arrivals_this_step.get(node_key, 0))
        inventory_before = int(pre_inventory.get(node_key, 0))
        backlog_before = int(pre_backlog.get(node_key, 0))

        incoming_now = int(incoming_orders_map.get(node_key, 0))
        (
            shipped_now,
            inventory_after,
            backlog_after,
            demand_now,
            available_now,
        ) = _compute_shipping_outcome(
            node_type=node_type,
            inventory_before=inventory_before,
            backlog_before=backlog_before,
            arrivals_now=arrivals_now,
            incoming_now=incoming_now,
        )
        available_before_shipping[node_key] = available_now
        shipping_inputs[node_key] = {
            "inventory_before": inventory_before,
            "backlog_before": backlog_before,
            "incoming_now": incoming_now,
            "arrivals_now": arrivals_now,
        }

        state_node["incoming_orders"] = incoming_now
        state_node["last_arrival"] = arrivals_now
        state_node["inventory"] = inventory_after
        state_node["backlog"] = backlog_after

        demand_totals[node_key] = demand_now
        inventory_after_shipping[node_key] = inventory_after
        backlog_after_shipping[node_key] = backlog_after
        shipments_planned[node_key] = shipped_now

        inventory_before_ship = inventory_before
        backlog_before_ship = backlog_before
        total_demand_now = demand_now
        pipeline_snapshot = state_node.get("incoming_shipments")
        if isinstance(pipeline_snapshot, list):
            incoming_pipeline = [int(x) for x in pipeline_snapshot]
        elif pipeline_snapshot is None:
            incoming_pipeline = []
        else:
            incoming_pipeline = [int(pipeline_snapshot)]
        state_node["incoming_shipments"] = incoming_pipeline

        def _coerce_int_dict(payload: Any) -> Dict[str, int]:
            if not isinstance(payload, dict):
                return {}
            result: Dict[str, int] = {}
            for key, value in payload.items():
                try:
                    result[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
            return result

        inventory_by_item = _coerce_int_dict(state_node.get("inventory_by_item"))
        backlog_by_item = _coerce_int_dict(state_node.get("backlog_by_item"))
        on_order_by_item = _coerce_int_dict(state_node.get("on_order_by_item"))
        backlog_orders_raw = state_node.get("backlog_orders")
        backlog_orders: List[Dict[str, Any]] = []
        if isinstance(backlog_orders_raw, list):
            for entry in backlog_orders_raw:
                if not isinstance(entry, dict):
                    continue
                try:
                    qty_val = int(entry.get("quantity", 0) or 0)
                except (TypeError, ValueError):
                    qty_val = 0
                backlog_orders.append(
                    {
                        "downstream": entry.get("downstream"),
                        "product_id": entry.get("product_id") or entry.get("item_id"),
                        "quantity": qty_val,
                        "order_priority": entry.get("order_priority", 1),
                    }
                )

        scenario_user = players_by_role.get(node_key)
        agent_type = ROLE_TO_AGENT_TYPE.get(node_key)
        order_qty = 0
        decision_comment: Optional[str] = None
        info_payload: Optional[Dict[str, Any]] = None
        reply_payload: Optional[Dict[str, Any]] = None

        if node_type == "vendor":
            state_node["holding_cost"] = 0.0
            state_node["backorder_cost"] = 0.0
            state_node["total_cost"] = 0.0
            continue

        if node_type == "customer":
            state_node["holding_cost"] = 0.0
            state_node["backorder_cost"] = state_node.get("backorder_cost", 0.0) + backlog_after * back_cost
            state_node["total_cost"] = state_node["backorder_cost"]
            continue

        # Holding/backlog costs for other nodes will be finalised after shipments are scheduled.

        if scenario_user is None:
            continue

        existing_entry = orders_by_role.get(node_key)
        if existing_entry and not bool(getattr(scenario_user, "is_ai", False)):
            try:
                order_qty = max(0, int(existing_entry.get("quantity") or 0))
            except (TypeError, ValueError):
                order_qty = 0
        else:
            if not agent_type:
                order_qty = 0
            else:
                if node_key not in configured_agents:
                    strategy_value = (scenario_user.ai_strategy or "naive").lower()
                    try:
                        strategy_enum = AgentStrategyEnum(strategy_value)
                    except ValueError:
                        if strategy_value.startswith("llm"):
                            strategy_enum = AgentStrategyEnum.LLM
                        else:
                            strategy_enum = AgentStrategyEnum.NAIVE
                    override_pct = overrides.get(node_key)
                    agent_manager.set_agent_strategy(
                        agent_type,
                        strategy_enum,
                        llm_model=scenario_user.llm_model,
                        override_pct=override_pct,
                        llm_strategy=getattr(scenario_user, "llm_strategy", None),
                    )
                    configured_agents.add(node_key)
                agent = agent_manager.get_agent(agent_type) if agent_type else None
                downstream_nodes = shipments_map.get(node_key, [])
                downstream_orders_latest = {
                    MixedScenarioService._normalise_key(down): node_orders_new.get(down, 0)
                    for down in downstream_nodes
                }
                previous_downstream_orders = {
                    MixedScenarioService._normalise_key(down): previous_orders_by_node.get(down, 0)
                    for down in downstream_nodes
                }
                previous_orders_seq = list(previous_downstream_orders.values())
                local_state = {
                    "inventory": inventory_before_ship,
                    "backlog": backlog_before_ship,
                    "incoming_shipments": incoming_pipeline,
                    "incoming_arrivals": arrivals_now,
                    "incoming_orders": incoming_now,
                    "total_demand": total_demand_now,
                    "inventory_after_shipment": inventory_after,
                    "backlog_after_shipment": backlog_after,
                    "shipments_planned": shipped_now,
                    "inventory_by_item": inventory_by_item,
                    "backlog_by_item": backlog_by_item,
                    "on_order_by_item": on_order_by_item,
                }
                if backlog_orders:
                    local_state["backlog_orders"] = backlog_orders
                if full_visibility:
                    local_state["downstream_orders"] = list(downstream_orders_latest.values())
                observed_demand = incoming_now
                if node_key in market_demand_nodes:
                    current_demand_value = int(external_demand)
                elif scenario_user.can_see_demand or full_visibility:
                    current_demand_value = observed_demand
                else:
                    current_demand_value = None
                upstream_context = {
                    "previous_orders": previous_orders_seq,
                    "previous_orders_by_role": previous_downstream_orders,
                    "downstream_orders": downstream_orders_latest,
                }
                llm_payload = None
                if strategy_enum in (
                    AgentStrategyEnum.LLM,
                    AgentStrategyEnum.LLM_SUPERVISED,
                    AgentStrategyEnum.LLM_GLOBAL,
                ):
                    try:
                        llm_payload = build_llm_decision_payload(
                            db,
                            scenario,
                            round_number=round_number,
                            action_role=node_key,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "Failed to build Autonomy LLM payload | scenario=%s role=%s strategy=%s",
                            getattr(scenario, "id", "?"),
                            node_key,
                            strategy_enum.value,
                        )
                    else:
                        upstream_context["llm_payload"] = llm_payload
                info_payload = {
                    "current_round": round_number,
                    "current_demand": current_demand_value,
                    "local_state": dict(local_state),
                    "upstream_context": upstream_context,
                }
                if llm_payload is not None:
                    info_payload["llm_payload"] = llm_payload
                decision = agent.make_decision(
                    current_round=round_number,
                    current_demand=current_demand_value,
                    upstream_data=upstream_context,
                    local_state=local_state,
                ) if agent else 0
                if isinstance(decision, AgentDecision):
                    order_value = decision.quantity
                    decision_comment = decision.reason
                else:
                    order_value = decision
                try:
                    order_qty = max(0, int(round(order_value)))
                except (TypeError, ValueError):
                    order_qty = 0
                if agent:
                    decision_comment = decision_comment or agent.get_last_explanation_comment()

        node_orders_new[node_key] = order_qty
        state_node["last_order"] = order_qty
        state_node["on_order"] = max(
            0,
            state_node.get("on_order", 0) + order_qty - arrivals_now,
        )

        entry = orders_by_role.get(node_key, {
            "scenario_user_id": scenario_user.id,
            "quantity": order_qty,
            "comment": None,
            "submitted_at": orders_timestamp_iso,
        })
        entry["scenario_user_id"] = scenario_user.id
        entry["quantity"] = order_qty
        entry.setdefault("submitted_at", orders_timestamp_iso)
        if decision_comment:
            entry["comment"] = decision_comment
        orders_by_role[node_key] = entry

        reply_payload = {
            "type": "agent_decision"
            if bool(getattr(scenario_user, "is_ai", False))
            else "human_input",
            "order_quantity": order_qty,
            "submitted_at": entry.get("submitted_at"),
        }
        if entry.get("comment"):
            reply_payload["comment"] = entry["comment"]

        if info_payload is None:
            downstream_nodes = shipments_map.get(node_key, [])
            downstream_orders_latest = {
                MixedScenarioService._normalise_key(down): node_orders_new.get(down, 0)
                for down in downstream_nodes
            }
            previous_downstream_orders = {
                MixedScenarioService._normalise_key(down): previous_orders_by_node.get(down, 0)
                for down in downstream_nodes
            }
            info_payload = {
                "current_round": round_number,
                "current_demand": incoming_now,
                "local_state": {
                    "inventory": inventory_before_ship,
                    "backlog": backlog_before_ship,
                    "incoming_shipments": incoming_pipeline,
                    "incoming_arrivals": arrivals_now,
                    "total_demand": total_demand_now,
                    "inventory_after_shipment": inventory_after,
                    "backlog_after_shipment": backlog_after,
                    "shipments_planned": shipped_now,
                },
                "upstream_context": {
                    "previous_orders": list(previous_downstream_orders.values()),
                    "previous_orders_by_role": previous_downstream_orders,
                    "downstream_orders": downstream_orders_latest,
                },
            }

        player_info = {
            "id": getattr(scenario_user, "id", None),
            "name": getattr(scenario_user, "name", None),
            "is_ai": bool(getattr(scenario_user, "is_ai", False)),
        }

        debug_entry = {
            "node": node_key,
            "scenario_user": player_info,
            "info_sent": info_payload,
            "reply": reply_payload,
        }
        round_debug_entries[node_key] = debug_entry
        if node_key not in round_debug_order:
            round_debug_order.append(node_key)

        action_obj = actions_by_role.get(node_key)
        if action_obj is None:
            action_obj = ScenarioUserAction(
                scenario_id=scenario.id,
                period_id=round_record.id,
                scenario_user_id=scenario_user.id,
                action_type="order",
                quantity=order_qty,
                created_at=timestamp,
            )
            db.add(action_obj)
        else:
            action_obj.quantity = order_qty
            action_obj.created_at = timestamp
        actions_by_role[node_key] = action_obj

        upstream_candidates = orders_map.get(node_key, [])
        if upstream_candidates:
            share_count = max(1, len(upstream_candidates))
            base_share, remainder = divmod(order_qty, share_count)
            for idx, upstream in enumerate(upstream_candidates):
                upstream_key = MixedScenarioService._normalise_key(upstream)
                push_qty = base_share + (1 if idx < remainder else 0)
                if push_qty <= 0:
                    continue
                policy_up = MixedScenarioService._policy_for_node(node_policies, upstream_key)
                order_leadtime_up = max(0, int(policy_up.get("order_leadtime", 0)))
                state_upstream = engine.setdefault(upstream_key, {})
                if order_leadtime_up <= 0:
                    incoming_orders_map[upstream_key] = incoming_orders_map.get(upstream_key, 0) + push_qty
                    orders_due_by_source.setdefault(upstream_key, {})
                    orders_due_by_source[upstream_key][node_key] = (
                        orders_due_by_source[upstream_key].get(node_key, 0) + push_qty
                    )
                    order_queue_impacts[node_key].append(
                        {
                            "to": upstream_key,
                            "order_quantity": push_qty,
                            "upstream_queue_after": [],
                            "immediate_demand": incoming_orders_map.get(upstream_key, 0),
                        }
                    )
                    post_info_queues[upstream_key] = []
                    post_info_detail_queues[upstream_key] = []
                else:
                    queue_now = _ensure_queue_length(state_upstream.get("info_queue"), order_leadtime_up)
                    detail_queue = _ensure_detail_queue(state_upstream.get("info_detail_queue"), order_leadtime_up)
                    if not queue_now:
                        queue_now = [0] * order_leadtime_up
                    queue_now[-1] += push_qty
                    detail_slot = detail_queue[-1]
                    detail_slot[node_key] = detail_slot.get(node_key, 0) + push_qty
                    state_upstream["info_queue"] = queue_now
                    state_upstream["info_detail_queue"] = detail_queue
                    queue_snapshot = _snapshot_queue(queue_now)
                    detail_snapshot = [dict(slot) for slot in detail_queue]
                    post_info_queues[upstream_key] = queue_snapshot
                    post_info_detail_queues[upstream_key] = detail_snapshot
                    order_queue_impacts[node_key].append(
                        {
                            "to": upstream_key,
                            "order_quantity": push_qty,
                            "upstream_queue_after": queue_snapshot,
                            "upstream_detail_after": detail_snapshot,
                        }
                    )

                orders_due_by_source.setdefault(upstream_key, {})

    # Step 3: schedule shipments into transport queues for downstream nodes.
    post_ship_queues: Dict[str, List[int]] = {node: ship_queue_after_receipt.get(node, []) for node in all_nodes}

    def _recompute_after_immediate_arrival(node_id: str) -> None:
        inputs = shipping_inputs.get(node_id)
        if not inputs:
            return
        arrivals_total = int(arrivals_this_step.get(node_id, inputs.get("arrivals_now", 0)))
        (
            shipped_now,
            inventory_after,
            backlog_after,
            demand_now,
            available_now,
        ) = _compute_shipping_outcome(
            node_type=node_types_map.get(node_id, ""),
            inventory_before=inputs["inventory_before"],
            backlog_before=inputs["backlog_before"],
            arrivals_now=arrivals_total,
            incoming_now=inputs["incoming_now"],
        )
        shipments_planned[node_id] = shipped_now
        inventory_after_shipping[node_id] = inventory_after
        backlog_after_shipping[node_id] = backlog_after
        available_before_shipping[node_id] = available_now
        demand_totals[node_id] = demand_now
        state_node = engine.get(node_id)
        if isinstance(state_node, dict):
            state_node["inventory"] = inventory_after
            state_node["backlog"] = backlog_after

    for upstream in node_sequence:
        shipped_total = int(shipments_planned.get(upstream, 0))
        if shipped_total <= 0:
            engine[upstream]["last_shipment_planned"] = 0
            continue
        engine[upstream]["last_shipment_planned"] = shipped_total
        downstream_lanes = lanes_by_upstream.get(upstream, [])
        if not downstream_lanes:
            continue
        remaining = shipped_total
        for lane in downstream_lanes:
            downstream = lane["to"]
            requested = orders_due_by_source.get(upstream, {}).get(downstream, 0)
            allocate = min(remaining, requested) if requested > 0 else remaining
            allocate = max(0, allocate)
            if allocate == 0:
                continue
            shipment_allocations[upstream].append({"to": downstream, "quantity": allocate})
            shipment_sources[downstream].append({"from": upstream, "quantity": allocate})
            shipments_inbound[downstream] += allocate
            remaining -= allocate
            policy_down = MixedScenarioService._policy_for_node(node_policies, downstream)
            supply_delay = max(0, int(policy_down.get("supply_leadtime", 0)))
            state_downstream = engine[downstream]
            if supply_delay > 0:
                current_step_raw = state_downstream.get("current_step", round_number)
                try:
                    current_step_base = int(current_step_raw)
                except (TypeError, ValueError):
                    current_step_base = round_number
                supply_schedule = _normalize_inbound_supply_queue(
                    state_downstream.get("inbound_supply_future"),
                    current_step=current_step_base,
                    fallback=None,
                    supply_leadtime=supply_delay,
                )
                if not supply_schedule:
                    legacy_queue = _ensure_queue_length(
                        state_downstream.get("ship_queue"), supply_delay
                    )
                    if legacy_queue:
                        for offset, qty in enumerate(legacy_queue, start=1):
                            qty_int = int(qty)
                            if qty_int <= 0:
                                continue
                            supply_schedule.append(
                                {
                                    "step_number": current_step_base + offset,
                                    "quantity": qty_int,
                                    "product_id": primary_item_id,
                                }
                            )
                supply_schedule.append(
                    {
                        "step_number": round_number + supply_delay,
                        "quantity": int(allocate),
                        "source": upstream,
                        "product_id": primary_item_id,
                    }
                )
                _sort_inbound_supply_queue(supply_schedule)
                state_downstream["inbound_supply_future"] = supply_schedule
                snapshot = _summarise_inbound_supply_queue(
                    supply_schedule,
                    current_step=current_step_base,
                    supply_leadtime=supply_delay,
                )
                state_downstream["ship_queue"] = list(snapshot)
                state_downstream["ship_detail_queue"] = _summarise_inbound_supply_detail(
                    supply_schedule,
                    current_step=current_step_base,
                    supply_leadtime=supply_delay,
                )
                state_downstream["incoming_shipments"] = list(snapshot)
                post_ship_queues[downstream] = list(snapshot)
            else:
                arrivals_this_step[downstream] = arrivals_this_step.get(downstream, 0) + allocate
                _recompute_after_immediate_arrival(downstream)
            if remaining <= 0:
                break

    for node in all_nodes:
        node_type = node_types_map.get(node, "")
        state_node = engine[node]
        arriving = arrivals_this_step.get(node, 0)
        if node_type in {"vendor", "customer"}:
            continue
        available_after_ship = max(0, inventory_after_shipping.get(node, 0))
        backlog_after = max(0, backlog_after_shipping.get(node, 0))
        state_node["inventory"] = available_after_ship
        state_node["backlog"] = backlog_after
        state_node["last_arrival"] = arriving
        state_node["holding_cost"] = state_node.get("holding_cost", 0.0) + available_after_ship * hold_cost
        state_node["backorder_cost"] = state_node.get("backorder_cost", 0.0) + backlog_after * back_cost
        state_node["total_cost"] = state_node["holding_cost"] + state_node["backorder_cost"]

    debug_info_queues: Dict[str, List[int]] = {
        node: list(post_info_queues.get(node, [])) for node in all_nodes
    }

    for role in actor_nodes:
        orders_by_role.setdefault(
            role,
            {
                "scenario_user_id": players_by_role.get(role).id if players_by_role.get(role) else None,
                "quantity": engine.get(role, {}).get("last_order", 0),
                "comment": None,
                "submitted_at": orders_timestamp_iso,
            },
        )

    for node in all_nodes:
        if node in round_debug_entries:
            continue
        scenario_user = players_by_role.get(node)
        if not scenario_user:
            continue
        recorded = orders_by_role.get(node, {})
        reply_payload = {
            "type": "human_input"
            if not bool(getattr(scenario_user, "is_ai", False))
            else "recorded_order",
            "order_quantity": int(recorded.get("quantity") or 0),
            "submitted_at": recorded.get("submitted_at"),
        }
        if recorded.get("comment"):
            reply_payload["comment"] = recorded["comment"]
        info_payload = {
            "current_round": round_number,
            "note": "Order received from scenario_user submission",
            "observed_demand": int(incoming_orders_map.get(node, 0)),
            "inventory_before": int(
                pre_inventory.get(node, engine.get(node, {}).get("inventory", 0))
            ),
            "backlog_before": int(
                pre_backlog.get(node, engine.get(node, {}).get("backlog", 0))
            ),
        }
        debug_entry = {
            "node": node,
            "scenario_user": {
                "id": getattr(scenario_user, "id", None),
                "name": getattr(scenario_user, "name", None),
                "is_ai": bool(getattr(scenario_user, "is_ai", False)),
            },
            "info_sent": info_payload,
            "reply": reply_payload,
        }
        round_debug_entries[node] = debug_entry
        if node not in round_debug_order:
            round_debug_order.append(node)

    market_demand_value = _safe_int(external_demand)
    market_entry = round_debug_entries.get("market")
    market_step_trace = [
        {"step": "Starting state", "demand": market_demand_value},
        {"step": "Demand issued", "demand": market_demand_value},
        {"step": "Ending state", "demand_sent": market_demand_value},
    ]
    if not market_entry:
        market_entry = {
            "node": "market",
            "scenario_user": {"name": "Customer", "is_ai": False},
            "info_sent": {
                "current_round": round_number,
                "external_demand": market_demand_value,
            },
            "reply": {"type": "customer", "demand": market_demand_value},
            "ending_state": {"demand_transmitted": market_demand_value},
        }
        round_debug_entries["market"] = market_entry
    else:
        market_entry.setdefault(
            "ending_state", {"demand_transmitted": market_demand_value}
        )
    market_entry["step_trace"] = market_step_trace
    if "market" not in round_debug_order:
        round_debug_order.insert(0, "market")

    step_traces: Dict[str, List[Dict[str, Any]]] = {}
    role_stats = {}
    for role in actor_nodes:
        node_state = engine.get(role, {})
        before_inv = pre_inventory.get(role, 0)
        before_backlog = pre_backlog.get(role, 0)
        inv_after = node_state.get("inventory", 0)
        backlog_after = node_state.get("backlog", 0)
        order_qty = node_state.get("last_order", 0)
        shipped_qty = shipments_planned.get(role, 0)
        arrivals_qty = arrivals_this_step.get(role, 0)
        demand_here = incoming_orders_map.get(role, 0)
        holding_cost_delta = float(node_state.get("holding_cost", 0.0) - pre_costs.get(role, {}).get("holding_cost", 0.0))
        backlog_cost_delta = float(node_state.get("backorder_cost", 0.0) - pre_costs.get(role, {}).get("backorder_cost", 0.0))
        total_cost_delta = holding_cost_delta + backlog_cost_delta
        available_before_ship = available_before_shipping.get(role, before_inv)
        inventory_after_ship = inventory_after_shipping.get(role, inv_after)
        shipments_detail = shipment_allocations.get(role, [])
        order_impacts = order_queue_impacts.get(role, [])
        queue_after_receipt = order_queue_after_receipt.get(role, [])
        ship_queue_mid = ship_queue_after_receipt.get(role, [])
        post_order_queue = post_info_queues.get(role, [])
        post_order_detail = post_info_detail_queues.get(role, [])
        pulled_detail = orders_pulled_detail.get(role, {})
        role_stats[role] = {
            "inventory_before": before_inv,
            "inventory_after": inv_after,
            "backlog_before": before_backlog,
            "backlog_after": backlog_after,
            "demand": demand_here,
            "shipped": shipped_qty,
            "arrivals": arrivals_qty,
            "order": order_qty,
            "available_before_ship": available_before_ship,
            "shipments_detail": shipments_detail,
            "order_impacts": order_impacts,
            "holding_cost": round(holding_cost_delta, 2),
            "backlog_cost": round(backlog_cost_delta, 2),
            "total_cost": round(total_cost_delta, 2),
        }
        step_traces[role] = [
            {
                "step": "Starting state",
                "inventory": int(before_inv),
                "backlog": int(before_backlog),
                "order_queue": pre_info_queues.get(role, []),
                "order_detail_queue": pre_info_detail_queues.get(role, []),
                "arrival_queue": pre_ship_queues.get(role, []),
            },
            {
                "step": "Inbound shipment processed",
                "arrivals": int(arrivals_qty),
                "arrival_queue_before_schedule": ship_queue_mid,
                "arrival_queue_after_schedule": post_ship_queues.get(role, []),
                "inventory_after_arrival": int(available_before_ship),
                "shipments_scheduled_to_arrive": shipment_sources.get(role, []),
            },
            {
                "step": "Incoming order observed",
                "incoming_orders": int(demand_here),
                "order_queue_before_observation": pre_info_queues.get(role, []),
                "order_queue_after_observation": queue_after_receipt,
                "order_detail_queue_before": pre_info_detail_queues.get(role, []),
                "order_detail_queue_after": order_detail_after_receipt.get(role, []),
                "order_detail": pulled_detail,
            },
            {
                "step": "Shipment this step",
                "available_inventory": int(available_before_ship),
                "demand": int(demand_totals.get(role, demand_here + before_backlog)),
                "shipped": int(shipped_qty),
                "shipment_breakdown": shipments_detail,
                "inventory_after_shipment": int(inventory_after_ship),
            },
            {
                "step": "Order placement",
                "order_quantity": int(order_qty),
                "orders_sent": order_impacts,
                "order_queue_after_submission": post_order_queue,
                "order_detail_after_submission": post_order_detail,
            },
            {
                "step": "Ending state",
                "inventory": int(inv_after),
                "backlog": int(backlog_after),
                "order_queue": debug_info_queues.get(role, post_order_queue),
                "arrival_queue": post_ship_queues.get(role, []),
            },
        ]

    node_stats: Dict[str, Dict[str, Any]] = {}
    for node in all_nodes:
        base_stats = dict(role_stats.get(node, {})) if node in role_stats else {}
        inventory_before_val = int(pre_inventory.get(node, base_stats.get("inventory_before", 0)))
        backlog_before_val = int(pre_backlog.get(node, base_stats.get("backlog_before", 0)))
        node_state = engine.get(node, {})
        inventory_after_val = int(
            node_state.get(
                "inventory",
                inventory_after_shipping.get(node, base_stats.get("inventory_after", 0)),
            )
        )
        backlog_after_val = int(
            node_state.get(
                "backlog",
                backlog_after_shipping.get(node, base_stats.get("backlog_after", 0)),
            )
        )
        order_qty_val = int(
            node_orders_new.get(
                node,
                base_stats.get(
                    "order",
                    node_state.get("last_order", previous_orders_by_node.get(node, 0)),
                ),
            )
        )
        arrivals_val = int(arrivals_this_step.get(node, base_stats.get("arrivals", 0)))
        shipped_val = int(shipments_planned.get(node, base_stats.get("shipped", 0)))
        demand_val = int(
            incoming_orders_map.get(
                node,
                demand_totals.get(node, base_stats.get("demand", 0)),
            )
        )
        holding_cost_delta = float(node_state.get("holding_cost", 0.0)) - float(
            pre_costs.get(node, {}).get("holding_cost", 0.0)
        )
        backlog_cost_delta = float(node_state.get("backorder_cost", 0.0)) - float(
            pre_costs.get(node, {}).get("backorder_cost", 0.0)
        )
        total_cost_delta = holding_cost_delta + backlog_cost_delta

        stats_payload = {
            **base_stats,
            "inventory_before": inventory_before_val,
            "inventory_after": inventory_after_val,
            "backlog_before": backlog_before_val,
            "backlog_after": backlog_after_val,
            "demand": demand_val,
            "shipped": shipped_val,
            "arrivals": arrivals_val,
            "order": order_qty_val,
            "holding_cost": round(holding_cost_delta, 2),
            "backlog_cost": round(backlog_cost_delta, 2),
            "total_cost": round(total_cost_delta, 2),
            "shipments_detail": shipment_allocations.get(node, base_stats.get("shipments_detail", [])),
            "order_impacts": order_queue_impacts.get(node, base_stats.get("order_impacts", [])),
        }

        stats_payload["type"] = resolved_node_types.get(node, base_stats.get("type"))
        stats_payload["display_name"] = display_name_map.get(
            node, base_stats.get("display_name", node.replace("_", " ").title())
        )

        node_stats[node] = stats_payload

    for node, debug_entry in round_debug_entries.items():
        if node == "market":
            continue
        stats = node_stats.get(node)
        node_state = engine.get(node, {})
        pipeline_shipments = node_state.get("incoming_shipments", [])
        if isinstance(pipeline_shipments, list):
            pipeline_list = [int(x) for x in pipeline_shipments]
            pipeline_total = sum(pipeline_list)
        else:
            pipeline_total = int(pipeline_shipments or 0)
            pipeline_list = []

        ending_state = {
            "inventory": int(
                node_state.get("inventory", stats["inventory_after"] if stats else 0)
            ),
            "backlog": int(
                node_state.get("backlog", stats["backlog_after"] if stats else 0)
            ),
            "last_order": int(node_state.get("last_order", 0)),
            "on_order": int(node_state.get("on_order", 0)),
            "incoming_shipments": pipeline_list,
            "incoming_shipments_total": pipeline_total,
            "observed_demand": int(
                incoming_orders_map.get(node, stats["demand"] if stats else 0)
            ),
        }
        if stats:
            ending_state.update(
                {
                    "shipped": int(stats.get("shipped", 0)),
                    "arrivals": int(stats.get("arrivals", 0)),
                    "holding_cost": stats.get("holding_cost"),
                    "backlog_cost": stats.get("backlog_cost"),
                    "total_cost_delta": stats.get("total_cost"),
                    "shipment_breakdown": stats.get("shipments_detail"),
                    "orders_sent": stats.get("order_impacts"),
                }
            )
            ending_state.update(
                {
                    "order_queue": debug_info_queues.get(node, post_info_queues.get(node, [])),
                    "arrival_queue": post_ship_queues.get(node, []),
                }
            )
        else:
            ending_state.update(
                {
                    "order_queue": debug_info_queues.get(node, post_info_queues.get(node, [])),
                    "arrival_queue": post_ship_queues.get(node, []),
                }
            )
        step_trace = step_traces.get(node)
        if step_trace:
            debug_entry["step_trace"] = step_trace
        debug_entry["ending_state"] = ending_state

    state_inventory = state.setdefault("inventory", {})
    state_backlog = state.setdefault("backlog", {})
    state_last_orders = state.setdefault("last_orders", {})
    state_incoming = state.setdefault("incoming_shipments", {})
    state_pending_orders = state.setdefault("pending_orders", {})
    for role in actor_nodes:
        stats = role_stats.get(role)
        if not stats:
            continue
        state_inventory[role] = int(stats["inventory_after"])
        state_backlog[role] = int(stats["backlog_after"])
        state_last_orders[role] = int(stats["order"])
        state_incoming[role] = int(stats["arrivals"])
        state_pending_orders[role] = int(stats["demand"])

    if pending is not None:
        pending.clear()

    preferred_order: List[str] = []
    market_nodes = list(lane_views.get("market_nodes") or [])
    for candidate in market_nodes + actor_nodes:
        if candidate not in preferred_order:
            preferred_order.append(candidate)
    if "market" in round_debug_entries and "market" not in preferred_order:
        preferred_order.insert(0, "market")
    ordered_debug_entries: List[Dict[str, Any]] = []
    for node in preferred_order:
        entry = round_debug_entries.get(node)
        if entry and entry not in ordered_debug_entries:
            ordered_debug_entries.append(entry)
    for node in round_debug_order:
        entry = round_debug_entries.get(node)
        if entry and entry not in ordered_debug_entries:
            ordered_debug_entries.append(entry)
    for node, entry in round_debug_entries.items():
        if entry not in ordered_debug_entries:
            ordered_debug_entries.append(entry)

    def _coerce_positive_int(value: Any) -> int:
        try:
            numeric = int(round(float(value)))
        except (TypeError, ValueError):
            return 0
        return max(0, numeric)

    def _register_flow(store: Dict[str, Dict[str, int]], source: str, target: str, value: Any) -> None:
        qty = _coerce_positive_int(value)
        if qty <= 0:
            return
        store.setdefault(source, {})[target] = qty

    shipments_map: Dict[str, Dict[str, int]] = {}
    for upstream, allocations in shipment_allocations.items():
        for allocation in allocations:
            if not isinstance(allocation, dict):
                continue
            _register_flow(
                shipments_map,
                upstream,
                allocation.get("to"),
                allocation.get("quantity"),
            )

    node_orders_payload: Dict[str, Dict[str, Any]] = {}
    combined_nodes_for_orders = set(all_nodes) | set(orders_by_role.keys())
    for node in combined_nodes_for_orders:
        base_entry = dict(orders_by_role.get(node, {}))
        if "quantity" in base_entry:
            try:
                base_entry["quantity"] = int(base_entry.get("quantity") or 0)
            except (TypeError, ValueError):
                base_entry["quantity"] = 0
        else:
            base_entry["quantity"] = int(node_stats.get(node, {}).get("order", 0))
        # For market demand nodes, rely on their own recorded orders (not an external
        # demand echo) so we don’t double-count customer demand in UI summaries.
        node_type = str(node_stats.get(node, {}).get("type") or resolved_node_types.get(node) or "").lower()
        if node_type == "customer":
            base_entry["quantity"] = int(node_stats.get(node, {}).get("order", base_entry.get("quantity", 0)) or 0)
        base_entry.setdefault("type", node_stats.get(node, {}).get("type", resolved_node_types.get(node)))
        base_entry.setdefault(
            "display_name",
            node_stats.get(node, {}).get(
                "display_name",
                display_name_map.get(node, node.replace("_", " ").title()),
            ),
        )
        node_orders_payload[node] = base_entry

    node_type_summaries: Dict[str, Dict[str, Any]] = {}
    for node, stats in node_stats.items():
        node_type = str(stats.get("type") or resolved_node_types.get(node) or "").lower()
        if not node_type:
            continue
        summary = node_type_summaries.setdefault(
            node_type,
            {
                "orders": 0.0,
                "shipments": 0.0,
                "arrivals": 0.0,
                "demand": 0.0,
                "orders_satisfied": 0.0,
                "inventory_after": 0.0,
                "backlog_after": 0.0,
                "holding_cost": 0.0,
                "backlog_cost": 0.0,
                "total_cost": 0.0,
                "node_count": 0,
                "nodes": [],
            },
        )
        summary["orders"] += float(stats.get("order", 0))
        summary["shipments"] += float(stats.get("shipped", 0))
        summary["arrivals"] += float(stats.get("arrivals", 0))
        summary["demand"] += float(stats.get("demand", 0))
        if node_type != "customer":
            summary["orders_satisfied"] += float(stats.get("shipped", 0))
        summary["inventory_after"] += float(stats.get("inventory_after", 0))
        summary["backlog_after"] += float(stats.get("backlog_after", 0))
        summary["holding_cost"] += float(stats.get("holding_cost", 0))
        summary["backlog_cost"] += float(stats.get("backlog_cost", 0))
        summary["total_cost"] += float(stats.get("total_cost", 0))
        summary["node_count"] += 1
        summary["nodes"].append(node)

    history_entry = {
        "round": round_number,
        "timestamp": timestamp.isoformat() + "Z",
        "demand": external_demand,
        "orders": {role: dict(orders_by_role.get(role, {})) for role in actor_nodes},
        "node_orders": node_orders_payload,
        "inventory_positions": {
            role: role_stats.get(role, {}).get("inventory_after", 0) for role in actor_nodes
        },
        "backlogs": {
            role: role_stats.get(role, {}).get("backlog_after", 0) for role in actor_nodes
        },
        "node_states": {
            node: {
                "inventory_before": stats.get("inventory_before", 0),
                "inventory_after": stats.get("inventory_after", 0),
                "backlog_before": stats.get("backlog_before", 0),
                "backlog_after": stats.get("backlog_after", 0),
                "incoming_order": stats.get("demand", 0),
                "arrivals": stats.get("arrivals", 0),
                "shipped": stats.get("shipped", 0),
                "last_order": stats.get("order", 0),
                "holding_cost": stats.get("holding_cost", 0.0),
                "backlog_cost": stats.get("backlog_cost", 0.0),
                "total_cost": stats.get("total_cost", 0.0),
                "type": stats.get("type"),
                "display_name": stats.get("display_name"),
                "shipments_detail": stats.get("shipments_detail", []),
                "order_impacts": stats.get("order_impacts", []),
            }
            for node, stats in node_stats.items()
        },
        "costs": {
            role: {
                "holding_cost": role_stats.get(role, {}).get("holding_cost", 0.0),
                "backlog_cost": role_stats.get(role, {}).get("backlog_cost", 0.0),
                "total_cost": role_stats.get(role, {}).get("total_cost", 0.0),
            }
            for role in actor_nodes
        },
        "node_type_summaries": {
            node_type: {
                **summary,
                "orders": round(summary["orders"], 2),
                "shipments": round(summary["shipments"], 2),
                "arrivals": round(summary["arrivals"], 2),
                "demand": round(summary["demand"], 2),
                "orders_satisfied": round(summary["orders_satisfied"], 2),
                "inventory_after": round(summary["inventory_after"], 2),
                "backlog_after": round(summary["backlog_after"], 2),
                "holding_cost": round(summary["holding_cost"], 2),
                "backlog_cost": round(summary["backlog_cost"], 2),
                "total_cost": round(summary["total_cost"], 2),
            }
            for node_type, summary in node_type_summaries.items()
        },
        "total_cost": round(
            sum(summary["total_cost"] for summary in node_type_summaries.values()), 2
        ),
    }
    if shipments_map:
        history_entry["shipments"] = {source: dict(targets) for source, targets in shipments_map.items()}

    _append_debug_round_log(
        config,
        scenario,
        round_number=round_number,
        timestamp=timestamp,
        entries=ordered_debug_entries,
    )

    existing_history = config.setdefault("history", [])
    existing_index = next(
        (idx for idx, item in enumerate(existing_history) if isinstance(item, dict) and item.get("round") == round_number),
        None,
    )
    if existing_index is not None:
        existing_history[existing_index] = history_entry
    else:
        existing_history.append(history_entry)

    if shipments_map:
        sankey_entry = {
            "round": round_number,
            "shipments": {source: dict(targets) for source, targets in shipments_map.items()},
            "demand": external_demand,
        }
        sankey_history = config.setdefault("sankey_history", [])
        sankey_index = next(
            (idx for idx, item in enumerate(sankey_history) if isinstance(item, dict) and item.get("round") == round_number),
            None,
        )
        if sankey_index is not None:
            sankey_history[sankey_index] = sankey_entry
        else:
            sankey_history.append(sankey_entry)

    config["pending_orders"] = {}

    round_record.status = "completed"
    round_record.completed_at = timestamp
    round_record.config = {
        "orders": {role: dict(orders_by_role.get(role, {})) for role in actor_nodes},
        "demand": external_demand,
        "node_states": history_entry.get("node_states", {}),
        "node_orders": history_entry.get("node_orders", {}),
        "node_type_summaries": history_entry.get("node_type_summaries", {}),
    }
    if shipments_map:
        round_record.config["shipments"] = {source: dict(targets) for source, targets in shipments_map.items()}

    if scenario.max_rounds and round_number >= scenario.max_rounds:
        scenario.status = DbScenarioStatus.FINISHED
        scenario.current_round = round_number
    else:
        scenario.current_round = round_number + 1
        next_round = _ensure_round(db, scenario, scenario.current_round)
        next_round.status = "in_progress"
        next_round.started_at = datetime.utcnow()
        scenario.status = (
            DbScenarioStatus.PERIOD_IN_PROGRESS
            if _get_progression_mode(scenario) == PROGRESSION_UNSUPERVISED
            else DbScenarioStatus.STARTED
        )

    _touch_game(scenario)
    _save_scenario_config(db, scenario, config)
    db.add(round_record)
    db.add(scenario)
    db.flush()
    return True


def _auto_advance_unsupervised_scenario_sync(
    scenario_id: int,
    *,
    sleep_seconds: float = 0.35,
    iteration_limit: int = 2048,
) -> None:
    session = SyncSessionLocal()
    try:
        service = MixedScenarioService(session)
        scenario = session.query(DbGame).filter(DbGame.id == scenario_id).first()
        if not scenario:
            logger.warning("Auto-advance aborted: scenario %s not found", scenario_id)
            try:
                _debug_note(scenario, "Auto-advance aborted: scenario not found")
            except Exception:
                pass
            return

        if _get_progression_mode(scenario) != PROGRESSION_UNSUPERVISED:
            logger.debug("Scenario %s is not unsupervised; skipping auto advance", scenario_id)
            _debug_note(
                scenario,
                "Auto-advance skipped: progression mode is not unsupervised",
                details={"progression_mode": _get_progression_mode(scenario)},
            )
            return

        scenario_users = session.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
        if not scenario_users:
            logger.warning("Auto-advance aborted: no scenario_users for scenario %s", scenario_id)
            _debug_note(scenario, "Auto-advance aborted: no scenario_users found")
            return

        has_human_players = any(not p.is_ai for p in scenario_users)
        if has_human_players:
            logger.info(
                "Auto-advance skipped for scenario %s: unsupervised mode requires AI-only scenario_users",
                scenario_id,
            )
            _debug_note(
                scenario,
                "Auto-advance skipped: human scenario_users present",
                details={
                    "ai_players": sum(1 for p in scenario_users if p.is_ai),
                    "human_players": sum(1 for p in scenario_users if not p.is_ai),
                },
            )
            return

        logger.info("Auto-advance starting for scenario %s (max_rounds=%s)", scenario_id, scenario.max_rounds)
        _debug_note(
            scenario,
            "Auto-advance starting",
            details={"max_rounds": scenario.max_rounds, "progression_mode": _get_progression_mode(scenario)},
        )
        iteration = 0

        while True:
            iteration += 1
            if iteration_limit and iteration > iteration_limit:
                logger.warning(
                    "Auto-advance stopped for scenario %s after reaching the iteration limit",
                    scenario_id,
                )
                _debug_note(
                    scenario,
                    "Auto-advance stopped: iteration limit reached",
                    details={"iteration_limit": iteration_limit},
                )
                break

            session.expire_all()
            scenario = session.query(DbGame).filter(DbGame.id == scenario_id).first()
            if not scenario:
                logger.debug("Scenario %s disappeared mid-run; stopping auto advance", scenario_id)
                break

            if _get_progression_mode(scenario) != PROGRESSION_UNSUPERVISED:
                logger.debug(
                    "Scenario %s progression mode changed away from unsupervised; stopping",
                    scenario_id,
                )
                break

            if scenario.status == DbScenarioStatus.FINISHED:
                break

            try:
                round_record = service.start_new_round(scenario)
            except Exception as exc:  # noqa: BLE001
                _debug_note(
                    scenario,
                    "Auto-advance stopped: start_new_round raised",
                    details={"iteration": iteration, "error": str(exc)},
                )
                logger.exception("start_new_round failed for scenario %s", scenario_id)
                break
            if round_record is None:
                _debug_note(
                    scenario,
                    "Auto-advance stopped: start_new_round returned None",
                    details={"iteration": iteration},
                )
                break

            session.refresh(scenario)

            if scenario.status == DbScenarioStatus.FINISHED:
                break

            if sleep_seconds:
                time.sleep(sleep_seconds)
        logger.info("Auto-advance finished for scenario %s after %s iterations", scenario_id, iteration)
        _debug_note(
            scenario,
            "Auto-advance finished",
            details={"iterations": iteration, "status": str(scenario.status)},
        )
    except Exception:
        logger.exception("Auto-advance failed for scenario %s", scenario_id)
    finally:
        session.close()


async def _schedule_unsupervised_autoplay(scenario_id: int) -> None:
    with _AUTO_TASKS_LOCK:
        existing = _AUTO_ADVANCE_TASKS.get(scenario_id)
        if existing and not existing.done():
            return

    async def runner() -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.to_thread(_auto_advance_unsupervised_scenario_sync, scenario_id)
        finally:
            with _AUTO_TASKS_LOCK:
                if _AUTO_ADVANCE_TASKS.get(scenario_id) is current_task:
                    _AUTO_ADVANCE_TASKS.pop(scenario_id, None)

    task = asyncio.create_task(runner())
    with _AUTO_TASKS_LOCK:
        _AUTO_ADVANCE_TASKS[scenario_id] = task
    logger.info("Scheduled unsupervised auto-advance for scenario %s", scenario_id)


def _cancel_unsupervised_autoplay(scenario_id: int) -> None:
    with _AUTO_TASKS_LOCK:
        task = _AUTO_ADVANCE_TASKS.pop(scenario_id, None)
    if task and not task.done():
        task.cancel()
        # Cancellation is cooperative; once cancelled, the background task will
        # exit on its own when the event loop next runs. No further action needed.


def _normalize_node_key(value: Any) -> str:
    if value is None:
        return ""
    token = str(value).strip().lower()
    token = re.sub(r"[^\w]+", "_", token)
    token = re.sub(r"_+", "_", token).strip("_")
    return token


def _replay_history_from_rounds(
    db: Session,
    scenario: DbGame,
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Rebuild round history from persisted round tables when config lacks it."""

    if config is None:
        config = _coerce_scenario_config(scenario)
    site_type_definitions, _ = _ensure_site_type_definitions(config or {})
    site_type_sequence = [
        MixedScenarioService._normalise_node_type(defn.get("type"))
        for defn in site_type_definitions
        if MixedScenarioService._normalise_node_type(defn.get("type"))
    ]

    records = (
        db.query(Period)
        .filter(Period.scenario_id == scenario.id)
        .order_by(Period.round_number.asc())
        .all()
    )

    history: List[Dict[str, Any]] = []
    last_recorded_round = 0
    if records:
        for record in records:
            payload: Dict[str, Any] = {}
            if record.config:
                if isinstance(record.config, dict):
                    payload = dict(record.config)
                else:
                    try:
                        payload = json.loads(record.config)
                    except json.JSONDecodeError:
                        payload = {}
            if not payload:
                continue

            entry = {
                "round": record.round_number,
                "timestamp": _iso(record.completed_at) if record.completed_at else None,
                "demand": payload.get("demand"),
                "orders": payload.get("orders", {}),
                "node_states": payload.get("node_states", {}),
                "node_orders": payload.get("node_orders", {}),
                "node_type_summaries": payload.get("node_type_summaries", {}),
            }
            shipments = _derive_shipments_from_entry(entry, site_type_sequence)
            if shipments:
                entry["shipments"] = shipments
            history.append(entry)
            last_recorded_round = max(last_recorded_round, record.round_number)

    supply_rounds = (
        db.query(SupplyScenarioPeriod)
        .filter(SupplyScenarioPeriod.scenario_id == scenario.id)
        .filter(SupplyScenarioPeriod.round_number > last_recorded_round)
        .order_by(SupplyScenarioPeriod.round_number.asc())
        .all()
    )
    if not supply_rounds:
        return history

    node_types: Dict[str, str] = {}
    for node in config.get("nodes") or []:
        key = _normalize_node_key(node.get("name") or node.get("id"))
        if key:
            node_types[key] = MixedScenarioService._normalise_node_type(node.get("type"))
    for key, node_type in (config.get("node_types") or {}).items():
        normalized_key = _normalize_node_key(key)
        if normalized_key:
            node_types[normalized_key] = MixedScenarioService._normalise_node_type(node_type)

    scenario_users = {
        scenario_user.id: scenario_user
        for scenario_user in db.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
    }

    rounds_by_id = {round_rec.id: round_rec for round_rec in supply_rounds}
    entry_map: Dict[int, Dict[str, Any]] = {}
    for round_rec in supply_rounds:
        entry_map[round_rec.id] = {
            "round": round_rec.round_number,
            "timestamp": _iso(round_rec.completed_at or round_rec.ended_at),
            "demand": round_rec.customer_demand,
            "orders": {},
            "node_states": {},
            "node_orders": {},
            "node_type_summaries": {},
        }

    scenario_user_periods = (
        db.query(SupplyScenarioUserPeriod)
        .filter(SupplyScenarioUserPeriod.scenario_period_id.in_(rounds_by_id.keys()))
        .order_by(SupplyScenarioUserPeriod.scenario_period_id.asc(), SupplyScenarioUserPeriod.id.asc())
        .all()
    )

    for pr in scenario_user_periods:
        entry = entry_map.get(pr.scenario_period_id)
        if not entry:
            continue
        scenario_user = scenario_users.get(pr.scenario_user_id)
        if not scenario_user:
            continue
        node_key = _normalize_node_key(
            getattr(scenario_user, "node_key", None)
            or getattr(scenario_user.role, "value", scenario_user.role)
        )
        if not node_key:
            continue
        node_state = entry["node_states"].setdefault(
            node_key,
            {
                "orders": 0.0,
                "shipped": 0.0,
                "arrivals": 0.0,
                "inventory_before": 0.0,
                "inventory_after": 0.0,
                "backlog_before": 0.0,
                "backlog_after": 0.0,
                "holding_cost": 0.0,
                "backlog_cost": 0.0,
                "total_cost": 0.0,
                "type": node_types.get(node_key),
            },
        )
        node_state["orders"] += float(pr.order_placed or 0)
        node_state["shipped"] += float(pr.order_placed or 0)
        node_state["arrivals"] += float(pr.order_received or 0)
        node_state["inventory_before"] = float(pr.inventory_before or 0)
        node_state["inventory_after"] = float(pr.inventory_after or 0)
        node_state["backlog_before"] = float(pr.backorders_before or 0)
        node_state["backlog_after"] = float(pr.backorders_after or 0)
        node_state["holding_cost"] += float(pr.holding_cost or 0)
        node_state["backlog_cost"] += float(pr.backorder_cost or 0)
        node_state["total_cost"] += float(pr.total_cost or 0)

        entry["orders"].setdefault(node_key, {"quantity": 0.0})
        entry["orders"][node_key]["quantity"] += float(pr.order_placed or 0)
        entry["node_orders"].setdefault(node_key, {"quantity": 0.0})
        entry["node_orders"][node_key]["quantity"] += float(pr.order_placed or 0)

    for entry in entry_map.values():
        type_summaries: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "orders": 0.0,
                "shipments": 0.0,
                "arrivals": 0.0,
                "demand": 0.0,
                "inventory_after": 0.0,
                "backlog_after": 0.0,
                "holding_cost": 0.0,
                "backlog_cost": 0.0,
                "total_cost": 0.0,
                "node_count": 0,
                "nodes": [],
            }
        )
        for node_key, node_state in entry["node_states"].items():
            node_type = node_state.get("type") or node_types.get(node_key) or node_key
            summary = type_summaries[node_type]
            summary["orders"] += node_state.get("orders", 0.0)
            summary["shipments"] += node_state.get("shipped", 0.0)
            summary["arrivals"] += node_state.get("arrivals", 0.0)
            summary["inventory_after"] += node_state.get("inventory_after", 0.0)
            summary["backlog_after"] += node_state.get("backlog_after", 0.0)
            summary["holding_cost"] += node_state.get("holding_cost", 0.0)
            summary["backlog_cost"] += node_state.get("backlog_cost", 0.0)
            summary["total_cost"] += node_state.get("total_cost", 0.0)
            summary["node_count"] += 1
            summary["nodes"].append(node_key)
        entry["node_type_summaries"] = {
            key: dict(value) for key, value in type_summaries.items()
        }
        shipments = _derive_shipments_from_entry(entry, site_type_sequence)
        if shipments:
            entry["shipments"] = shipments
        history.append(entry)

    history.sort(key=lambda item: item.get("round", 0))
    return history


def _compute_scenario_report(db: Session, scenario: DbGame) -> Dict[str, Any]:
    config = _coerce_scenario_config(scenario)
    site_type_definitions, site_type_labels = _ensure_site_type_definitions(config)
    site_type_sequence = [
        MixedScenarioService._normalise_node_type(defn.get("type"))
        for defn in site_type_definitions
        if MixedScenarioService._normalise_node_type(defn.get("type"))
    ]
    history = list(config.get("history", []))

    supply_chain_config_id = getattr(scenario, "supply_chain_config_id", None)
    supply_chain_name = config.get("supply_chain_name")

    try:
        supply_chain_obj = getattr(scenario, "supply_chain_config", None)
    except Exception:  # noqa: BLE001 - defensive: relationship access shouldn't fail
        supply_chain_obj = None

    if supply_chain_obj is not None:
        if supply_chain_config_id is None:
            supply_chain_config_id = getattr(supply_chain_obj, "id", supply_chain_config_id)
        if not supply_chain_name:
            supply_chain_name = getattr(supply_chain_obj, "name", supply_chain_name)

    if supply_chain_name is None and supply_chain_config_id is not None:
        sc_record = (
            db.query(SupplyChainConfig)
            .filter(SupplyChainConfig.id == supply_chain_config_id)
            .first()
        )
        if sc_record is not None:
            supply_chain_name = sc_record.name

    if not history:
        history = _replay_history_from_rounds(db, scenario, config)

    config_history = config.get("history")
    for idx, entry in enumerate(history):
        shipments = _derive_shipments_from_entry(entry, site_type_sequence)
        if shipments:
            entry["shipments"] = shipments
            if isinstance(config_history, list) and idx < len(config_history):
                config_item = config_history[idx]
                if isinstance(config_item, dict):
                    config_item["shipments"] = shipments

    node_policies = config.get("node_policies") or {}
    lane_views = MixedScenarioService._build_lane_views(node_policies, config)
    lane_nodes = lane_views.get("all_nodes") or []
    node_types_map: Dict[str, str] = {}
    extracted_types = MixedScenarioService._extract_node_types(config)
    for raw_key, node_type in extracted_types.items():
        canonical_key = MixedScenarioService._normalise_key(raw_key)
        if canonical_key:
            node_types_map[canonical_key] = MixedScenarioService._normalise_node_type(node_type)
    for raw_key, node_type in (lane_views.get("node_types") or {}).items():
        canonical_key = MixedScenarioService._normalise_key(raw_key)
        if canonical_key:
            node_types_map[canonical_key] = MixedScenarioService._normalise_node_type(node_type)

    node_display_names: Dict[str, str] = dict(config.get("node_display_names", {}) or {})
    for node in config.get("nodes") or []:
        label = node.get("name") or node.get("id")
        key = MixedScenarioService._normalise_key(label)
        display_label = (
            node.get("display_name")
            or node.get("label")
            or node.get("name")
            or node.get("id")
        )
        if key:
            node_display_names.setdefault(key, str(display_label))
            if node.get("type") and key not in node_types_map:
                node_types_map[key] = MixedScenarioService._normalise_node_type(node.get("type"))
    for raw_label in node_policies.keys():
        key = MixedScenarioService._normalise_key(raw_label)
        if key and key not in node_display_names:
            node_display_names[key] = str(raw_label).replace("_", " ").title()

    ordered_nodes: List[str] = []
    seen_nodes: Set[str] = set()
    combined_sequence = lane_nodes or list(node_types_map.keys())
    if not combined_sequence:
        combined_sequence = sorted(node_types_map.keys())
    for candidate in combined_sequence:
        key = MixedScenarioService._normalise_key(candidate)
        if not key or key in seen_nodes:
            continue
        ordered_nodes.append(key)
        seen_nodes.add(key)
    node_types_normalized = dict(node_types_map)
    node_sequence = ordered_nodes or list(node_types_normalized.keys())

    totals_by_type: Dict[str, Dict[str, Any]] = defaultdict(lambda: defaultdict(float))
    order_series_by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    inventory_series: List[Dict[str, Any]] = []
    demand_series: List[Dict[str, Any]] = []
    observed_types: Set[str] = set()

    def _coerce_type(node_key: str, node_state: Mapping[str, Any]) -> str:
        inferred = node_state.get("type") if isinstance(node_state, Mapping) else None
        if inferred:
            return str(inferred).lower()
        fallback = node_types_normalized.get(node_key)
        if fallback:
            return str(fallback).lower()
        if node_key == "market":
            return "customer"
        return str(node_key).lower()

    type_to_nodes: Dict[str, List[str]] = defaultdict(list)
    for node_key in node_sequence:
        node_type = node_types_normalized.get(node_key) or _coerce_type(node_key, {})
        if node_type:
            type_to_nodes[node_type].append(node_key)

    for entry in history:
        round_number = entry.get("round")
        demand_series.append({"round": round_number, "demand": entry.get("demand", 0)})

        node_states = entry.get("node_states") or {}
        node_orders_payload = entry.get("node_orders") or entry.get("orders", {}) or {}
        for node_key in node_sequence:
            state = node_states.setdefault(node_key, {})
            state.setdefault("type", node_types_normalized.get(node_key))
            state.setdefault("display_name", node_display_names.get(node_key, node_key.replace("_", " ").title()))
            for metric in (
                "inventory_before",
                "inventory_after",
                "backlog_before",
                "backlog_after",
                "incoming_order",
                "arrivals",
                "shipped",
                "holding_cost",
                "backlog_cost",
                "total_cost",
            ):
                state.setdefault(metric, 0.0)
        for node_key in node_sequence:
            node_orders_payload.setdefault(node_key, {"quantity": 0.0})
        entry["node_states"] = node_states
        entry["node_orders"] = node_orders_payload

        type_summaries = entry.get("node_type_summaries") or {}
        if not type_summaries:
            derived: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
                "orders": 0.0,
                "shipments": 0.0,
                "arrivals": 0.0,
                "demand": 0.0,
                "inventory_after": 0.0,
                "backlog_after": 0.0,
                "holding_cost": 0.0,
                "backlog_cost": 0.0,
                "total_cost": 0.0,
                "node_count": 0,
                "nodes": [],
            })
            for node_key, node_state in node_states.items():
                node_type = _coerce_type(node_key, node_state)
                if not node_type:
                    continue
                summary_entry = derived[node_type]
                qty_raw = 0.0
                order_entry = node_orders_payload.get(node_key, {})
                if isinstance(order_entry, Mapping):
                    qty_raw = float(order_entry.get("quantity", 0) or 0)
                else:
                    qty_raw = float(order_entry or 0)
                summary_entry["orders"] += qty_raw
                summary_entry["shipments"] += float(node_state.get("shipped", 0) or 0)
                summary_entry["arrivals"] += float(node_state.get("arrivals", 0) or 0)
                summary_entry["demand"] += float(node_state.get("incoming_order", 0) or 0)
                summary_entry["inventory_after"] += float(node_state.get("inventory_after", 0) or 0)
                summary_entry["backlog_after"] += float(node_state.get("backlog_after", 0) or 0)
                summary_entry["holding_cost"] += float(node_state.get("holding_cost", 0) or 0)
                summary_entry["backlog_cost"] += float(node_state.get("backlog_cost", 0) or 0)
                summary_entry["total_cost"] += float(node_state.get("total_cost", 0) or 0)
                summary_entry["node_count"] += 1
                summary_entry["nodes"].append(node_key)
            type_summaries = {
                node_type: {
                    **summary,
                    "orders": round(summary["orders"], 2),
                    "shipments": round(summary["shipments"], 2),
                    "arrivals": round(summary["arrivals"], 2),
                    "demand": round(summary["demand"], 2),
                    "inventory_after": round(summary["inventory_after"], 2),
                    "backlog_after": round(summary["backlog_after"], 2),
                    "holding_cost": round(summary["holding_cost"], 2),
                    "backlog_cost": round(summary["backlog_cost"], 2),
                    "total_cost": round(summary["total_cost"], 2),
                }
                for node_type, summary in derived.items()
            }
            entry["node_type_summaries"] = type_summaries

        for definition in site_type_definitions:
            slug = str(definition.get("type") or "").lower()
            if not slug:
                continue
            summary = type_summaries.setdefault(
                slug,
                {
                    "orders": 0.0,
                    "shipments": 0.0,
                    "arrivals": 0.0,
                    "demand": 0.0,
                    "inventory_after": 0.0,
                    "backlog_after": 0.0,
                    "holding_cost": 0.0,
                    "backlog_cost": 0.0,
                    "total_cost": 0.0,
                    "node_count": 0,
                    "nodes": [],
                },
            )
            summary["nodes"] = list(type_to_nodes.get(slug, summary.get("nodes", [])))
            summary["node_count"] = len(summary["nodes"])
            summary["label"] = site_type_labels.get(slug, slug.replace("_", " ").title())

        inventory_snapshot = {
            "round": round_number,
            "types": {},
        }

        for node_type, summary in type_summaries.items():
            observed_types.add(node_type)
            quantity = float(summary.get("orders", 0) or 0)
            order_series_by_type[node_type].append({"round": round_number, "quantity": quantity})

            totals_entry = totals_by_type[node_type]
            totals_entry["holding_cost"] += float(summary.get("holding_cost", 0) or 0)
            totals_entry["backlog_cost"] += float(summary.get("backlog_cost", 0) or 0)
            totals_entry["total_cost"] += float(summary.get("total_cost", 0) or 0)
            totals_entry["orders"] += float(summary.get("orders", 0) or 0)
            totals_entry["final_inventory"] = float(summary.get("inventory_after", 0) or 0)
            totals_entry["final_backlog"] = float(summary.get("backlog_after", 0) or 0)
            totals_entry.setdefault("nodes", set()).update(summary.get("nodes", []) or [])
            totals_entry["node_count"] = max(
                int(totals_entry.get("node_count", 0)),
                int(summary.get("node_count", 0) or len(summary.get("nodes", []) or [])),
            )

            inventory_snapshot["types"][node_type] = {
                "inventory": float(summary.get("inventory_after", 0) or 0),
                "backlog": float(summary.get("backlog_after", 0) or 0),
            }

        inventory_series.append(inventory_snapshot)

    total_cost = sum(values.get("total_cost", 0.0) for values in totals_by_type.values())

    formatted_totals = {}
    for node_type, values in totals_by_type.items():
        nodes_associated = values.pop("nodes", set()) if "nodes" in values else set()
        formatted_totals[node_type] = {
            "holding_cost": round(values.get("holding_cost", 0.0), 2),
            "backlog_cost": round(values.get("backlog_cost", 0.0), 2),
            "total_cost": round(values.get("total_cost", 0.0), 2),
            "orders": round(values.get("orders", 0.0), 2),
            "final_inventory": values.get("final_inventory", 0.0),
            "final_backlog": values.get("final_backlog", 0.0),
            "node_count": int(values.get("node_count", 0)),
            "nodes": sorted(nodes_associated),
        }

    if not history:
        for definition in site_type_definitions:
            node_type = str(definition.get("type") or "").lower()
            formatted_totals.setdefault(
                node_type,
                {
                    "holding_cost": 0.0,
                    "backlog_cost": 0.0,
                    "total_cost": 0.0,
                    "orders": 0.0,
                    "final_inventory": 0.0,
                    "final_backlog": 0.0,
                    "node_count": 0,
                    "nodes": [],
                },
            )

    node_catalog: List[Dict[str, Any]] = []
    seen_nodes: Set[str] = set()
    for node_key in node_sequence:
        if node_key in seen_nodes:
            continue
        seen_nodes.add(node_key)
        node_catalog.append(
            {
                "key": node_key,
                "type": node_types_normalized.get(node_key, str(node_key).lower()),
                "display_name": node_display_names.get(node_key, node_key.replace("_", " ").title()),
            }
        )

    for entry in history:
        for node_key, node_state in (entry.get("node_states") or {}).items():
            if node_key in seen_nodes:
                continue
            seen_nodes.add(node_key)
            node_catalog.append(
                {
                    "key": node_key,
                    "type": _coerce_type(node_key, node_state),
                    "display_name": node_display_names.get(
                        node_key, node_state.get("display_name") or node_key.replace("_", " ").title()
                    ),
                }
            )
            node_type = _coerce_type(node_key, node_state)
            if node_type:
                type_to_nodes[node_type].append(node_key)


    order_series_by_type = {
        node_type: series for node_type, series in order_series_by_type.items()
    }

    try:
        _write_sankey_log(scenario, history)
    except Exception:  # noqa: BLE001 - log and continue without failing report generation
        logger.exception(
            "Failed to write Sankey log for scenario %s", getattr(scenario, "id", "?")
        )

    raw_product_catalog = config.get("product_catalog") or config.get("item_catalog")
    if isinstance(raw_product_catalog, dict):
        product_catalog_serialized = [
            dict(entry) for entry in raw_product_catalog.values() if isinstance(entry, dict)
        ]
    elif isinstance(raw_product_catalog, list):
        product_catalog_serialized = list(raw_product_catalog)
    else:
        product_catalog_serialized = []

    products = config.get("products") or config.get("items") or []
    products_payload = products if any(isinstance(entry, dict) for entry in products) else []
    if not products_payload:
        products_payload = list(product_catalog_serialized)

    return {
        "scenario_id": scenario.id,
        "name": scenario.name,
        "supply_chain_config_id": supply_chain_config_id,
        "supply_chain_name": supply_chain_name,
        "progression_mode": _get_progression_mode(scenario),
        "total_cost": round(total_cost, 2),
        "totals": formatted_totals,
        "history": history,
        "order_series": order_series_by_type,
        "order_series_by_type": order_series_by_type,
        "inventory_series": inventory_series,
        "demand_series": demand_series,
        "rounds_completed": len(history),
        "site_type_definitions": site_type_definitions,
        "site_type_labels": site_type_labels,
        "site_catalog": node_catalog,
        "site_display_names": node_display_names,
        "site_types_normalized": node_types_normalized,
        "observed_site_types": sorted(observed_types),
        "products": products_payload,
        "product_catalog": product_catalog_serialized,
    }


def _serialize_game(scenario: DbGame) -> Dict[str, Any]:
    if isinstance(scenario.status, DbScenarioStatus):
        status = STATUS_REMAPPING.get(scenario.status, scenario.status.value)
    else:
        status = str(scenario.status or "")

    config = dict(_coerce_scenario_config(scenario))
    _ensure_site_type_definitions(config)
    demand_pattern = scenario.demand_pattern or config.get("demand_pattern") or {}

    supply_chain_config_id = getattr(scenario, "supply_chain_config_id", None)
    supply_chain_name = config.get("supply_chain_name")

    try:
        supply_chain_obj = getattr(scenario, "supply_chain_config", None)
    except Exception:  # noqa: BLE001 - defensive: relationship access shouldn't fail
        supply_chain_obj = None

    if supply_chain_obj is not None:
        if supply_chain_config_id is None:
            supply_chain_config_id = getattr(supply_chain_obj, "id", supply_chain_config_id)
        if not supply_chain_name:
            supply_chain_name = getattr(supply_chain_obj, "name", supply_chain_name)

    if supply_chain_config_id is not None and config.get("supply_chain_config_id") != supply_chain_config_id:
        config.setdefault("supply_chain_config_id", supply_chain_config_id)
    if supply_chain_name and config.get("supply_chain_name") != supply_chain_name:
        config.setdefault("supply_chain_name", supply_chain_name)

    payload = {
        "id": scenario.id,
        "name": scenario.name,
        "description": scenario.description,
        "status": str(status).upper(),
        "current_round": scenario.current_round or 0,
        "max_rounds": scenario.max_rounds or 0,
        "created_at": _iso(scenario.created_at),
        "updated_at": _iso(getattr(scenario, "updated_at", None)),
        "tenant_id": scenario.tenant_id,
        "created_by": scenario.created_by,
        "is_public": bool(getattr(scenario, "is_public", True)),
        "config": config,
        "demand_pattern": demand_pattern,
        "progression_mode": _get_progression_mode(scenario),
    }

    if supply_chain_config_id is not None:
        payload["supply_chain_config_id"] = supply_chain_config_id
    if supply_chain_name:
        payload["supply_chain_name"] = supply_chain_name

    return payload


def _get_scenario_for_user(db: Session, user: Any, scenario_id: int) -> DbGame:
    scenario = db.query(DbGame).filter(DbGame.id == scenario_id).first()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if _is_system_admin_user(user):
        return scenario
    user_tenant_id = _extract_tenant_id(user)
    if user_tenant_id is None or user_tenant_id != scenario.tenant_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return scenario


def _touch_game(scenario: DbGame) -> None:
    scenario.updated_at = datetime.utcnow()


@api.post("/mixed-scenarios/", status_code=201)
async def create_mixed_scenario(payload: ScenarioCreate, user: Dict[str, Any] = Depends(get_current_user)):
    db = SyncSessionLocal()
    try:
        tenant_id = _extract_tenant_id(user)
        if tenant_id is None and not _is_system_admin_user(user):
            raise HTTPException(status_code=403, detail="Tenant membership required to create scenarios")
        if payload.supply_chain_config_id is None:
            raise HTTPException(status_code=400, detail="supply_chain_config_id is required to create a mixed scenario")

        config: Dict[str, Any] = {
            "demand_pattern": payload.demand_pattern.dict() if payload.demand_pattern else {},
            "pricing_config": payload.pricing_config.dict() if payload.pricing_config else {},
            "node_policies": {key: policy.dict() for key, policy in (payload.node_policies or {}).items()},
            "system_config": payload.system_config or {},
            "global_policy": payload.global_policy or {},
            "progression_mode": payload.progression_mode,
            "pending_orders": {},
            "history": [],
        }

        scenario = DbGame(
            name=payload.name,
            description=payload.description,
            status=DbScenarioStatus.CREATED,
            current_round=0,
            max_rounds=payload.max_rounds or 0,
            created_at=datetime.utcnow(),
            tenant_id=tenant_id,
            created_by=user.get("id"),
            is_public=payload.is_public,
            demand_pattern=config.get("demand_pattern", {}),
            config=config,
            supply_chain_config_id=int(payload.supply_chain_config_id),
        )
        db.add(scenario)
        db.flush()

        role_enum_map = {
            "retailer": "RETAILER",
            "wholesaler": "WHOLESALER",
            "distributor": "DISTRIBUTOR",
            "manufacturer": "MANUFACTURER",
            "factory": "MANUFACTURER",
        }

        for assignment in payload.player_assignments:
            is_agent = assignment.scenario_user_type == ScenarioUserTypeSchema.AGENT
            role_value_raw = assignment.role.value if hasattr(assignment.role, "value") else str(assignment.role)
            role_key = role_value_raw.lower()
            role_enum_name = role_enum_map.get(role_key)
            if not role_enum_name:
                raise HTTPException(status_code=400, detail=f"Unsupported role: {role_value_raw}")
            if not is_agent and assignment.user_id is None:
                raise HTTPException(status_code=400, detail=f"User ID required for human role {role_value_raw}")

            strategy_value = None
            if assignment.strategy is not None:
                strategy_value = assignment.strategy.value if hasattr(assignment.strategy, "value") else str(assignment.strategy)

            scenario_user = ScenarioUser(
                scenario_id=scenario.id,
                user_id=None if is_agent else assignment.user_id,
                name=f"{role_enum_name.title().replace('_', ' ')} ({'AI' if is_agent else 'Human'})",
                role=ScenarioUserRole[role_enum_name],
                is_ai=is_agent,
                ai_strategy=strategy_value,
                can_see_demand=assignment.can_see_demand,
                llm_model=assignment.llm_model if is_agent else None,
                strategy=ScenarioUserStrategy.MANUAL,
            )
            db.add(scenario_user)

        _ensure_simulation_state(config)
        _save_scenario_config(db, scenario, config)
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        payload = _serialize_game(scenario)
        if _get_progression_mode(scenario) == PROGRESSION_UNSUPERVISED:
            await _schedule_unsupervised_autoplay(scenario.id)
        return payload
    finally:
        db.close()


def _apply_player_assignments_for_update(
    db: Session,
    scenario: DbGame,
    assignments: List[ScenarioUserAssignment],
    config: Dict[str, Any],
) -> None:
    existing_scenario_users = {
        (scenario_user.role.name if hasattr(scenario_user.role, "name") else str(scenario_user.role).upper()): scenario_user
        for scenario_user in db.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
    }

    role_assignments: Dict[str, Dict[str, Any]] = {}
    overrides: Dict[str, float] = {}

    for assignment in assignments:
        role_value = assignment.role.value if hasattr(assignment.role, "value") else str(assignment.role)
        role_key = role_value.lower()
        role_enum_name = role_value.upper()
        if role_enum_name not in ScenarioUserRole.__members__:
            raise HTTPException(status_code=400, detail=f"Unsupported role: {role_value}")

        role_enum = ScenarioUserRole[role_enum_name]
        scenario_user = existing_scenario_users.pop(role_enum.name, None)
        if scenario_user is None:
            scenario_user = ScenarioUser(
                scenario_id=scenario.id,
                role=role_enum,
                name=role_enum.name.title(),
            )

        is_agent = assignment.scenario_user_type == ScenarioUserTypeSchema.AGENT
        strategy_value = None
        if assignment.strategy is not None:
            strategy_value = (
                assignment.strategy.value
                if hasattr(assignment.strategy, "value")
                else str(assignment.strategy)
            )

        scenario_user.is_ai = is_agent
        scenario_user.type = ScenarioUserModelType.AI if is_agent else ScenarioUserModelType.HUMAN
        scenario_user.ai_strategy = strategy_value if is_agent else None
        scenario_user.can_see_demand = assignment.can_see_demand
        scenario_user.user_id = None if is_agent else assignment.user_id
        scenario_user.llm_model = assignment.llm_model if is_agent and assignment.llm_model else None
        scenario_user.name = f"{role_enum.name.title()} ({'AI' if is_agent else 'Human'})"
        scenario_user.strategy = ScenarioUserStrategy.MANUAL
        db.add(scenario_user)

        role_assignments[role_key] = {
            "is_ai": is_agent,
            "agent_config_id": None,
            "user_id": None if is_agent else assignment.user_id,
        }

        if assignment.autonomy_override_pct is not None:
            overrides[role_key] = float(assignment.autonomy_override_pct)

    for leftover in existing_scenario_users.values():
        db.delete(leftover)

    if overrides:
        config["autonomy_overrides"] = overrides
    else:
        config.pop("autonomy_overrides", None)

    scenario.role_assignments = role_assignments

@api.get("/mixed-scenarios/")
async def list_mixed_scenarios(user: Dict[str, Any] = Depends(get_current_user)):
    db = SyncSessionLocal()
    try:
        query = db.query(DbGame).order_by(DbGame.created_at.desc())
        if not _is_system_admin_user(user):
            tenant_id = _extract_tenant_id(user)
            if tenant_id is None:
                return []
            query = query.filter(DbGame.tenant_id == tenant_id)
        scenarios = query.all()
        if not scenarios:
            ensure_default_tenant_and_scenario(db)
            db.expire_all()
            scenarios = query.all()

        payload: List[Dict[str, Any]] = []
        for scenario in scenarios:
            if _get_progression_mode(scenario) == PROGRESSION_UNSUPERVISED and scenario.status in {
                DbScenarioStatus.STARTED,
                DbScenarioStatus.PERIOD_IN_PROGRESS,
            }:
                await _schedule_unsupervised_autoplay(scenario.id)
            payload.append(_serialize_game(scenario))
        return payload
    finally:
        db.close()


@api.put("/mixed-scenarios/{scenario_id}")
async def update_mixed_scenario(
    scenario_id: int,
    payload: GameUpdatePayload,
    user: Dict[str, Any] = Depends(get_current_user),
):
    db = SyncSessionLocal()
    try:
        scenario = _get_scenario_for_user(db, user, scenario_id)
        if scenario.status not in {DbScenarioStatus.CREATED}:
            raise HTTPException(
                status_code=400,
                detail="Games can only be edited while in the CREATED state.",
            )

        config = _coerce_scenario_config(scenario)
        update_data = payload.dict(exclude_unset=True)

        if "name" in update_data:
            scenario.name = update_data["name"]
        if "description" in update_data:
            scenario.description = update_data["description"]
        if "is_public" in update_data:
            scenario.is_public = bool(update_data["is_public"])
        if "max_rounds" in update_data:
            scenario.max_rounds = update_data["max_rounds"]
        if "progression_mode" in update_data:
            config["progression_mode"] = update_data["progression_mode"]
        if payload.demand_pattern is not None:
            config["demand_pattern"] = payload.demand_pattern.dict()
            scenario.demand_pattern = config["demand_pattern"]
        if payload.pricing_config is not None:
            config["pricing_config"] = payload.pricing_config.dict()
        if payload.node_policies is not None:
            config["node_policies"] = {
                key: policy.dict() for key, policy in payload.node_policies.items()
            }
        if payload.system_config is not None:
            config["system_config"] = payload.system_config
        if payload.global_policy is not None:
            config["global_policy"] = payload.global_policy
        if payload.player_assignments is not None:
            _apply_player_assignments_for_update(db, scenario, payload.player_assignments, config)

        _ensure_simulation_state(config)
        _save_scenario_config(db, scenario, config)
        _touch_game(scenario)
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        return _serialize_game(scenario)
    finally:
        db.close()


@api.post("/scenarios/{scenario_id}/scenario_users/{scenario_user_id}/orders")
async def submit_order(
    scenario_id: int,
    scenario_user_id: int,
    submission: OrderSubmission,
    user: Dict[str, Any] = Depends(get_current_user),
):
    db = SyncSessionLocal()
    try:
        if submission.quantity < 0:
            raise HTTPException(status_code=400, detail="Quantity must be non-negative")

        scenario = _get_scenario_for_user(db, user, scenario_id)
        scenario_user = db.query(ScenarioUser).filter(ScenarioUser.id == scenario_user_id, ScenarioUser.scenario_id == scenario.id).first()
        if not scenario_user:
            raise HTTPException(status_code=404, detail="ScenarioUser not found for this scenario")

        config = _coerce_scenario_config(scenario)
        round_record = _ensure_round(db, scenario)

        # Record scenario_user action for auditing
        action = (
            db.query(ScenarioUserAction)
            .filter(
                ScenarioUserAction.scenario_id == scenario.id,
                ScenarioUserAction.scenario_user_id == scenario_user.id,
                ScenarioUserAction.period_id == round_record.id,
                ScenarioUserAction.action_type == "order",
            )
            .first()
        )

        timestamp = datetime.utcnow()

        if action:
            action.quantity = submission.quantity
            action.created_at = timestamp
        else:
            action = ScenarioUserAction(
                scenario_id=scenario.id,
                period_id=round_record.id,
                scenario_user_id=scenario_user.id,
                action_type="order",
                quantity=submission.quantity,
                created_at=timestamp,
            )
            db.add(action)

        role_key = str(scenario_user.role.value if hasattr(scenario_user.role, "value") else scenario_user.role).lower()
        pending_snapshot = _pending_orders(config)
        pending_snapshot[role_key] = {
            "scenario_user_id": scenario_user.id,
            "quantity": submission.quantity,
            "comment": submission.comment,
            "submitted_at": timestamp.isoformat() + "Z",
        }

        _save_scenario_config(db, scenario, config)
        db.add(scenario)
        db.flush()

        auto_advanced = _finalize_round_if_ready(db, scenario, config, round_record, force=False)
        db.commit()

        progression_mode = _get_progression_mode(scenario)
        if progression_mode == PROGRESSION_UNSUPERVISED:
            await _schedule_unsupervised_autoplay(scenario.id)

        return {
            "status": "recorded",
            "auto_advanced": auto_advanced,
            "pending_orders": {
                str(p.role.value if hasattr(p.role, "value") else p.role).lower(): {
                    "scenario_user_id": p.id,
                    "quantity": act.quantity,
                }
                for act, p in db.query(ScenarioUserAction, ScenarioUser)
                .join(ScenarioUser, ScenarioUser.id == ScenarioUserAction.scenario_user_id)
                .filter(
                    ScenarioUserAction.scenario_id == scenario.id,
                    ScenarioUserAction.period_id == round_record.id,
                    ScenarioUserAction.action_type == "order",
                )
            },
            "progression_mode": progression_mode,
        }
    finally:
        db.close()


@api.get("/mixed-scenarios/{scenario_id}/rounds")
async def list_rounds(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    db = SyncSessionLocal()
    try:
        scenario = _get_scenario_for_user(db, user, scenario_id)
        config = _coerce_scenario_config(scenario)
        history = config.get("history", [])
        if not history:
            history = _replay_history_from_rounds(db, scenario, config)
        scenario_users = db.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
        players_by_role = {
            str(p.role.value if hasattr(p.role, "value") else p.role).lower(): p
            for p in scenario_users
        }

        rounds_payload = []
        for entry in history:
            round_number = entry.get("round")
            scenario_user_periods = []
            for role, scenario_user in players_by_role.items():
                order_info = entry.get("orders", {}).get(role, {})
                scenario_user_periods.append(
                    {
                        "scenario_user_id": scenario_user.id,
                        "role": role.upper(),
                        "order_placed": order_info.get("quantity", 0),
                        "inventory_after": entry.get("inventory_positions", {}).get(role, 0),
                        "backorders_after": entry.get("backlogs", {}).get(role, 0),
                        "comment": order_info.get("comment"),
                    }
                )

            rounds_payload.append(
                {
                    "round_number": round_number,
                    "demand": entry.get("demand", 0),
                    "scenario_user_periods": scenario_user_periods,
                }
            )
        return rounds_payload
    finally:
        db.close()


@api.get("/scenarios/{scenario_id}/rounds")
async def list_rounds_alias(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    return await list_rounds(scenario_id, user)


@api.get("/mixed-scenarios/{scenario_id}/rounds/current/status")
async def current_round_status(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    db = SyncSessionLocal()
    try:
        scenario = _get_scenario_for_user(db, user, scenario_id)
        config = _coerce_scenario_config(scenario)
        pending = _pending_orders(config)
        scenario_users = db.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
        all_roles = {
            str(p.role.value if hasattr(p.role, "value") else p.role).lower(): p.id
            for p in scenario_users
        }
        submitted = {role for role, data in pending.items() if data.get("quantity") is not None}
        outstanding = [role for role in all_roles.keys() if role not in submitted]
        return {
            "scenario_id": scenario.id,
            "current_round": scenario.current_round or 1,
            "progression_mode": _get_progression_mode(scenario),
            "submitted_roles": list(submitted),
            "outstanding_roles": outstanding,
        }
    finally:
        db.close()


@api.get("/scenarios/{scenario_id}/rounds/current/status")
async def current_round_status_alias(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    return await current_round_status(scenario_id, user)


@api.get("/mixed-scenarios/{scenario_id}/report")
async def get_scenario_report(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    db = SyncSessionLocal()
    try:
        scenario = _get_scenario_for_user(db, user, scenario_id)
        return _compute_scenario_report(db, scenario)
    finally:
        db.close()


@api.get("/mixed-scenarios/{scenario_id}/state")
async def get_scenario_state(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    db = SyncSessionLocal()
    try:
        scenario = _get_scenario_for_user(db, user, scenario_id)
        config = _coerce_scenario_config(scenario)
        scenario_users = db.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
        players_payload = [
            {
                "id": scenario_user.id,
                "role": scenario_user.role.value if hasattr(scenario_user.role, "value") else scenario_user.role,
                "user_id": scenario_user.user_id,
                "is_ai": bool(scenario_user.is_ai),
                "ai_strategy": scenario_user.ai_strategy,
                "can_see_demand": bool(scenario_user.can_see_demand),
            }
            for scenario_user in scenario_users
        ]
        round_record = _ensure_round(db, scenario, scenario.current_round or 1)
        pending = _pending_orders(config)
        history = config.get("history", [])
        if not history:
            history = _replay_history_from_rounds(db, scenario, config)
        return {
            "scenario": _serialize_game(scenario),
            "progression_mode": _get_progression_mode(scenario),
            "round": round_record.round_number,
            "pending_orders": pending,
            "history": history,
            "scenario_users": players_payload,
        }
    finally:
        db.close()


@api.get("/scenarios/{scenario_id}")
async def get_game(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    db = SyncSessionLocal()
    try:
        scenario = _get_scenario_for_user(db, user, scenario_id)
        payload = _serialize_game(scenario)
        scenario_users = db.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
        payload["scenario_users"] = [
            {
                "id": scenario_user.id,
                "name": scenario_user.name,
                "role": scenario_user.role.value if hasattr(scenario_user.role, "value") else scenario_user.role,
                "user_id": scenario_user.user_id,
                "is_ai": bool(scenario_user.is_ai),
                "ai_strategy": scenario_user.ai_strategy,
                "can_see_demand": bool(scenario_user.can_see_demand),
            }
            for scenario_user in scenario_users
        ]
        return payload
    finally:
        db.close()


@api.get("/scenarios/{scenario_id}/scenario_users")
async def list_players(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    db = SyncSessionLocal()
    try:
        scenario = _get_scenario_for_user(db, user, scenario_id)
        scenario_users = db.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
        return [
            {
                "id": scenario_user.id,
                "name": scenario_user.name,
                "role": scenario_user.role.value if hasattr(scenario_user.role, "value") else scenario_user.role,
                "user_id": scenario_user.user_id,
                "is_ai": bool(scenario_user.is_ai),
                "ai_strategy": scenario_user.ai_strategy,
                "can_see_demand": bool(scenario_user.can_see_demand),
            }
            for scenario_user in scenario_users
        ]
    finally:
        db.close()


@api.post("/mixed-scenarios/{scenario_id}/start")
async def start_game(
    scenario_id: int,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
):
    db = SyncSessionLocal()
    try:
        scenario = _get_scenario_for_user(db, user, scenario_id)
        if scenario.status == DbScenarioStatus.FINISHED:
            raise HTTPException(status_code=400, detail="Scenario is already finished")
        scenario.started_at = datetime.utcnow()
        request_payload: Dict[str, Any] = {}
        try:
            if request.headers.get("content-length") not in (None, "0"):
                request_payload = await request.json()
        except Exception:  # noqa: BLE001
            request_payload = {}
        if not isinstance(request_payload, dict):
            request_payload = {}

        config = _coerce_scenario_config(scenario)
        legacy_normalized = _normalize_legacy_engine_queues(config)
        if legacy_normalized:
            _save_scenario_config(db, scenario, config)
            db.add(scenario)
            db.commit()
            db.refresh(scenario)

        scenario_users = db.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
        human_count = sum(1 for p in scenario_users if not getattr(p, "is_ai", False))
        ai_count = len(scenario_users) - human_count
        all_ai_players = bool(scenario_users) and human_count == 0
        # If the scenario is AI-only and progression mode isn't set, default to unsupervised so it can autoplay.
        if _get_progression_mode(scenario) != PROGRESSION_UNSUPERVISED and all_ai_players:
            config["progression_mode"] = PROGRESSION_UNSUPERVISED

        raw_debug = (
            request_payload.get("debug_logging")
            or request_payload.get("debug")
            or request_payload.get("debug_mode")
        )
        if raw_debug is None:
            qp_value = (
                request.query_params.get("debug_logging")
                or request.query_params.get("debug")
                or request.query_params.get("debug_mode")
            )
            if qp_value is not None:
                raw_debug = qp_value

        # Start with any persisted config then overlay request preferences so a
        # dictionary payload (e.g., {"enabled": true, "file_path": "..."}) is
        # honoured.
        debug_cfg = _normalize_debug_config(config)
        if raw_debug is not None:
            override_cfg = _normalize_debug_config({"debug_logging": raw_debug})
            debug_cfg.update({k: v for k, v in override_cfg.items() if v is not None})
        config["debug_logging"] = debug_cfg
        debug_requested = bool(debug_cfg.get("enabled"))
        if debug_requested:
            _ensure_debug_log_file(config, scenario)
            _debug_note(
                scenario,
                "Start requested",
                details={
                    "progression_mode": config.get("progression_mode"),
                    "ai_players": ai_count,
                    "human_players": human_count,
                },
                config=config,
            )

        try:
            pending = _pending_orders(config)
            pending.clear()
            config.setdefault("history", [])
            _ensure_simulation_state(config)
            _log_initialisation_debug(config, scenario, db)

            if scenario.current_round is None or scenario.current_round <= 0:
                scenario.current_round = 1
            round_record = _ensure_round(db, scenario, scenario.current_round)
            round_record.status = "in_progress"
            round_record.started_at = datetime.utcnow()

            scenario.status = (
                DbScenarioStatus.PERIOD_IN_PROGRESS
                if _get_progression_mode(scenario) == PROGRESSION_UNSUPERVISED
                else DbScenarioStatus.STARTED
            )
            _touch_game(scenario)
            _save_scenario_config(db, scenario, config)
            db.add(round_record)
            db.add(scenario)
            db.commit()
            db.refresh(scenario)
            unsupervised = _get_progression_mode(scenario) == PROGRESSION_UNSUPERVISED
            if unsupervised and all_ai_players:
                try:
                    # Run synchronously so the first request produces rounds/logs immediately.
                    _auto_advance_unsupervised_scenario_sync(scenario.id, sleep_seconds=0.0)
                except Exception as autoplay_exc:  # noqa: BLE001
                    logger.exception("Auto-advance failed for scenario %s", scenario.id)
                    if debug_requested:
                        _append_debug_error(
                            config,
                            scenario,
                            "Auto-advance failed",
                            details={"request_payload": request_payload},
                            exc=autoplay_exc,
                        )
                    startup_notices = config.setdefault("startup_notices", [])
                    if isinstance(startup_notices, list):
                        startup_notices.append(f"Auto-advance failed: {autoplay_exc}")
                        _save_scenario_config(db, scenario, config)
                        db.add(scenario)
                        db.commit()
                        db.refresh(scenario)
                else:
                    _debug_note(
                        scenario,
                        "Auto-advance completed synchronously",
                        details={"status": str(scenario.status)},
                    )
            elif unsupervised:
                await _schedule_unsupervised_autoplay(scenario.id)
            db.refresh(scenario)
            return _serialize_game(scenario)
        except Exception as exc:  # noqa: BLE001
            if debug_requested:
                _append_debug_error(
                    config,
                    scenario,
                    "Failed to start scenario",
                    details={"request_payload": request_payload},
                    exc=exc,
                )
            raise
    finally:
        db.close()


@api.post("/mixed-scenarios/{scenario_id}/stop")
async def stop_game(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    db = SyncSessionLocal()
    try:
        _cancel_unsupervised_autoplay(scenario_id)
        scenario = _get_scenario_for_user(db, user, scenario_id)
        config = _coerce_scenario_config(scenario)
        round_record = _ensure_round(db, scenario, scenario.current_round or 1)
        pending = _pending_orders(config)

        if pending and not _finalize_round_if_ready(db, scenario, config, round_record, force=True):
            _save_scenario_config(db, scenario, config)

        scenario.status = DbScenarioStatus.FINISHED
        if scenario.max_rounds:
            scenario.current_round = max(scenario.current_round or 0, scenario.max_rounds)

        _touch_game(scenario)
        _save_scenario_config(db, scenario, config)
        db.add(round_record)
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        return _serialize_game(scenario)
    finally:
        db.close()


@api.post("/mixed-scenarios/{scenario_id}/reset")
async def reset_game(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    _cancel_unsupervised_autoplay(scenario_id)
    db = SyncSessionLocal()
    try:
        scenario = _get_scenario_for_user(db, user, scenario_id)

        # Remove historical round data
        round_ids = [rid for (rid,) in db.query(Period.id).filter(Period.scenario_id == scenario.id).all()]
        if round_ids:
            db.query(ScenarioUserAction).filter(ScenarioUserAction.scenario_id == scenario.id).delete(synchronize_session=False)
        db.query(Period).filter(Period.scenario_id == scenario.id).delete(synchronize_session=False)

        sc_round_ids = [rid for (rid,) in db.query(SupplyScenarioPeriod.id).filter(SupplyScenarioPeriod.scenario_id == scenario.id).all()]
        if sc_round_ids:
            db.query(SupplyScenarioUserPeriod).filter(SupplyScenarioUserPeriod.scenario_period_id.in_(sc_round_ids)).delete(synchronize_session=False)
        db.query(SupplyScenarioPeriod).filter(SupplyScenarioPeriod.scenario_id == scenario.id).delete(synchronize_session=False)
        db.query(SupplyOrder).filter(SupplyOrder.scenario_id == scenario.id).delete(synchronize_session=False)

        config = _coerce_scenario_config(scenario)
        config["pending_orders"] = {}
        config["history"] = []
        config.pop("simulation_state", None)
        config.pop("engine_state", None)

        node_policies = config.get("node_policies", {})
        sim_params = _simulation_parameters(config)

        scenario_users = db.query(ScenarioUser).filter(ScenarioUser.scenario_id == scenario.id).all()
        for scenario_user in scenario_users:
            role_key = _role_key(scenario_user)
            policy_cfg = MixedScenarioService._policy_for_node(node_policies, role_key)
            init_inventory = int(policy_cfg.get("init_inventory", sim_params.get("initial_inventory", 12)))
            supply_lead = int(policy_cfg.get("supply_leadtime", sim_params.get("shipping_lead_time", 2)))
            incoming = [0] * max(1, supply_lead)

            inventory = (
                db.query(ScenarioUserInventory)
                .filter(ScenarioUserInventory.scenario_user_id == scenario_user.id)
                .first()
            )
            if inventory is None:
                inventory = ScenarioUserInventory(
                    scenario_user_id=scenario_user.id,
                    current_stock=init_inventory,
                    incoming_shipments=incoming,
                    backorders=0,
                    cost=0.0,
                )
                db.add(inventory)
            else:
                inventory.current_stock = init_inventory
                inventory.incoming_shipments = incoming
                inventory.backorders = 0
                inventory.cost = 0.0

            scenario_user.last_order = None
            scenario_user.is_ready = False

        _ensure_simulation_state(config)
        _save_scenario_config(db, scenario, config)

        scenario.current_round = 0
        scenario.status = DbScenarioStatus.CREATED
        scenario.started_at = None
        scenario.finished_at = None
        scenario.completed_at = None
        _touch_game(scenario)
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        return _serialize_game(scenario)
    finally:
        db.close()


@api.post("/mixed-scenarios/{scenario_id}/next-round")
async def next_round(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    db = SyncSessionLocal()
    try:
        scenario = _get_scenario_for_user(db, user, scenario_id)
        if _get_progression_mode(scenario) == PROGRESSION_UNSUPERVISED:
            raise HTTPException(status_code=400, detail="Unsupervised scenarios advance automatically")

        config = _coerce_scenario_config(scenario)
        round_record = _ensure_round(db, scenario, scenario.current_round or 1)
        if not _all_players_submitted(db, scenario, round_record):
            raise HTTPException(status_code=400, detail="All scenario_users must submit orders before advancing")

        if not _finalize_round_if_ready(db, scenario, config, round_record, force=True):
            raise HTTPException(status_code=400, detail="Unable to advance round")

        db.commit()
        db.refresh(scenario)
        return _serialize_game(scenario)
    finally:
        db.close()


@api.post("/mixed-scenarios/{scenario_id}/finish")
async def finish_game(scenario_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    db = SyncSessionLocal()
    try:
        scenario = _get_scenario_for_user(db, user, scenario_id)
        config = _coerce_scenario_config(scenario)
        round_record = _ensure_round(db, scenario, scenario.current_round or 1)
        pending = _pending_orders(config)
        if pending and not _finalize_round_if_ready(db, scenario, config, round_record, force=True):
            _save_scenario_config(db, scenario, config)

        scenario.status = DbScenarioStatus.FINISHED
        if scenario.max_rounds:
            scenario.current_round = max(scenario.current_round or 0, scenario.max_rounds)
        _touch_game(scenario)
        _save_scenario_config(db, scenario, config)
        db.add(round_record)
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        return _serialize_game(scenario)
    finally:
        db.close()


# Simple model status for UI banner
# ------------------------------------------------------------------------------
# Tenant management
# ------------------------------------------------------------------------------

@api.get("/tenants", response_model=List[TenantSchema], tags=["tenants"])
def list_tenants_endpoint(
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_sync_session),
):
    require_system_admin(current_user)
    service = TenantService(db)
    return service.get_tenants()


@api.get("/tenants/{tenant_id}", response_model=TenantSchema, tags=["tenants"])
def get_tenant_endpoint(
    tenant_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_sync_session),
):
    """Get a single tenant by ID. Accessible to system admins and users of that tenant."""
    user_tenant_id = current_user.get("tenant_id")
    is_admin = current_user.get("user_type") in ("SYSTEM_ADMIN", "TENANT_ADMIN")
    if not is_admin and user_tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    service = TenantService(db)
    tenant = service.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@api.post("/tenants/default", response_model=TenantSchema, tags=["tenants"])
def ensure_default_tenant_endpoint(
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_sync_session),
):
    require_system_admin(current_user)
    service = TenantService(db)
    tenants = service.get_tenants()
    if tenants:
        return tenants[0]
    payload = _default_tenant_payload()
    return service.create_tenant(payload)


@api.post("/tenants", response_model=TenantSchema, status_code=status.HTTP_201_CREATED, tags=["tenants"])
def create_tenant_endpoint(
    tenant_in: TenantCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_sync_session),
):
    require_system_admin(current_user)
    service = TenantService(db)
    return service.create_tenant(tenant_in)


@api.put("/tenants/{tenant_id}", response_model=TenantSchema, tags=["tenants"])
def update_tenant_endpoint(
    tenant_id: int,
    tenant_update: TenantUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_sync_session),
):
    require_system_admin(current_user)
    service = TenantService(db)
    return service.update_tenant(tenant_id, tenant_update)


@api.delete("/tenants/{tenant_id}", tags=["tenants"])
def delete_tenant_endpoint(
    tenant_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_sync_session),
):
    require_system_admin(current_user)
    service = TenantService(db)
    return service.delete_tenant(tenant_id)


_MODEL_STATUS = {
    "is_trained": True,
    "last_modified": datetime.utcnow().isoformat() + "Z",
    "file_size_mb": 12.3,
    "epoch": 10,
    "training_loss": 0.1234,
}


@api.get("/model/status")
async def model_status():
    return _MODEL_STATUS

# ------------------------------------------------------------------------------
# Include sub-routers
# ------------------------------------------------------------------------------
# Phase 5: Stochastic distribution API
from app.api.endpoints.stochastic import router as stochastic_router
api.include_router(stochastic_router)

# Phase 5: Stochastic analytics API
from app.api.endpoints.stochastic_analytics import router as stochastic_analytics_router
api.include_router(stochastic_analytics_router)

# Phase 6 Sprint 3: Monitoring & Observability
from app.api.endpoints.health import router as health_router
from app.api.endpoints.metrics import router as metrics_router
api.include_router(health_router, prefix="/health", tags=["health"])
api.include_router(metrics_router, prefix="/metrics", tags=["metrics"])

# Phase 6 Sprint 4: User Experience Enhancements
from app.api.endpoints.templates import router as templates_router

# Phase 7 Sprint 2: A2A Collaboration
from app.api.endpoints.chat import router as chat_router
api.include_router(chat_router)

# Phase 7 Sprint 4: Advanced A2A Features
from app.api.endpoints.conversation import router as conversation_router
from app.api.endpoints.pattern_analysis import router as pattern_analysis_router
from app.api.endpoints.visibility import router as visibility_router
from app.api.endpoints.negotiation import router as negotiation_router
from app.api.endpoints.optimization import router as optimization_router
api.include_router(conversation_router)
api.include_router(pattern_analysis_router)
api.include_router(visibility_router)
api.include_router(negotiation_router)
api.include_router(optimization_router)
api.include_router(templates_router)

# Phase 7 Sprint 5: Gamification & Enhanced Gameplay
from app.api.endpoints.gamification import router as gamification_router
api.include_router(gamification_router, prefix="/gamification", tags=["gamification"])

from app.api.endpoints.reporting import router as reporting_router
api.include_router(reporting_router, prefix="/reports", tags=["reporting"])

# Post-Phase 7: Advanced AI/ML - Predictive Analytics
from app.api.endpoints.predictive_analytics import router as predictive_analytics_router
api.include_router(predictive_analytics_router, prefix="/predictive-analytics", tags=["predictive-analytics"])

# Option 1: Enterprise Features - SSO/LDAP Integration
from app.api.endpoints.sso import router as sso_router
api.include_router(sso_router, prefix="/sso", tags=["sso", "authentication"])

# Tenant management
from app.api.endpoints.tenant import router as tenant_router
api.include_router(tenant_router, prefix="/tenants", tags=["tenants", "organization"])

# Option 1: Enterprise Features - RBAC
from app.api.endpoints.rbac import router as rbac_router
api.include_router(rbac_router, prefix="/rbac", tags=["rbac", "permissions", "roles"])

# Option 1: Enterprise Features - Audit Logging
from app.api.endpoints.audit import router as audit_router
api.include_router(audit_router, prefix="/audit", tags=["audit", "logging"])

# Option 2: Mobile Application - Push Notifications
from app.api.endpoints.notifications import router as notifications_router
api.include_router(notifications_router, prefix="/notifications", tags=["notifications", "mobile"])

# Phase 2: AWS SC Compliance - Production Orders, Capacity Planning, Suppliers, Inventory Projection & Lot Sizing
from app.api.endpoints.capacity_plans import router as capacity_plans_router
from app.api.endpoints.rl import router as rl_router
from app.api.endpoints.model import router as model_router
api.include_router(capacity_plans_router, prefix="/capacity-plans", tags=["capacity-plans"])
from app.api.endpoints.resource_heatmap import router as resource_heatmap_router
api.include_router(resource_heatmap_router, prefix="/resource-heatmap", tags=["resource-heatmap", "capacity-plans"])
api.include_router(rl_router)
api.include_router(model_router)

# Phase 3: MRP, Purchase Orders, Transfer Orders

# Invoice and 3-Way Matching API

# Mixed Scenario ATP/CTP endpoints (Phase 3)
from app.api.endpoints.mixed_scenario import router as mixed_scenario_router
api.include_router(mixed_scenario_router)  # Mixed scenario router has /mixed-scenarios prefix in routes

# New terminology: Mixed Alternative endpoints (Feb 2026)
# These endpoints use the new Alternative/ScenarioUser terminology
# while maintaining backward compatibility with the existing mixed-scenarios routes
# mixed_alternative.py removed - consolidated into mixed_scenario.py

# Phase 4: Supply Planning, Sourcing & Analytics
from app.api.endpoints.analytics import router as analytics_router
from app.api.endpoints.risk_analysis import router as risk_analysis_router
from app.api.endpoints.shipment_tracking import router as shipment_tracking_router
from app.api.endpoints.recommendations import router as recommendations_router
from app.api.endpoints.collaboration import router as collaboration_router
from app.api.endpoints.project_orders import router as project_orders_router
from app.api.endpoints.turnaround_orders import router as turnaround_orders_router
from app.api.endpoints.user_capabilities import router as user_capabilities_router
from app.api.endpoints.capabilities import router as capabilities_router
api.include_router(analytics_router, prefix="/analytics", tags=["analytics", "kpi"])
api.include_router(risk_analysis_router, prefix="/risk-analysis", tags=["risk-analysis", "insights"])
api.include_router(shipment_tracking_router, prefix="/shipment-tracking", tags=["shipment-tracking", "material-visibility"])
api.include_router(recommendations_router, prefix="/recommendations", tags=["recommendations", "planning"])
from app.api.endpoints.scenario_planning import router as scenario_planning_router
api.include_router(scenario_planning_router, prefix="/scenario-planning", tags=["scenario-planning"])
api.include_router(collaboration_router, prefix="/collaboration", tags=["collaboration", "sprint5"])

# Inline Comments API
from app.api.endpoints.comments import router as comments_router
api.include_router(comments_router, prefix="/comments", tags=["comments", "collaboration"])

# Team Messaging API
from app.api.endpoints.team_messaging import router as team_messaging_router
api.include_router(team_messaging_router)  # Team messaging router has prefix="/team-messaging" in endpoint file

# Forecast Exception Alerts API
from app.api.endpoints.forecast_exceptions import router as forecast_exceptions_router
api.include_router(forecast_exceptions_router)  # prefix="/forecast-exceptions" defined in router

# Conformal Prediction API
from app.api.endpoints.conformal_prediction import router as conformal_prediction_router
api.include_router(conformal_prediction_router)  # prefix="/conformal-prediction" defined in router

# Agent Stochastic Parameters API
from app.api.endpoints.agent_stochastic_params import router as agent_stochastic_params_router
api.include_router(agent_stochastic_params_router)  # prefix="/agent-stochastic-params" defined in router

# Rebalancing API
from app.api.endpoints.rebalancing import router as rebalancing_router
api.include_router(rebalancing_router)  # prefix="/rebalancing" defined in router

# Approval Templates API
from app.api.endpoints.approval_templates import router as approval_templates_router
api.include_router(approval_templates_router)  # prefix="/approval-templates" defined in router

# Forecast Adjustments API
from app.api.endpoints.forecast_adjustments import router as forecast_adjustments_router
api.include_router(forecast_adjustments_router, prefix="/forecast-adjustments", tags=["forecast-adjustments", "demand-planning"])

# Forecast Pipeline API

# Warm Start API
from app.api.endpoints.warm_start import router as warm_start_router
api.include_router(warm_start_router, tags=["warm-start"])

# User Directives API — "Talk to Me" natural language context capture
from app.api.endpoints.user_directives import router as directives_router
api.include_router(directives_router, tags=["directives"])

# Provisioning API — Powell Cascade warm-start stepper
from app.api.endpoints.provisioning import router as provisioning_router
api.include_router(provisioning_router, tags=["provisioning"])

# Scenario Engine API — Machine-speed what-if planning
from app.api.endpoints.scenarios_engine import router as scenarios_engine_router
api.include_router(scenarios_engine_router, tags=["scenario-engine"])

# Email Signal Intelligence — GDPR-safe email ingestion for SC signals
from app.api.endpoints.email_signals import router as email_signals_router
api.include_router(email_signals_router, tags=["email-signals"])

# Experiential Knowledge — Alicke's "Planner Was the System" (Powell Bₜ)
from app.api.endpoints.experiential_knowledge import router as ek_router
api.include_router(ek_router, tags=["experiential-knowledge"])

# Hierarchy Tree — site/product hierarchy for user scope picker
@api.get("/hierarchy/site-tree", tags=["hierarchy"])
async def get_site_hierarchy_tree(
    tenant_id: int = None,
    current_user = Depends(get_current_user),
):
    """Return site hierarchy tree for user scope assignment.
    Auto-resolves tenant from authenticated user if tenant_id not provided.
    """
    from sqlalchemy import select as sa_select
    from app.db.session import async_session_factory
    from app.models.planning_hierarchy import SiteHierarchyNode
    resolved_tenant = tenant_id or (current_user.get("tenant_id") if current_user else None)
    if not resolved_tenant:
        return []
    async with async_session_factory() as db:
        result = await db.execute(
            sa_select(SiteHierarchyNode)
            .where(SiteHierarchyNode.tenant_id == resolved_tenant)
            .order_by(SiteHierarchyNode.hierarchy_path)
        )
        nodes = result.scalars().all()
        return [
            {
                "id": n.id, "code": n.code, "name": n.name,
                "level": n.hierarchy_level, "parent_id": n.parent_id,
                "depth": n.depth,
            }
            for n in nodes
        ]

@api.get("/hierarchy/product-tree", tags=["hierarchy"])
async def get_product_hierarchy_tree(
    tenant_id: int = None,
    current_user = Depends(get_current_user),
):
    """Return product hierarchy tree for user scope assignment.
    Auto-resolves tenant from authenticated user if tenant_id not provided.
    """
    from sqlalchemy import select as sa_select
    from app.db.session import async_session_factory
    from app.models.planning_hierarchy import ProductHierarchyNode
    resolved_tenant = tenant_id or (current_user.get("tenant_id") if current_user else None)
    if not resolved_tenant:
        return []
    async with async_session_factory() as db:
        result = await db.execute(
            sa_select(ProductHierarchyNode)
            .where(ProductHierarchyNode.tenant_id == resolved_tenant)
            .order_by(ProductHierarchyNode.hierarchy_path)
        )
        nodes = result.scalars().all()
        return [
            {
                "id": n.id, "code": n.code, "name": n.name,
                "level": n.hierarchy_level, "parent_id": n.parent_id,
                "depth": n.depth,
            }
            for n in nodes
        ]

# BSC Configuration — tenant-admin BSC weights for CDT calibration loss function
from app.api.endpoints.bsc_config import router as bsc_config_router
api.include_router(bsc_config_router, tags=["bsc-config"])

# Tenant Display Preferences — UI identifier display mode (name vs id)
from app.api.endpoints.tenant_preferences import router as tenant_prefs_router
api.include_router(tenant_prefs_router, tags=["tenant-preferences"])

# External Signal Intelligence — Outside-in planning data (FRED, weather, energy, GDELT, trends, FDA)
from app.api.endpoints.external_signals import router as external_signals_router
api.include_router(external_signals_router, tags=["external-signals"])

# Slack Signal Intelligence — SC signals from Slack channels
from app.api.endpoints.slack_signals import router as slack_signals_router
api.include_router(slack_signals_router, tags=["slack-signals"])

# Promotional Planning — Extension to AWS SC supplementary_time_series (PROMOTION)

# Product Lifecycle — NPI, EOL, Markdown/Clearance management
from app.api.endpoints.product_lifecycle import router as product_lifecycle_router
api.include_router(product_lifecycle_router, tags=["product-lifecycle"])

# Consensus Planning API

# Exception Workflows API (Phase 3)
from app.api.endpoints.exception_workflows import router as exception_workflows_router
api.include_router(exception_workflows_router, prefix="/exception-workflows", tags=["exception-workflows", "planning"])

# Advanced Order Management API (Phase 3.4)
from app.api.endpoints.order_management import router as order_management_router
api.include_router(order_management_router, prefix="/order-management", tags=["order-management", "planning"])

# Carrier Integration API (Phase 3.5)
from app.api.endpoints.carrier_integration import router as carrier_integration_router
api.include_router(carrier_integration_router, prefix="/carrier-integration", tags=["carrier-integration", "shipping"])

# project44 Integration API
from app.api.endpoints.p44_integration import router as p44_router
api.include_router(p44_router, prefix="/p44", tags=["p44", "integration", "visibility"])

# TMS RESTful API — clean resource paths for frontend pages
from app.api.endpoints.tms_api import carriers_router, loads_router, tms_operations_router, rates_router, routes_router
api.include_router(carriers_router, prefix="/carriers", tags=["carriers", "tms"])
api.include_router(loads_router, prefix="/loads", tags=["loads", "tms"])
api.include_router(tms_operations_router, tags=["tms", "operations"])
api.include_router(rates_router, prefix="/rates", tags=["rates", "tms"])
api.include_router(routes_router, prefix="/shipments", tags=["shipment-routes", "tms"])

# Transportation Plan view — Phase 0 of the Tactical Planning Re-Architecture
from app.api.endpoints.tms_api import plans_router
api.include_router(plans_router, prefix="/transportation-plans", tags=["transportation-plans", "tms"])

# TMS-native scenario runner
from app.api.endpoints.tms_scenarios import router as tms_scenarios_router
api.include_router(tms_scenarios_router, prefix="/tms-scenarios", tags=["tms-scenarios"])

# Collaborative Editing API (Phase 3.6)
from app.api.endpoints.collaborative_editing import router as collaborative_editing_router
api.include_router(collaborative_editing_router, prefix="/collaborative-editing", tags=["collaborative-editing", "real-time"])

api.include_router(project_orders_router, prefix="/project-orders", tags=["project-orders", "sprint6"])
api.include_router(turnaround_orders_router, prefix="/turnaround-orders", tags=["turnaround-orders", "sprint6"])
api.include_router(user_capabilities_router)  # prefix="/users" defined in router
api.include_router(capabilities_router)  # prefix="/capabilities" defined in router - for /capabilities/me

# User CRUD (create, update, delete, change-password, status)
from app.api.endpoints.users import router as users_crud_router
api.include_router(users_crud_router, prefix="/users", tags=["users"])

# Agent-Human Mapping (topology-based user recommendations)
from app.api.endpoints.agent_human_mapping import router as agent_human_mapping_router
api.include_router(agent_human_mapping_router, prefix="/agent-human-mapping", tags=["agent-human-mapping"])

# Decision Metrics API (Agent Performance for Powell Framework Dashboards)
from app.api.endpoints.decision_metrics import router as decision_metrics_router
api.include_router(decision_metrics_router, prefix="/decision-metrics", tags=["decision-metrics", "powell"])

# Hierarchical Metrics API (Gartner-aligned metrics with hierarchy drill-down)
from app.api.endpoints.hierarchical_metrics import router as hierarchical_metrics_router
api.include_router(hierarchical_metrics_router, prefix="/hierarchical-metrics", tags=["metrics", "gartner"])

# Executive Briefing API (LLM-synthesized strategy briefings)
from app.api.endpoints.executive_briefing import router as executive_briefing_router
api.include_router(executive_briefing_router, prefix="/executive-briefing", tags=["executive-briefing"])

# Decision Stream — LLM-First UI with Decision-Back Planning
from app.api.endpoints.decision_stream import router as decision_stream_router
api.include_router(decision_stream_router, tags=["decision-stream"])
from app.api.endpoints.governance import router as governance_router
api.include_router(governance_router, tags=["governance"])
from app.api.endpoints.decision_stream_ws import router as decision_stream_ws_router
app.include_router(decision_stream_ws_router)

# Full-Level Pegging & Multi-Stage CTP API
from app.api.endpoints.pegging import router as pegging_router
api.include_router(pegging_router, prefix="/pegging", tags=["pegging", "ctp", "planning"])

# Routers previously only on api_v1 router (consolidated to single /api/v1 prefix)
from app.api.endpoints import (
    dashboard_router,
    advanced_analytics_router,
    monte_carlo_router,
    supply_plan_crud_router,
    atp_ctp_router,
    vendor_lead_time_router,
    production_process_router,
    resource_capacity_router,
    demand_collaboration_router,
    service_order_router,
    analytics_optimization_router,
    simulation_execution_router,
    sync_jobs_router,
    workflows_router,
    synthetic_data_router,
    sap_data_management_router,
    erp_integration_router,
    tms_erp_integration_router,
    autonomy_customers_router,
    sap_change_simulator_router,
    site_agent_router,
    insights_router,
    planning_cascade_router,
    deployment_router,
)
from app.api.endpoints.sap_atp import router as sap_atp_router

from app.api.endpoints.knowledge_base import router as knowledge_base_router
from app.api.endpoints.authorization_protocol import router as authorization_protocol_router
api.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])
api.include_router(advanced_analytics_router, prefix="/advanced-analytics", tags=["advanced-analytics"])
api.include_router(monte_carlo_router, tags=["monte-carlo"])
api.include_router(supply_plan_crud_router, prefix="/supply-plan-crud", tags=["supply-plan-crud"])

# Planning Board (Netting Timeline & Filter Options)
api.include_router(atp_ctp_router, prefix="/atp-ctp", tags=["atp-ctp"])
api.include_router(vendor_lead_time_router, prefix="/vendor-lead-time", tags=["vendor-lead-time"])
api.include_router(production_process_router, prefix="/production-process", tags=["production-process"])
api.include_router(resource_capacity_router, prefix="/resource-capacity", tags=["resource-capacity"])
api.include_router(demand_collaboration_router, prefix="/demand-collaboration", tags=["demand-collaboration"])
api.include_router(service_order_router, prefix="/service-order", tags=["service-order"])
api.include_router(analytics_optimization_router, prefix="/analytics-optimization", tags=["analytics-optimization"])
api.include_router(simulation_execution_router, prefix="/simulation-execution", tags=["simulation-execution"])
api.include_router(sap_atp_router, prefix="/sap-atp", tags=["sap-atp"])
api.include_router(sync_jobs_router, prefix="/sync-jobs", tags=["sync-jobs"])
api.include_router(workflows_router, prefix="/workflows", tags=["workflows"])
api.include_router(synthetic_data_router, prefix="/synthetic-data", tags=["synthetic-data"])
api.include_router(sap_data_management_router, prefix="/sap-data", tags=["sap-data-management"])
api.include_router(erp_integration_router, prefix="/erp", tags=["erp-integration"])
api.include_router(tms_erp_integration_router, prefix="/tms-integration", tags=["tms-integration"])
api.include_router(autonomy_customers_router, prefix="/customers", tags=["customers"])
api.include_router(sap_change_simulator_router, tags=["sap-change-simulator"])
# powell_router + powell_training_router unwired 2026-04-23 — SCP-fork
# endpoint surface (InventoryRebalancing / OrderTracking / allocation on
# SKUs). Rescope / deletion tracked under MIGRATION_REGISTER 1.13.
api.include_router(site_agent_router, tags=["site-agent"])
api.include_router(insights_router, tags=["insights"])
api.include_router(planning_cascade_router, prefix="/planning-cascade", tags=["planning-cascade"])
api.include_router(deployment_router, prefix="/deployment", tags=["deployment"])


api.include_router(knowledge_base_router, tags=["knowledge-base"])
from app.api.endpoints.assistant import router as assistant_router
api.include_router(assistant_router, tags=["assistant"])
api.include_router(authorization_protocol_router, tags=["authorization-protocol"])
from app.api.endpoints.fulfillment_orders import router as fulfillment_orders_router
api.include_router(fulfillment_orders_router, prefix="/fulfillment-orders", tags=["fulfillment-orders"])
from app.api.endpoints.planning_scenarios import router as planning_scenarios_router
api.include_router(planning_scenarios_router, tags=["planning-scenarios"])
from app.api.endpoints.skills_monitoring import router as skills_monitoring_router
api.include_router(skills_monitoring_router, tags=["skills-monitoring"])

# Data Drift Monitor API (long-horizon distributional shift detection)
from app.api.endpoints.data_drift import router as data_drift_router
api.include_router(data_drift_router, tags=["data-drift"])

# Scenario Events API (what-if event injection)
from app.api.endpoints.scenario_events import router as scenario_events_router
api.include_router(scenario_events_router, tags=["scenario-events"])

# Demo auth endpoints (demo-token-redirect, demo-capacity, etc.)
from app.api.endpoints.auth import router as auth_router
api.include_router(auth_router, prefix="/auth", tags=["auth"])

# Demo date shift API — manual trigger for shifting demo data dates forward
from app.api.endpoints.demo_date_shift import router as demo_date_shift_router
api.include_router(demo_date_shift_router, prefix="/demo", tags=["demo"])

# Freight Procurement — carrier waterfall tendering (first TMS-native TRM endpoint)
from app.api.endpoints.freight_procurement import router as freight_procurement_router
api.include_router(freight_procurement_router, tags=["freight-procurement"])

# Capacity Promise — SENSE-phase Load-status promotion (PLANNING → READY)
from app.api.endpoints.capacity_promise import router as capacity_promise_router
api.include_router(capacity_promise_router, tags=["capacity-promise"])

# Shipment Tracking TRM — SENSE-phase in-transit exception detection
# (distinct from the pre-existing /shipment-tracking/* material-visibility
#  endpoints; mounted at /shipment-tracking-trm/*)
from app.api.endpoints.shipment_tracking_trm import router as shipment_tracking_trm_router
api.include_router(shipment_tracking_trm_router, tags=["shipment-tracking-trm"])

# Exception Management TRM — ASSESS-phase shipment exception triage
from app.api.endpoints.exception_management import router as exception_management_router
api.include_router(exception_management_router, tags=["exception-management"])

# Broker Routing TRM — ACQUIRE-phase broker-vs-asset-carrier routing
from app.api.endpoints.broker_routing import router as broker_routing_router
api.include_router(broker_routing_router, tags=["broker-routing"])

# Dock Scheduling TRM — PROTECT-phase dock-door appointment triage
from app.api.endpoints.dock_scheduling import router as dock_scheduling_router
api.include_router(dock_scheduling_router, tags=["dock-scheduling"])

# Load Build TRM — BUILD-phase shipment→load consolidation
from app.api.endpoints.load_build import router as load_build_router
api.include_router(load_build_router, tags=["load-build"])

# Demand Sensing TRM — SENSE-phase short-horizon forecast adjustment
# (Trigg's tracking signal + order-pipeline velocity + asymmetric loss)
from app.api.endpoints.demand_sensing import router as demand_sensing_trm_router
api.include_router(demand_sensing_trm_router, tags=["demand-sensing-trm"])

# Capacity Buffer TRM — ASSESS-phase lane-level capacity buffer sizing
# (conformal P90-P50 + tender reject rate + newsvendor-inspired multiplier)
from app.api.endpoints.capacity_buffer import router as capacity_buffer_trm_router
api.include_router(capacity_buffer_trm_router, tags=["capacity-buffer-trm"])

# Intermodal Transfer TRM — BUILD-phase mode-shift eligibility & viability
# (Oracle OTM gates + drayage-decomposed all-in cost + carrying cost)
from app.api.endpoints.intermodal_transfer import router as intermodal_transfer_trm_router
api.include_router(intermodal_transfer_trm_router, tags=["intermodal-transfer-trm"])

# Equipment Reposition TRM — REFLECT-phase empty-vehicle redistribution
# (Powell 1996 / Crainic et al. 1993 dynamic assignment + ROI gating)
from app.api.endpoints.equipment_reposition import router as equipment_reposition_trm_router
api.include_router(equipment_reposition_trm_router, tags=["equipment-reposition-trm"])

# ------------------------------------------------------------------------------
# Mount routers
# ------------------------------------------------------------------------------
# Single API router (all routes at /api/v1/...)
app.include_router(api)

# TMS A2A surface — §3.32 Phase 4. Adds /.well-known/agent.json
# + /a2a/ for cross-plane internal-agent callers (SCP rebalancing
# TRMs evaluating consolidation candidates, DP-side planners
# requesting lane ETAs, etc.). Per the §3.7-bis four-row taxonomy.
try:
    from app.a2a import mount as mount_a2a
    mount_a2a(app)
except ImportError as exc:
    import logging
    logging.getLogger(__name__).warning(
        "TMS A2A surface not mounted (azirella-a2a-client not installed?): %s",
        exc,
    )

# ------------------------------------------------------------------------------
# Root
# ------------------------------------------------------------------------------
@app.get("/")
def root():
    return {"name": "Autonomy API", "docs": f"{API_PREFIX}/docs"}

# ------------------------------------------------------------------------------
# Error handlers (optional but useful)
# ------------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

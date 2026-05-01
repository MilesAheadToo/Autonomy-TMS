import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
from urllib.parse import urlparse

from pydantic import Field, validator
from app.core.db_urls import resolve_sync_database_url

try:  # pragma: no cover - dependency fallback for offline environments
    from pydantic_settings import BaseSettings  # type: ignore
except ImportError:  # pragma: no cover
    from pydantic import BaseModel

    class BaseSettings(BaseModel):
        """Minimal fallback replacement for :class:`pydantic_settings.BaseSettings`.

        The real ``BaseSettings`` loader automatically pulls values from the
        process environment. When the dedicated ``pydantic-settings`` package
        is unavailable (common in restricted execution sandboxes without
        network access), we recreate the tiny subset of behaviour the backend
        relies on so configuration continues to work during tests and scripts.

        Only top-level fields are considered and they map to uppercase
        environment variables that share the field name (e.g. ``SQLALCHEMY_``
        ``DATABASE_URI``). Any explicit keyword arguments still override the
        environment-derived values, mirroring the upstream API.
        """

        class Config:
            env_file = ".env"

        def __init__(self, **data: Any):  # type: ignore[override]
            env_values: Dict[str, Any] = {}
            for field_name in self.__fields__:
                env_key = field_name.upper()
                if env_key in os.environ:
                    env_values[field_name] = os.environ[env_key]
            env_values.update(data)
            super().__init__(**env_values)


def _can_connect(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False

class Settings(BaseSettings):
    # Application
    PROJECT_NAME: str = "Autonomy API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = True
    ENVIRONMENT: str = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Security
    SECRET_KEY: str = secrets.token_urlsafe(32)
    REFRESH_SECRET_KEY: str = secrets.token_urlsafe(32)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # 8 days
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30  # 30 days
    
    # CORS settings
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8000", "http://localhost:8088", "http://acer-nitro.local:8088"]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: List[str] = ["*"]
    CORS_ALLOW_HEADERS: List[str] = ["*"]
    
    # Cookie settings
    COOKIE_SECURE: bool = False  # False for local development
    COOKIE_HTTP_ONLY: bool = True
    COOKIE_SAME_SITE: str = "lax"  # 'lax' is correct when behind nginx proxy (same origin)
    COOKIE_DOMAIN: Optional[str] = None
    COOKIE_ACCESS_TOKEN_NAME: str = "access_token"
    COOKIE_TOKEN_TYPE_NAME: str = "token_type"
    COOKIE_PATH: str = "/"
    COOKIE_MAX_AGE: int = 60 * 60 * 24 * 7  # 7 days
    
    # JWT Token settings
    ACCESS_TOKEN_COOKIE_NAME: str = "access_token"
    REFRESH_TOKEN_COOKIE_NAME: str = "refresh_token"
    TOKEN_PREFIX: str = "Bearer"
    
    # CSRF settings
    CSRF_COOKIE_NAME: str = "csrf_token"
    CSRF_HEADER_NAME: str = "X-CSRF-Token"
    CSRF_TOKEN_LENGTH: int = 32
    CSRF_COOKIE_SECURE: bool = False  # False in dev to allow http
    CSRF_COOKIE_HTTP_ONLY: bool = False  # Must be accessible from JS
    CSRF_COOKIE_SAME_SITE: str = "lax"
    CSRF_COOKIE_PATH: str = "/"
    CSRF_COOKIE_DOMAIN: Optional[str] = None  # None allows all domains in dev
    CSRF_EXPIRE_SECONDS: int = 60 * 60 * 24 * 7  # 7 days
    
    # Password settings
    PASSWORD_MIN_LENGTH: int = 12
    PASSWORD_MAX_ATTEMPTS: int = 5
    PASSWORD_LOCKOUT_MINUTES: int = 15
    PASSWORD_HISTORY_SIZE: int = 5
    MAX_LOGIN_ATTEMPTS: int = 5  # Maximum number of failed login attempts before lockout
    
    # Session settings
    SESSION_TIMEOUT_MINUTES: int = 30
    MAX_SESSIONS_PER_USER: int = 5
    
    # Rate limiting
    RATE_LIMIT: str = "100/minute"
    AUTH_RATE_LIMIT: str = "5/minute"
    
    # Security headers
    SECURE_HEADERS: Dict[str, str] = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Content-Security-Policy": "default-src 'self'",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()"
    }
    
    # CORS
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
        "http://localhost:8088",
        "http://127.0.0.1:8088",
        "http://acer-nitro.local:8088",
    ]

    # Frontend URL for CORS and redirects
    FRONTEND_URL: str = "http://localhost:3000"
    FRONTEND_ORIGIN: str = "http://localhost:3000"
    
    # Refresh cookie config
    REFRESH_COOKIE_NAME: str = "refresh_token"
    REFRESH_COOKIE_PATH: str = "/api/v1/auth"
    REFRESH_COOKIE_DOMAIN: Optional[str] = None  # Set to your domain in production
    REFRESH_COOKIE_SAMESITE: str = "lax"  # 'lax' is correct when behind nginx proxy (same origin)
    REFRESH_COOKIE_SECURE: bool = False  # True only in production over HTTPS
    REFRESH_COOKIE_HTTPONLY: bool = True

    # ── Subdomain routing (Option C: Hybrid) ──
    # login.azirella.com → Login portal
    # autonomy.azirella.com → Default app (all tenants via JWT)
    # {slug}.azirella.com → Vanity subdomain (validated against JWT tenant)
    APP_DOMAIN: str = "localhost"           # "azirella.com" in production
    APP_SCHEME: str = "http"                # "https" in production
    APP_PORT: Optional[int] = 8088          # None in production (standard ports)
    LOGIN_SUBDOMAIN: str = "login"          # login.azirella.com
    DEFAULT_SUBDOMAIN: str = "autonomy"     # autonomy.azirella.com
    SUBDOMAIN_ROUTING_ENABLED: bool = False  # Off for local dev, True in prod
    
    # Database configuration
    SQLALCHEMY_DATABASE_URI: str = ""
    
    class Config:
        env_file = ".env"
        
    @validator("SQLALCHEMY_DATABASE_URI", pre=True)
    def assemble_db_connection(cls, v: Optional[str], values: Dict[str, Any]) -> Any:
        if v is not None and v != "":
            return v
        return resolve_sync_database_url()
    
    # WebSocket
    WEBSOCKET_PATH: str = "/ws"
    WEBSOCKET_PING_INTERVAL: int = 25  # seconds
    WEBSOCKET_PING_TIMEOUT: int = 5    # seconds
    WEBSOCKET_MAX_MESSAGE_SIZE: int = 1024 * 1024  # 1MB
    
    # Scenario Settings
    # DEPRECATED: The three constants below are legacy Beer Scenario defaults.
    # New scenarios must seed InvLevel.on_hand_qty via the AWS SC seed script.
    # Cost rates must come from InvPolicy.holding_cost_range / backlog_cost_range.
    INITIAL_INVENTORY: int = 12          # DEPRECATED — legacy Beer Scenario init
    HOLDING_COST_PER_UNIT: float = 0.5   # DEPRECATED — legacy Beer Scenario cost
    BACKORDER_COST_PER_UNIT: float = 1.0  # DEPRECATED — legacy Beer Scenario cost
    DEFAULT_MAX_ROUNDS: int = 52
    
    # AI Settings
    AI_REACTION_TIME: float = 1.0  # seconds to wait before AI makes a move

    # LLM Configuration — provider-agnostic (vLLM, Ollama, or any OpenAI-compatible API)
    LLM_PROVIDER: str = "openai-compatible"
    LLM_MODEL: str = "qwen3-8b"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 1000
    LLM_TIMEOUT: int = 10  # seconds
    LLM_CACHE_TTL: int = 300  # 5 minutes

    # ── Lab gateway (mss1-max) — workload-split env-vars ────────────
    # Every Autonomy backend reaches the lab gateway for all three
    # workloads per the Azirella Assistant platform invariant. See
    # Autonomy-Core/docs/architecture/LLM_ARCHITECTURE.md §2.1.1
    # and Autonomy-Core/docs/dev/LLM_DEPLOYMENT.md.
    LLM_LAB_API_KEY: Optional[str] = None
    LLM_CHAT_API_BASE: Optional[str] = None
    LLM_CHAT_MODEL_NAME: Optional[str] = None
    LLM_NARRATION_API_BASE: Optional[str] = None
    LLM_NARRATION_MODEL_NAME: Optional[str] = None
    LLM_EMBEDDINGS_API_BASE: Optional[str] = None
    LLM_EMBEDDINGS_MODEL_NAME: Optional[str] = None

    # Legacy LLM connection — kept for callers that have not yet
    # migrated to the workload split (MIGRATION_REGISTER §3.16).
    # Deployments point these at the gateway too.
    LLM_API_BASE: Optional[str] = None  # e.g. http://mss1-max.local:8080/v1/chat
    LLM_API_KEY: Optional[str] = None  # API key (reads OPENAI_API_KEY env as fallback)
    LLM_MODEL_NAME: Optional[str] = None  # Served model name (overrides LLM_MODEL)

    # Embedding configuration (legacy aliases — also point at the gateway)
    EMBEDDING_API_BASE: Optional[str] = None  # e.g. http://mss1-max.local:8080/v1/embeddings
    EMBEDDING_MODEL: str = "nomic-embed-text-v1.5"
    EMBEDDING_DIMENSIONS: int = 768
    EMBEDDING_PROVIDER: str = "openai"  # "openai" (Ollama/vLLM/OpenAI/llama.cpp) or "tei" (HuggingFace TEI)

    # RAG Configuration
    RAG_ENABLED: bool = False
    RAG_CHUNK_SIZE: int = 1024
    RAG_CHUNK_OVERLAP: int = 200
    RAG_TOP_K: int = 5

    # Separate KB database (pgvector) — if not set, falls back to main database
    KB_DATABASE_URL: Optional[str] = None
    KB_ASYNC_DATABASE_URL: Optional[str] = None

    # Read-only SCP database (sibling product). Used by the Food Dist TMS overlay
    # ETL extractor to pull source SCP shipment/site/order rows into TMS staging.
    # See docker-compose.override.yml for the network setup.
    SCP_DB_URL: Optional[str] = None

    # ==========================================================================
    # Connection settings
    SAP_HOST: Optional[str] = None  # Application server host (e.g., "sap-server.company.com")
    SAP_SYSNR: str = "00"  # System number
    SAP_CLIENT: str = "100"  # Client number
    SAP_USER: Optional[str] = None  # RFC user
    SAP_PASSWORD: Optional[str] = None  # RFC password

    # ATP/CTP settings
    SAP_USE_BAPI: bool = True  # Use BAPI for real-time ATP (vs table extraction)
    SAP_ATP_CHECK_RULE: str = "A"  # A=ATP only, B=full planning check
    SAP_SYNC_INTERVAL_MINUTES: int = 15  # Default sync interval

    # Safety settings
    SAP_TEST_MODE: bool = False  # Simulate only - no write-back to SAP

    # Redis (for WebSocket message broker and LLM caching in production)
    REDIS_URL: Optional[str] = None
    
    # Rate limiting
    RATE_LIMIT: str = "100/minute"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # CORS methods and headers
    CORS_ALLOW_METHODS: List[str] = ["*"]
    CORS_ALLOW_HEADERS: List[str] = ["*"]
    CORS_EXPOSE_HEADERS: List[str] = []
    CORS_ALLOW_CREDENTIALS: bool = True
    
    @validator("BACKEND_CORS_ORIGINS", pre=True)
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> Union[List[str], str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)
    
    @property
    def allowed_origins(self) -> Set[str]:
        """Get the set of allowed origins for CORS."""
        if "*" in self.BACKEND_CORS_ORIGINS:
            return {"*"}
        return {origin.strip() for origin in self.BACKEND_CORS_ORIGINS if origin.strip()}
    
    @property
    def is_production(self) -> bool:
        """Check if the application is running in production mode."""
        return self.ENVIRONMENT == "production"
    
    @property
    def websocket_url(self) -> str:
        """Get the WebSocket URL for the current environment."""
        if self.ENVIRONMENT == "production":
            return f"wss://{self.HOST}{self.WEBSOCKET_PATH}"
        return f"ws://{self.HOST}:{self.PORT}{self.WEBSOCKET_PATH}"

    @property
    def DATABASE_URL(self) -> str:
        """Get the database URL for APScheduler and other services.

        This is an alias for SQLALCHEMY_DATABASE_URI for compatibility
        with services that expect DATABASE_URL (e.g., SyncSchedulerService).
        """
        return self.SQLALCHEMY_DATABASE_URI

    class Config:
        case_sensitive = True
        env_file = ".env"
        env_file_encoding = "utf-8"
        
        @classmethod
        def customise_sources(
            cls,
            init_settings,
            env_settings,
            file_secret_settings,
        ):
            # Prioritize environment variables over .env file
            return (
                init_settings,
                env_settings,
                file_secret_settings,
            )
            
        # Allow extra fields in the config
        extra = "ignore"
        
        @classmethod
        def parse_env_var(cls, field_name: str, raw_val: str) -> Any:
            if field_name == "BACKEND_CORS_ORIGINS" and raw_val:
                if isinstance(raw_val, str) and not raw_val.startswith("["):
                    return [origin.strip() for origin in raw_val.split(",")]
                elif isinstance(raw_val, list):
                    return raw_val
            return cls.json_loads(raw_val)

def get_settings() -> Settings:
    """Get the application settings with MySQL configuration."""
    settings = Settings()
    uri = settings.SQLALCHEMY_DATABASE_URI
    if not uri:
        uri = settings.__class__.assemble_db_connection("", {})  # type: ignore[arg-type]
        object.__setattr__(settings, "SQLALCHEMY_DATABASE_URI", uri)
    parsed = urlparse(uri)
    server = parsed.hostname or "db"
    db = parsed.path.lstrip("/") or "autonomy"
    if uri.startswith("mysql"):
        port = parsed.port or 3306
        print(f"Using MySQL database at: {server}:{port}/{db}")
    elif uri.startswith("postgresql"):
        port = parsed.port or 5432
        print(f"Using PostgreSQL database at: {server}:{port}/{db}")
    else:
        print(f"Using database at: {uri}")
    return settings

# Global settings instance
settings = get_settings()

# JWT token configuration
def create_access_token(
    subject: Union[str, Any], 
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create a JWT access token.
    
    Args:
        subject: The subject of the token (usually user ID or username)
        expires_delta: Optional timedelta for token expiration
        
    Returns:
        str: Encoded JWT token
    """
    from datetime import datetime, timezone
    import jwt
    
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

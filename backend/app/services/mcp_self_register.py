"""§3.8.2 — TMS MCP self-registration on app startup.

TMS runs an MCP server (``app/mcp_server/``) as a sidecar to the
modular monolith. Peer planes (SCP, DP) discover TMS's MCP URL
through the plane registry (``plane_registration.mcp_endpoint_url``
added by Core migration ``0048``) — *not* through hard-coded env
vars on the peer's side. This module is TMS's contribution to that
contract: on startup, sync every active Transport-plane
registration to the currently-configured public MCP URL.

Why sync on every startup rather than at provisioning time:

  * Idempotent. Re-running the registration is harmless.
  * URL changes (port shift, host migration, customer-managed
    DNS) propagate without touching provisioning code.
  * Doesn't require coordinated provisioning + sidecar deployment
    timing — provisioning creates the row, the next TMS restart
    populates the URL.

Env var contract:

  * ``TMS_MCP_PUBLIC_URL`` — the URL peers should call. Required to
    activate self-registration. When unset, the function logs INFO
    and exits cleanly (deployments that don't expose MCP externally
    legitimately leave this empty).

Mirror of the SCP-side ``mcp_self_register.py`` in Autonomy-SCP
commit 31c5b320; differs only in plane (``Plane.TRANSPORT`` vs
``Plane.SUPPLY``) and env var name. Two distinct copies, not a
shared module — each plane registers its own endpoint, and the
trivial difference between the two doesn't justify a Core-side
shared service.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


_ENV_VAR = "TMS_MCP_PUBLIC_URL"


def _resolve_public_url() -> Optional[str]:
    """Read the publicly-resolvable MCP URL from env, or return None.

    Two env-var spellings are accepted for backward compatibility:
    ``TMS_MCP_PUBLIC_URL`` (preferred — explicit "public") and
    ``MCP_PUBLIC_URL`` (generic fallback for deployments that set
    a single value per plane).
    """
    url = os.getenv(_ENV_VAR) or os.getenv("MCP_PUBLIC_URL")
    return url.strip() if url else None


def self_register_mcp_endpoint(session_factory) -> int:
    """Sync the TMS MCP URL into every active Transport-plane registration.

    :param session_factory: a zero-arg callable returning a new
        synchronous SQLAlchemy ``Session``. Same pattern as the
        existing startup hooks (``sync_session_factory``) — we hold
        the session only as long as the sync takes.
    :returns: number of registrations updated. ``0`` when the env
        var is unset (deployment opts out) or when no Transport-plane
        registrations exist yet (fresh install).
    """
    url = _resolve_public_url()
    if not url:
        logger.info(
            "§3.8.2: TMS MCP self-registration skipped — %s is unset. "
            "Cross-plane peers will see Plane.TRANSPORT mcp_endpoint_url "
            "as NULL and drop into solo-mode.",
            _ENV_VAR,
        )
        return 0

    # Lazy imports — keep service-module load time cheap.
    from azirella_data_model.planes import Plane, PlaneRegistration, PlaneRegistry

    updated = 0
    skipped = 0
    db = session_factory()
    try:
        rows = (
            db.query(PlaneRegistration)
            .filter(
                PlaneRegistration.plane == Plane.TRANSPORT,
                PlaneRegistration.deregistered_at.is_(None),
            )
            .all()
        )
        for row in rows:
            if row.mcp_endpoint_url == url:
                skipped += 1
                continue
            ok = PlaneRegistry.set_mcp_endpoint(
                db,
                tenant_id=row.tenant_id,
                plane=Plane.TRANSPORT,
                mcp_endpoint_url=url,
                config_id=row.config_id,
            )
            if ok:
                updated += 1
        db.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort startup hook
        db.rollback()
        logger.warning(
            "§3.8.2: TMS MCP self-registration failed (will retry on next "
            "startup): %s", exc,
        )
        return 0
    finally:
        db.close()

    logger.info(
        "§3.8.2: TMS MCP self-registration synced %d registration(s) to %s "
        "(%d already up-to-date).",
        updated, url, skipped,
    )
    return updated

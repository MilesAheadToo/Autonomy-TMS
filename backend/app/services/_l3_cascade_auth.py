"""§3.46 Phase 5 — auth helpers for the L3 cascade HTTP endpoint.

Lives under ``services/`` (NOT under ``services/powell/`` or
``api/endpoints/``) so unit tests can import the helpers without
triggering either of those packages' eager re-export ``__init__``s,
which transitively pull in the FastAPI app's auth + DB stack. The
helpers themselves are pure: they only need ``UserTypeEnum`` from
Core's ``tenant`` package and FastAPI's ``HTTPException`` class.

Public helpers:
- :func:`is_operator` — operator-level authority predicate.
- :func:`enforce_tenant_scope` — same-tenant gate with operator escape.

The helpers take a ``user`` argument duck-typed via ``getattr``: any
object exposing ``id``, ``tenant_id``, ``is_superuser``, ``user_type``
attributes works (the real ``User`` ORM class, ``ServiceAccountUser``
sentinel, or a ``SimpleNamespace`` test fixture). This is why
``User`` is only imported under :data:`TYPE_CHECKING`.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException

from azirella_data_model.tenant import UserTypeEnum

if TYPE_CHECKING:
    from app.models.user import User


logger = logging.getLogger(__name__)


def is_operator(user: "User") -> bool:
    """§3.46 Phase 5 — operator-level authority for cross-tenant
    cascade invocation.

    Two equivalent paths to operator authority:
    - ``is_superuser=True`` — legacy account flag, still honoured.
    - ``user_type == UserTypeEnum.SYSTEM_ADMIN`` — the canonical
      role-based path, set during user provisioning for ops engineers
      and SRE accounts.

    ``TENANT_ADMIN`` is explicitly NOT operator-level: a tenant admin's
    authority is scoped to *their own* tenant. They use the same
    same-tenant path as a regular ``USER``.
    """
    if getattr(user, "is_superuser", False):
        return True
    user_type = getattr(user, "user_type", None)
    return user_type == UserTypeEnum.SYSTEM_ADMIN


def enforce_tenant_scope(user: "User", requested_tenant_id: int) -> None:
    """Caller may only invoke cascades for their own tenant — unless
    they have operator authority (§3.46 Phase 5).

    Same-tenant invocation: any authenticated active user.
    Cross-tenant invocation: ``is_superuser=True`` or
    ``user_type=SYSTEM_ADMIN`` (see :func:`is_operator`).
    """
    user_tenant = getattr(user, "tenant_id", None)
    if user_tenant is not None and user_tenant != requested_tenant_id:
        if is_operator(user):
            logger.info(
                "L3 cascade — cross-tenant invocation by operator user_id=%s "
                "(home_tenant=%s) targeting tenant_id=%s",
                getattr(user, "id", None), user_tenant, requested_tenant_id,
            )
            return
        raise HTTPException(
            status_code=403,
            detail=(
                f"User tenant_id={user_tenant} cannot invoke cascade "
                f"for tenant_id={requested_tenant_id}; cross-tenant "
                "invocation requires operator authority "
                "(is_superuser or SYSTEM_ADMIN)"
            ),
        )


__all__ = ["enforce_tenant_scope", "is_operator"]

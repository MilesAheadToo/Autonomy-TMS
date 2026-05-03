"""§3.46 Phase 5 — operator-role authority for cross-tenant L3 cascade.

Unit tests for the auth helpers on the L3 cascade HTTP endpoint
(``app.api.endpoints.l3_cascade``):

- ``_is_operator`` — returns True for ``is_superuser=True`` OR
  ``user_type == SYSTEM_ADMIN``; False otherwise (USER, TENANT_ADMIN,
  unset).
- ``_enforce_tenant_scope`` — same-tenant always passes; cross-tenant
  raises 403 unless the caller is an operator; operator path logs but
  passes.

The endpoint body itself stays exercised via CLI tests + scheduled-job
tests (which share the runner code path); the auth surface is the
Phase 5 contribution and tested directly here.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from azirella_data_model.tenant import UserTypeEnum

from app.services._l3_cascade_auth import (
    enforce_tenant_scope as _enforce_tenant_scope,
    is_operator as _is_operator,
)


def _user(
    *,
    id: int = 1,
    tenant_id: int = 42,
    is_superuser: bool = False,
    user_type: Any = UserTypeEnum.USER,
) -> SimpleNamespace:
    """Build a User-shaped sentinel — the helpers only access
    attributes via ``getattr``, so SimpleNamespace covers the contract
    without booting the ORM."""
    return SimpleNamespace(
        id=id,
        tenant_id=tenant_id,
        is_superuser=is_superuser,
        user_type=user_type,
    )


# ---------------------------------------------------------------------------
# _is_operator
# ---------------------------------------------------------------------------


def test_operator_via_is_superuser() -> None:
    assert _is_operator(_user(is_superuser=True)) is True


def test_operator_via_system_admin_role() -> None:
    assert _is_operator(_user(user_type=UserTypeEnum.SYSTEM_ADMIN)) is True


def test_operator_both_paths_compose() -> None:
    """A user who is both is_superuser AND SYSTEM_ADMIN is still
    operator (no AND-of-two-conditions trap)."""
    assert _is_operator(_user(
        is_superuser=True, user_type=UserTypeEnum.SYSTEM_ADMIN,
    )) is True


def test_tenant_admin_is_not_operator() -> None:
    """TENANT_ADMIN's authority is scoped to their own tenant — they
    use the same same-tenant path as USER for the cascade endpoint."""
    assert _is_operator(_user(user_type=UserTypeEnum.TENANT_ADMIN)) is False


def test_regular_user_is_not_operator() -> None:
    assert _is_operator(_user(user_type=UserTypeEnum.USER)) is False


def test_unset_user_type_is_not_operator() -> None:
    """A user object without ``user_type`` attribute (e.g., legacy
    test fixtures) is treated as non-operator."""
    user = SimpleNamespace(id=1, tenant_id=42, is_superuser=False)
    assert _is_operator(user) is False


# ---------------------------------------------------------------------------
# _enforce_tenant_scope
# ---------------------------------------------------------------------------


def test_same_tenant_invocation_passes_for_regular_user() -> None:
    """USER targeting their own tenant_id → no exception."""
    user = _user(tenant_id=42, user_type=UserTypeEnum.USER)
    _enforce_tenant_scope(user, requested_tenant_id=42)  # no raise


def test_cross_tenant_blocked_for_regular_user() -> None:
    """USER targeting a different tenant → 403."""
    user = _user(tenant_id=42, user_type=UserTypeEnum.USER)
    with pytest.raises(HTTPException) as exc_info:
        _enforce_tenant_scope(user, requested_tenant_id=99)
    assert exc_info.value.status_code == 403
    assert "operator authority" in exc_info.value.detail.lower()


def test_cross_tenant_blocked_for_tenant_admin() -> None:
    """TENANT_ADMIN's authority is scoped to their own tenant — no
    cross-tenant cascade invocation, even for admins of other tenants."""
    user = _user(tenant_id=42, user_type=UserTypeEnum.TENANT_ADMIN)
    with pytest.raises(HTTPException) as exc_info:
        _enforce_tenant_scope(user, requested_tenant_id=99)
    assert exc_info.value.status_code == 403


def test_cross_tenant_passes_for_superuser() -> None:
    """is_superuser=True → cross-tenant invocation allowed."""
    user = _user(tenant_id=42, is_superuser=True)
    _enforce_tenant_scope(user, requested_tenant_id=99)  # no raise


def test_cross_tenant_passes_for_system_admin() -> None:
    """user_type=SYSTEM_ADMIN → cross-tenant invocation allowed."""
    user = _user(tenant_id=42, user_type=UserTypeEnum.SYSTEM_ADMIN)
    _enforce_tenant_scope(user, requested_tenant_id=99)  # no raise


def test_user_without_tenant_id_passes() -> None:
    """A user whose ``tenant_id`` attribute is None (e.g., a system
    service account) bypasses the same-tenant gate. The original
    Phase 4 helper had this branch; Phase 5 preserves it."""
    user = _user(tenant_id=None)
    _enforce_tenant_scope(user, requested_tenant_id=99)  # no raise

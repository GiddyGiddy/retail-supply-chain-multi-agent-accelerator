from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src import auth


def test_approval_requires_dedicated_role(monkeypatch):
    monkeypatch.setattr(auth, "settings", SimpleNamespace(auth_enabled=True))
    with pytest.raises(HTTPException) as error:
        auth.require_approval_user(auth.CurrentUser(subject="user"))
    assert error.value.status_code == 403
    user = auth.CurrentUser(subject="approver", roles=("PurchaseOrder.Approver",))
    assert auth.require_approval_user(user) == user


def test_local_unauthenticated_mode_allows_decisions(monkeypatch):
    monkeypatch.setattr(auth, "settings", SimpleNamespace(auth_enabled=False))
    user = auth.CurrentUser(subject="local-development")
    assert auth.require_approval_user(user) == user


@pytest.mark.parametrize("roles", ["PurchaseOrder.Approver", [1], None])
def test_invalid_roles_claim_is_rejected(monkeypatch, roles):
    monkeypatch.setattr(
        auth, "settings",
        SimpleNamespace(auth_enabled=True, entra_tenant_id="tenant", entra_api_client_id="api"),
    )
    monkeypatch.setattr(auth, "_jwks_client", lambda: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key="key"),
    ))
    monkeypatch.setattr(auth.jwt, "decode", lambda *args, **kwargs: {"sub": "user", "roles": roles})
    with pytest.raises(HTTPException) as error:
        auth.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"))
    assert error.value.status_code == 401


def test_decoded_roles_are_available_for_authorization(monkeypatch):
    monkeypatch.setattr(
        auth, "settings",
        SimpleNamespace(auth_enabled=True, entra_tenant_id="tenant", entra_api_client_id="api"),
    )
    monkeypatch.setattr(auth, "_jwks_client", lambda: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key="key"),
    ))
    monkeypatch.setattr(
        auth.jwt, "decode",
        lambda *args, **kwargs: {"sub": "user", "roles": ["PurchaseOrder.Approver"]},
    )
    user = auth.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"))
    assert auth.require_approval_user(user).subject == "user"

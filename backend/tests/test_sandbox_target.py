from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agents.tools.business_central import BusinessCentralConfig
from src.app import app
from src.auth import CurrentUser, require_approval_user
from src.sandbox_target import (
    ADMIN_ENVIRONMENT_URL,
    EXPECTED_COMPANY_ID,
    EXPECTED_ENVIRONMENT,
    EXPECTED_TENANT_ID,
    SandboxVerificationError,
    SandboxVerifierCredentials,
    verify_sandbox_target,
)

PURCHASING_CLIENT = "11111111-1111-4111-8111-111111111111"
VERIFIER_CLIENT = "22222222-2222-4222-8222-222222222222"


def target(**overrides):
    values = {
        "tenant_id": EXPECTED_TENANT_ID,
        "company_id": EXPECTED_COMPANY_ID,
        "environment": EXPECTED_ENVIRONMENT,
        "client_id": PURCHASING_CLIENT,
        "client_secret": "",
        "writes_enabled": False,
    }
    return BusinessCentralConfig(**{**values, **overrides})


def admin_response(**overrides):
    return {"value": [{
        "name": EXPECTED_ENVIRONMENT,
        "applicationFamily": "BusinessCentral",
        "aadTenantId": EXPECTED_TENANT_ID,
        "type": "Sandbox",
        "status": "Active",
        **overrides,
    }]}


def transport_for(body, calls):
    def handle(request):
        calls.append((request.method, str(request.url)))
        if request.method == "POST":
            assert request.url.path.endswith("/oauth2/v2.0/token")
            return httpx.Response(200, json={"access_token": "test-token"})
        assert str(request.url) == ADMIN_ENVIRONMENT_URL
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json=body)
    return httpx.MockTransport(handle)


def test_verifies_pinned_sandbox_without_enabling_writes():
    assert EXPECTED_TENANT_ID == "8d132419-f288-4bcb-b3b9-42e1ffc6d824"
    assert EXPECTED_ENVIRONMENT == "InventarMind"
    assert EXPECTED_COMPANY_ID == "6ecc6e3d-f6bb-f111-85be-7c1e52401c43"
    calls = []
    verify_sandbox_target(
        target(), SandboxVerifierCredentials(VERIFIER_CLIENT, "secret"),
        transport=transport_for(admin_response(), calls),
    )
    assert [method for method, _ in calls] == ["POST", "GET"]


@pytest.mark.parametrize("overrides", [
    {"tenant_id": "33333333-3333-4333-8333-333333333333"},
    {"company_id": "33333333-3333-4333-8333-333333333333"},
    {"environment": "Production"},
    {"environment": "inventarmind"},
])
def test_wrong_configured_target_is_rejected_without_network(overrides):
    calls = []
    with pytest.raises(SandboxVerificationError, match="target"):
        verify_sandbox_target(
            target(**overrides), SandboxVerifierCredentials(VERIFIER_CLIENT, "secret"),
            transport=transport_for(admin_response(), calls),
        )
    assert calls == []


@pytest.mark.parametrize("credentials", [
    SandboxVerifierCredentials("", ""),
    SandboxVerifierCredentials(PURCHASING_CLIENT, "secret"),
    SandboxVerifierCredentials("not-a-guid", "secret"),
])
def test_rejects_missing_invalid_or_shared_verifier(credentials):
    calls = []
    with pytest.raises(SandboxVerificationError, match="separate"):
        verify_sandbox_target(target(), credentials, transport=transport_for(admin_response(), calls))
    assert calls == []


@pytest.mark.parametrize("body", [
    admin_response(type="Production"),
    admin_response(status="NotReady"),
    admin_response(name="Other"),
    admin_response(aadTenantId="33333333-3333-4333-8333-333333333333"),
    admin_response(applicationFamily="Other"),
    {"value": []},
    {"value": [admin_response()["value"][0], admin_response()["value"][0]]},
    {"value": "invalid"},
    {},
])
def test_rejects_non_sandbox_or_unexpected_admin_response(body):
    with pytest.raises(SandboxVerificationError):
        verify_sandbox_target(
            target(), SandboxVerifierCredentials(VERIFIER_CLIENT, "secret"),
            transport=transport_for(body, []),
        )


@pytest.mark.parametrize("failure", ["token", "admin", "redirect", "invalid_json"])
def test_api_failure_is_fail_closed_and_sanitized(failure):
    def handle(request):
        if request.method == "POST":
            if failure == "token":
                return httpx.Response(401, text="secret")
            return httpx.Response(200, json={"access_token": "test-token"})
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.com"})
        if failure == "invalid_json":
            return httpx.Response(200, text="not json")
        return httpx.Response(403, text="secret")

    with pytest.raises(SandboxVerificationError) as error:
        verify_sandbox_target(
            target(), SandboxVerifierCredentials(VERIFIER_CLIENT, "secret"),
            transport=httpx.MockTransport(handle),
        )
    assert "secret" not in str(error.value)


def test_verification_endpoint_requires_auth_and_reports_failures(monkeypatch):
    app.dependency_overrides[require_approval_user] = lambda: CurrentUser("approver")
    client = TestClient(app)
    try:
        monkeypatch.setattr("src.app.settings", SimpleNamespace(auth_enabled=False))
        assert client.post("/api/sandbox/verify-target").status_code == 503
        monkeypatch.setattr("src.app.settings", SimpleNamespace(auth_enabled=True))

        def fail(*args):
            raise SandboxVerificationError("Could not verify target")

        monkeypatch.setattr("src.app.verify_sandbox_target", fail)
        response = client.post("/api/sandbox/verify-target")
        assert response.status_code == 503
        assert response.json()["detail"] == "Could not verify target"
        monkeypatch.setattr("src.app.verify_sandbox_target", lambda *args: None)
        assert client.post("/api/sandbox/verify-target").json()["status"] == "verified"
    finally:
        app.dependency_overrides.clear()


def test_verification_endpoint_rejects_non_approver(monkeypatch):
    from src.auth import get_current_user

    monkeypatch.setattr("src.auth.settings", SimpleNamespace(auth_enabled=True))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("user")
    try:
        assert TestClient(app).post("/api/sandbox/verify-target").status_code == 403
    finally:
        app.dependency_overrides.clear()

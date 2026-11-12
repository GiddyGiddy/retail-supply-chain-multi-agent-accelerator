from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.tools.business_central import BusinessCentralClient, BusinessCentralConfig, BusinessCentralError
from src import purchase_order_dispatch
from src.app import app
from src.auth import CurrentUser, get_current_user, require_approval_user
from src.database import Base, get_db
from src.models import Approval, PurchaseOrderDispatch, PurchaseOrderDispatchAttempt, PurchaseOrderDraftRecovery
from src.sandbox_proposal import build_sandbox_proposal

ORDER_ID = "ed2799d8-dede-f111-84e4-70a8a514159d"
APPROVAL_ID = "11111111-1111-4111-8111-111111111111"
STARTED = datetime(2026, 11, 12, 21, 20, 18, tzinfo=timezone.utc)
REFERENCE = f"IM-{APPROVAL_ID}"
BODY = {"erp_order_id": ORDER_ID, "attempt_started_at": STARTED.isoformat(), "confirmed_exclusive_access": True}
PAYLOAD = {
    "vendor_id": "V00010", "currency": "EUR", "po_reference": REFERENCE, "total_amount": 50.0,
    "lines": [{"sku": "1000", "quantity": 10, "unit_price": 5.0}],
}


class FakeBC:
    def __init__(self):
        self.order = {"id": ORDER_ID, "number": "106001", "status": "Draft", "vendorNumber": "V00010",
                      "currencyCode": "EUR", "totalAmountExcludingTax": 0}
        self.lines = [{"lineType": "Comment", "description": REFERENCE}]
        self.posts = 0
        self.fail_post = False
        self.next_page = False
        self.item_changes = {}
        self.methods = []

    def __call__(self, request):
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600})
        self.methods.append(request.method)
        if request.method == "POST":
            self.posts += 1
            assert request.url.path.endswith("/purchaseOrderLines")
            assert json.loads(request.read()) == {
                "lineType": "Item", "lineObjectNumber": "1000", "quantity": 10, "directUnitCost": 5.0,
            }
            if self.fail_post:
                return httpx.Response(400, json={"error": {"code": "Test", "message": "failed"}})
            self.lines.append({"lineType": "Item", "lineObjectNumber": "1000", "quantity": 10,
                               "directUnitCost": 5, "discountAmount": 0, "discountPercent": 0,
                               "amountExcludingTax": 50,
                               "locationId": "00000000-0000-0000-0000-000000000000",
                               **self.item_changes})
            self.order["totalAmountExcludingTax"] = 50
            return httpx.Response(201, json={})
        if request.url.path.endswith("/purchaseOrderLines"):
            result = {"value": self.lines}
            if self.next_page:
                result["@odata.nextLink"] = "more"
            return httpx.Response(200, json=result)
        return httpx.Response(200, json=self.order)


def adapter(fake):
    config = BusinessCentralConfig(
        tenant_id="11111111-1111-1111-1111-111111111111", company_id="22222222-2222-2222-2222-222222222222",
        client_id="client", client_secret="secret", local_currency="EUR", writes_enabled=True,
    )
    return BusinessCentralClient(config, transport=httpx.MockTransport(fake), sleep=lambda _: None)


def test_adapter_adds_only_one_line_to_known_draft_and_verifies_it():
    fake = FakeBC()
    result = adapter(fake).complete_sandbox_draft(ORDER_ID, PAYLOAD)
    assert result["po_number"] == "106001"
    assert fake.posts == 1
    assert fake.methods == ["GET", "GET", "POST", "GET", "GET"]


@pytest.mark.parametrize("change", ["wrong_id", "vendor", "status", "currency", "missing_currency",
                                   "reference", "extra_line", "total", "pagination"])
def test_adapter_never_writes_to_unverified_draft(change):
    fake = FakeBC()
    if change == "wrong_id":
        fake.order["id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    elif change == "vendor":
        fake.order["vendorNumber"] = "other"
    elif change == "status":
        fake.order["status"] = "Released"
    elif change == "currency":
        fake.order["currencyCode"] = "USD"
    elif change == "missing_currency":
        del fake.order["currencyCode"]
    elif change == "reference":
        fake.lines[0]["description"] = "different"
    elif change == "extra_line":
        fake.lines.append({"lineType": "Item"})
    elif change == "total":
        fake.order["totalAmountExcludingTax"] = 10
    else:
        fake.next_page = True
    with pytest.raises(BusinessCentralError):
        adapter(fake).complete_sandbox_draft(ORDER_ID, PAYLOAD)
    assert fake.posts == 0


@pytest.mark.parametrize("change", [
    {"lineObjectNumber": "other"}, {"quantity": 11}, {"directUnitCost": 6},
    {"discountAmount": 1}, {"discountPercent": 1}, {"locationId": "somewhere"},
    {"amountExcludingTax": 49},
])
def test_adapter_rejects_mismatched_completed_line(change):
    fake = FakeBC()
    fake.item_changes = change
    with pytest.raises(BusinessCentralError):
        adapter(fake).complete_sandbox_draft(ORDER_ID, PAYLOAD)
    assert fake.posts == 1


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_approval_user] = lambda: CurrentUser("approver")
    monkeypatch.setattr(purchase_order_dispatch, "settings", SimpleNamespace(auth_enabled=True))
    monkeypatch.setenv("BC_DISPATCH_ENABLED", "true")
    monkeypatch.setenv("BC_SANDBOX_DISPATCH_ENABLED", "true")
    config = adapter(FakeBC()).config
    monkeypatch.setattr(purchase_order_dispatch.BusinessCentralConfig, "from_env", lambda: config)
    monkeypatch.setattr(purchase_order_dispatch, "verify_sandbox_target", lambda *args: None)
    proposal = build_sandbox_proposal()
    with sessions() as db:
        db.add(Approval(id=APPROVAL_ID, title="test", summary="test", status="approved", payload=proposal))
        db.add(PurchaseOrderDispatch(approval_id=APPROVAL_ID, reference=REFERENCE, status="needs_review",
                                     approved_by="approver", approved_payload=deepcopy(proposal)))
        db.add(PurchaseOrderDispatchAttempt(approval_id=APPROVAL_ID, started_by="sender", started_at=STARTED,
                                            erp_order_id=ORDER_ID, outcome="needs_review"))
        db.commit()
    yield TestClient(app), sessions
    app.dependency_overrides.clear()
    engine.dispose()


def test_recovery_claims_and_audits_then_rejects_another_attempt(api, monkeypatch):
    client, sessions = api
    fake = FakeBC()
    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", lambda config: adapter(fake))
    response = client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "created"
    assert result["attempt"]["erp_order_number"] == "106001"
    assert result["attempt"]["outcome"] == "needs_review"
    assert result["recovery"]["outcome"] == "created"
    assert result["recovery"]["started_by"] == "approver"
    assert client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY).status_code == 409
    assert fake.posts == 1
    with sessions() as db:
        assert db.get(PurchaseOrderDraftRecovery, APPROVAL_ID).finished_at is not None
        assert len(db.scalars(select(PurchaseOrderDraftRecovery)).all()) == 1


def test_failed_line_post_locks_recovery_and_never_retries(api, monkeypatch):
    client, sessions = api
    fake = FakeBC()
    fake.fail_post = True
    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", lambda config: adapter(fake))
    response = client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY)
    assert response.status_code == 502
    assert client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY).status_code == 409
    assert fake.posts == 1
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, APPROVAL_ID).status == "needs_review"
        assert db.get(PurchaseOrderDraftRecovery, APPROVAL_ID).outcome == "needs_review"
        assert db.get(PurchaseOrderDispatchAttempt, APPROVAL_ID).erp_order_id == ORDER_ID


def test_post_write_verification_failure_locks_recovery(api, monkeypatch):
    client, sessions = api
    fake = FakeBC()
    fake.item_changes = {"quantity": 11}
    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", lambda config: adapter(fake))
    assert client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY).status_code == 502
    assert client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY).status_code == 409
    assert fake.posts == 1
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, APPROVAL_ID).status == "needs_review"
        assert db.get(PurchaseOrderDraftRecovery, APPROVAL_ID).outcome == "needs_review"


def test_failed_target_verification_does_not_claim_or_write(api, monkeypatch):
    client, sessions = api

    def fail(*args):
        raise purchase_order_dispatch.SandboxVerificationError("incorrect target")

    monkeypatch.setattr(purchase_order_dispatch, "verify_sandbox_target", fail)
    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", lambda *args: pytest.fail("unexpected write"))
    assert client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY).status_code == 503
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, APPROVAL_ID).status == "needs_review"
        assert db.get(PurchaseOrderDraftRecovery, APPROVAL_ID) is None


@pytest.mark.parametrize("flag", ["BC_DISPATCH_ENABLED", "BC_SANDBOX_DISPATCH_ENABLED"])
def test_recovery_respects_dispatch_flags(api, monkeypatch, flag):
    client, sessions = api
    monkeypatch.setenv(flag, "false")
    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", lambda *args: pytest.fail("unexpected write"))
    assert client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY).status_code == 503
    with sessions() as db:
        assert db.get(PurchaseOrderDraftRecovery, APPROVAL_ID) is None


def test_existing_line_stops_recovery_without_another_post(api, monkeypatch):
    client, sessions = api
    fake = FakeBC()
    fake.lines.append({"lineType": "Item"})
    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", lambda config: adapter(fake))
    assert client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY).status_code == 502
    assert fake.posts == 0
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, APPROVAL_ID).status == "needs_review"
        assert db.get(PurchaseOrderDraftRecovery, APPROVAL_ID).outcome == "needs_review"


def test_recovery_rechecks_claim_after_target_verification(api, monkeypatch):
    client, sessions = api

    def claim_elsewhere(*args):
        with sessions() as db:
            db.get(PurchaseOrderDispatch, APPROVAL_ID).status = "recovery_in_progress"
            db.add(PurchaseOrderDraftRecovery(approval_id=APPROVAL_ID, erp_order_id=ORDER_ID, started_by="other"))
            db.commit()

    monkeypatch.setattr(purchase_order_dispatch, "verify_sandbox_target", claim_elsewhere)
    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", lambda *args: pytest.fail("unexpected write"))
    assert client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY).status_code == 409
    with sessions() as db:
        assert db.get(PurchaseOrderDraftRecovery, APPROVAL_ID).started_by == "other"


@pytest.mark.parametrize("body,status", [
    ({**BODY, "confirmed_exclusive_access": False}, 422),
    ({**BODY, "erp_order_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}, 409),
    ({**BODY, "attempt_started_at": "2026-11-12T21:20:17Z"}, 409),
    ({**BODY, "attempt_started_at": "2026-11-12T21:20:18"}, 422),
])
def test_recovery_rejects_invalid_confirmation_and_stale_attempt(api, body, status):
    client, sessions = api
    assert client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=body).status_code == status
    with sessions() as db:
        assert db.get(PurchaseOrderDraftRecovery, APPROVAL_ID) is None


def test_recovery_requires_auth_and_approver(api, monkeypatch):
    client, sessions = api
    monkeypatch.setattr(purchase_order_dispatch, "settings", SimpleNamespace(auth_enabled=False))
    assert client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY).status_code == 503
    monkeypatch.setattr("src.auth.settings", SimpleNamespace(auth_enabled=True))
    app.dependency_overrides.pop(require_approval_user)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("not-approver")
    assert client.post(f"/api/approvals/{APPROVAL_ID}/recover-sandbox-draft", json=BODY).status_code == 403
    with sessions() as db:
        assert db.get(PurchaseOrderDraftRecovery, APPROVAL_ID) is None

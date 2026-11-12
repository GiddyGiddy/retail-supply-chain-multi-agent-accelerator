from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src import purchase_order_dispatch
from src.app import app, event_bus
from src.auth import CurrentUser, require_approval_user
from src.database import Base, get_db
from src.models import Approval, PurchaseOrderDispatch
from src.sandbox_target import EXPECTED_TENANT_ID, verify_sandbox_target


@pytest.fixture
def api(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def database():
        with sessions() as db:
            yield db

    async def publish(*args):
        pass

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_approval_user] = lambda: CurrentUser("approver-123")
    monkeypatch.setattr(event_bus, "publish", publish)
    yield TestClient(app), sessions, engine
    app.dependency_overrides.clear()
    engine.dispose()


def create_approval(sessions, strategy="VENDOR_PO"):
    with sessions() as db:
        approval = Approval(
            id="11111111-1111-4111-8111-111111111111",
            title="Proposal", summary="For review", status="pending",
            payload={"sourcing_strategy": strategy, "po_draft": {"vendor_id": "V1"}},
        )
        db.add(approval)
        db.commit()
        return approval.id


def test_approval_records_held_handoff_atomically(api):
    client, sessions, _ = api
    approval_id = create_approval(sessions)
    response = client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "approved"})
    assert response.status_code == 200
    assert response.json()["dispatch"]["status"] == "held"
    assert response.json()["dispatch"]["reference"] == f"IM-{approval_id}"
    with sessions() as db:
        dispatch = db.get(PurchaseOrderDispatch, approval_id)
        assert dispatch.approved_by == "approver-123"
        assert dispatch.approved_payload["po_draft"]["vendor_id"] == "V1"
        assert db.get(Approval, approval_id).status == "approved"
        approval = db.get(Approval, approval_id)
        approval.payload = {"sourcing_strategy": "VENDOR_PO", "po_draft": {"vendor_id": "V2"}}
        db.commit()
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, approval_id).approved_payload["po_draft"]["vendor_id"] == "V1"
    duplicate = client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "approved"})
    assert duplicate.status_code == 409
    with sessions() as db:
        assert len(db.scalars(select(PurchaseOrderDispatch)).all()) == 1
    assert client.get("/api/approvals").json()[0]["dispatch"]["status"] == "held"


@pytest.mark.parametrize("decision,strategy", [
    ("rejected", "VENDOR_PO"), ("approved", "INTERNAL_TRANSFER"), ("approved", "NONE"),
])
def test_no_handoff_for_rejection_or_non_vendor(api, decision, strategy):
    client, sessions, _ = api
    approval_id = create_approval(sessions, strategy)
    response = client.post(f"/api/approvals/{approval_id}/decision", json={"decision": decision})
    assert response.status_code == 200
    assert response.json()["dispatch"] is None
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, approval_id) is None


def test_failed_handoff_insert_rolls_back_approval(api):
    client, sessions, _ = api
    approval_id = create_approval(sessions)

    def fail_insert(mapper, connection, target):
        raise RuntimeError("simulated database failure")

    event.listen(PurchaseOrderDispatch, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="simulated database failure"):
            client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "approved"})
    finally:
        event.remove(PurchaseOrderDispatch, "before_insert", fail_insert)
    with sessions() as db:
        assert db.get(Approval, approval_id).status == "pending"
        assert db.get(PurchaseOrderDispatch, approval_id) is None


def test_sandbox_proposal_is_disabled_by_default(api, monkeypatch):
    client, sessions, _ = api
    monkeypatch.delenv("SANDBOX_PROPOSALS_ENABLED", raising=False)
    assert client.get("/api/sandbox/proposals/status").json() == {"enabled": False}
    assert client.post("/api/sandbox/proposals").status_code == 403
    with sessions() as db:
        assert db.scalars(select(Approval)).all() == []


def test_sandbox_proposal_requires_approver_when_auth_enabled(api, monkeypatch):
    from src.auth import get_current_user

    client, _, _ = api
    monkeypatch.setenv("SANDBOX_PROPOSALS_ENABLED", "true")
    monkeypatch.setattr("src.auth.settings", SimpleNamespace(auth_enabled=True))
    app.dependency_overrides.pop(require_approval_user)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("not-an-approver")
    assert client.post("/api/sandbox/proposals").status_code == 403


def test_sandbox_proposal_requires_human_approval_and_disabled_by_default(api, monkeypatch):
    client, sessions, _ = api
    monkeypatch.setenv("SANDBOX_PROPOSALS_ENABLED", "true")
    monkeypatch.setenv("BC_DISPATCH_ENABLED", "true")
    monkeypatch.delenv("BC_SANDBOX_DISPATCH_ENABLED", raising=False)
    monkeypatch.setattr(purchase_order_dispatch, "settings", SimpleNamespace(auth_enabled=True))
    monkeypatch.setattr(purchase_order_dispatch.BusinessCentralConfig, "from_env", lambda: type(
        "Config", (), {"validate": lambda self: None},
    )())

    def forbidden(*args, **kwargs):
        raise AssertionError("Sandbox proposals must not contact Business Central")

    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", forbidden)
    assert client.get("/api/sandbox/proposals/status").json() == {"enabled": True}
    response = client.post("/api/sandbox/proposals")
    assert response.status_code == 201
    approval = response.json()["approval"]
    assert approval["status"] == "pending"
    assert approval["dispatch"] is None
    assert "SANDBOX TEST" in approval["title"]
    assert "assumption" in approval["summary"]
    payload = approval["payload"]
    assert payload["sandbox_test"] is True
    assert payload["selected_vendor_id"] == "V00010"
    assert payload["sku"] == "1000"
    assert payload["reorder_quantity"] == 10
    assert payload["unit_cost"] == 5.0
    assert payload["cost_currency"] == "EUR"
    assert payload["total_estimated_cost"] == 50.0
    assert payload["po_draft"]["currency"] == "EUR"
    assert payload["po_draft"]["lines"][0]["sku"] == "1000"
    approval_id = approval["id"]
    decision = client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "approved"})
    assert decision.status_code == 200
    assert decision.json()["dispatch"]["status"] == "held"
    response = client.post(f"/api/approvals/{approval_id}/dispatch")
    assert response.status_code == 503
    assert "dispatch is disabled" in response.json()["detail"]
    with sessions() as db:
        dispatch = db.get(PurchaseOrderDispatch, approval_id)
        assert dispatch.status == "held"
        assert dispatch.approved_payload["sandbox_test"] is True
        assert dispatch.attempt is None


def prepare_sandbox_dispatch(api, monkeypatch):
    client, sessions, _ = api
    monkeypatch.setenv("SANDBOX_PROPOSALS_ENABLED", "true")
    response = client.post("/api/sandbox/proposals")
    approval_id = response.json()["approval"]["id"]
    assert client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "approved"}).status_code == 200
    monkeypatch.setenv("BC_DISPATCH_ENABLED", "true")
    monkeypatch.setenv("BC_SANDBOX_DISPATCH_ENABLED", "true")
    monkeypatch.setattr(purchase_order_dispatch, "settings", SimpleNamespace(auth_enabled=True))
    config = purchase_order_dispatch.BusinessCentralConfig(
        tenant_id="8d132419-f288-4bcb-b3b9-42e1ffc6d824",
        environment="InventarMind",
        company_id="6ecc6e3d-f6bb-f111-85be-7c1e52401c43",
        client_id="11111111-1111-4111-8111-111111111111",
        client_secret="test-secret",
        local_currency="EUR",
        writes_enabled=True,
    )
    monkeypatch.setattr(purchase_order_dispatch.BusinessCentralConfig, "from_env", lambda: config)
    return client, sessions, approval_id, config


def test_sandbox_verification_failure_leaves_handoff_held(api, monkeypatch):
    client, sessions, approval_id, _ = prepare_sandbox_dispatch(api, monkeypatch)

    def fail_verification(config, credentials):
        raise purchase_order_dispatch.SandboxVerificationError("Admin Center unavailable")

    def forbidden(*args, **kwargs):
        raise AssertionError("No client should be created before successful verification")

    monkeypatch.setattr(purchase_order_dispatch, "verify_sandbox_target", fail_verification)
    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", forbidden)
    for _ in range(2):
        response = client.post(f"/api/approvals/{approval_id}/dispatch")
        assert response.status_code == 503
        assert "no order was sent" in response.json()["detail"]
    with sessions() as db:
        dispatch = db.get(PurchaseOrderDispatch, approval_id)
        assert dispatch.status == "held"
        assert dispatch.attempt is None


@pytest.mark.parametrize("failure", ["wrong_target", "production", "inactive", "network"])
def test_sandbox_real_verifier_failure_never_claims_handoff(api, monkeypatch, failure):
    client, sessions, approval_id, config = prepare_sandbox_dispatch(api, monkeypatch)
    if failure == "wrong_target":
        from dataclasses import replace

        config = replace(config, company_id="33333333-3333-4333-8333-333333333333")
        monkeypatch.setattr(purchase_order_dispatch.BusinessCentralConfig, "from_env", lambda: config)
    credentials = purchase_order_dispatch.SandboxVerifierCredentials(
        "22222222-2222-4222-8222-222222222222", "verifier-secret",
    )
    monkeypatch.setattr(
        purchase_order_dispatch.SandboxVerifierCredentials, "from_env", lambda: credentials,
    )
    calls = []

    def handle(request):
        calls.append(request.method)
        if failure == "network":
            raise httpx.ConnectError("unavailable", request=request)
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "test-token"})
        return httpx.Response(200, json={
            "name": "InventarMind",
            "applicationFamily": "BusinessCentral",
            "aadTenantId": EXPECTED_TENANT_ID,
            "type": "Production" if failure == "production" else "Sandbox",
            "status": "NotReady" if failure == "inactive" else "Active",
        })

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(
        purchase_order_dispatch, "verify_sandbox_target",
        lambda actual_config, actual_credentials: verify_sandbox_target(
            actual_config, actual_credentials, transport=transport,
        ),
    )
    monkeypatch.setattr(
        purchase_order_dispatch, "BusinessCentralClient",
        lambda *args: pytest.fail("A failed preflight must not create a client"),
    )
    response = client.post(f"/api/approvals/{approval_id}/dispatch")
    assert response.status_code == 503
    assert calls == ([] if failure == "wrong_target" else ["POST"] if failure == "network" else ["POST", "GET"])
    with sessions() as db:
        dispatch = db.get(PurchaseOrderDispatch, approval_id)
        assert dispatch.status == "held"
        assert dispatch.attempt is None


def test_concurrent_claim_after_sandbox_verification_prevents_write(api, monkeypatch):
    client, sessions, approval_id, _ = prepare_sandbox_dispatch(api, monkeypatch)

    def another_claim(*args):
        with sessions() as db:
            db.get(PurchaseOrderDispatch, approval_id).status = "in_progress"
            db.commit()

    monkeypatch.setattr(purchase_order_dispatch, "verify_sandbox_target", another_claim)
    monkeypatch.setattr(
        purchase_order_dispatch, "BusinessCentralClient",
        lambda *args: pytest.fail("A lost claim must not create a client"),
    )
    assert client.post(f"/api/approvals/{approval_id}/dispatch").status_code == 409
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, approval_id).attempt is None


def test_sandbox_dispatch_verifies_same_snapshot_and_sends_only_once(api, monkeypatch):
    client, sessions, approval_id, config = prepare_sandbox_dispatch(api, monkeypatch)
    events = []

    def verify(actual_config, credentials):
        assert actual_config is config
        events.append("verified")
        monkeypatch.setattr(purchase_order_dispatch.BusinessCentralConfig, "from_env", lambda: None)

    class FakeClient:
        def __init__(self, actual_config):
            assert actual_config is config
            events.append("client")

        def create_draft_purchase_order(self, payload, *, on_order_created, reject_existing):
            events.append("send")
            assert payload["vendor_id"] == "V00010"
            assert payload["currency"] == "EUR"
            assert payload["lines"][0]["sku"] == "1000"
            assert payload["lines"][0]["quantity"] == 10
            assert payload["lines"][0]["unit_price"] == 5.0
            assert reject_existing is True
            on_order_created("order-id")
            return {"erp_status": "Draft", "erp_po_id": "order-id",
                    "po_number": "PO-123", "total_matches": True, "duplicate": False}

        def close(self):
            pass

    monkeypatch.setattr(purchase_order_dispatch, "verify_sandbox_target", verify)
    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", FakeClient)
    response = client.post(f"/api/approvals/{approval_id}/dispatch")
    assert response.status_code == 200
    assert events == ["verified", "client", "send"]
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, approval_id).status == "created"


@pytest.mark.parametrize("change", ["vendor", "item", "quantity", "cost", "location", "date", "marker", "malformed"])
def test_sandbox_dispatch_rejects_modified_test_facts_before_verification(api, monkeypatch, change):
    client, sessions, approval_id, _ = prepare_sandbox_dispatch(api, monkeypatch)
    with sessions() as db:
        dispatch = db.get(PurchaseOrderDispatch, approval_id)
        payload = dict(dispatch.approved_payload)
        draft = dict(payload["po_draft"])
        line = dict(draft["lines"][0])
        if change == "vendor":
            payload["selected_vendor_id"] = draft["vendor_id"] = "OTHER"
        elif change == "item":
            payload["sku"] = line["sku"] = "OTHER"
        elif change == "quantity":
            payload["reorder_quantity"] = line["quantity"] = 1
            payload["total_estimated_cost"] = 5.0
        elif change == "cost":
            payload["unit_cost"] = line["unit_price"] = 1.0
            payload["total_estimated_cost"] = 10.0
        elif change == "location":
            draft["ship_to_warehouse_id"] = "OTHER"
        elif change == "date":
            draft["requested_delivery_date"] = "2030-01-01"
        elif change == "marker":
            payload["sandbox_test"] = False
        else:
            payload["sandbox_test"] = "true"
        draft["lines"] = [line]
        payload["po_draft"] = draft
        dispatch.approved_payload = payload
        db.commit()
    monkeypatch.setattr(
        purchase_order_dispatch, "verify_sandbox_target",
        lambda *args: pytest.fail("Invalid test facts must fail before verification"),
    )
    assert client.post(f"/api/approvals/{approval_id}/dispatch").status_code == 422
    with sessions() as db:
        dispatch = db.get(PurchaseOrderDispatch, approval_id)
        assert dispatch.status == "held"
        assert dispatch.attempt is None


def prepare_dispatch(api, monkeypatch, currency="USD"):
    client, sessions, _ = api
    approval_id = create_approval(sessions)
    with sessions() as db:
        approval = db.get(Approval, approval_id)
        approval.payload = {
            "sourcing_strategy": "VENDOR_PO",
            "selected_vendor_id": "V1",
            "sku": "SKU-1",
            "reorder_quantity": 10,
            "unit_cost": 2.5,
            "total_estimated_cost": 25.0,
            "cost_currency": currency,
            "po_draft": {
                "po_reference": "original", "vendor_id": "V1", "currency": currency,
                "lines": [{"sku": "SKU-1", "quantity": 10, "unit_price": 2.5}],
            },
        }
        db.commit()
    assert client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "approved"}).status_code == 200
    monkeypatch.setenv("BC_DISPATCH_ENABLED", "true")
    monkeypatch.setattr(purchase_order_dispatch, "settings", SimpleNamespace(auth_enabled=True))
    monkeypatch.setattr(purchase_order_dispatch.BusinessCentralConfig, "from_env", lambda: type(
        "Config", (), {"validate": lambda self: None},
    )())
    return client, sessions, approval_id


def test_dispatch_disabled_by_default(api, monkeypatch):
    client, sessions, approval_id = prepare_dispatch(api, monkeypatch)
    monkeypatch.delenv("BC_DISPATCH_ENABLED")
    assert client.post(f"/api/approvals/{approval_id}/dispatch").status_code == 503
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, approval_id).status == "held"


def test_dispatch_requires_authentication(api, monkeypatch):
    client, sessions, approval_id = prepare_dispatch(api, monkeypatch)
    monkeypatch.setattr(purchase_order_dispatch, "settings", SimpleNamespace(auth_enabled=False))
    assert client.post(f"/api/approvals/{approval_id}/dispatch").status_code == 503
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, approval_id).status == "held"


def test_dispatch_requires_business_central_writes_enabled(api, monkeypatch):
    client, sessions, approval_id = prepare_dispatch(api, monkeypatch)

    def disabled():
        return purchase_order_dispatch.BusinessCentralConfig(
            tenant_id="", client_id="", client_secret="", company_id="", writes_enabled=False,
        )

    monkeypatch.setattr(purchase_order_dispatch.BusinessCentralConfig, "from_env", disabled)
    assert client.post(f"/api/approvals/{approval_id}/dispatch").status_code == 503
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, approval_id).status == "held"


def test_interrupted_dispatch_cannot_be_retried(api, monkeypatch):
    client, sessions, approval_id = prepare_dispatch(api, monkeypatch)
    with sessions() as db:
        db.get(PurchaseOrderDispatch, approval_id).status = "in_progress"
        db.commit()
    assert client.post(f"/api/approvals/{approval_id}/dispatch").status_code == 409


def test_non_approver_cannot_dispatch(api, monkeypatch):
    from src.auth import get_current_user

    client, _, approval_id = prepare_dispatch(api, monkeypatch)
    app.dependency_overrides.pop(require_approval_user)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("not-an-approver")
    monkeypatch.setattr("src.auth.settings", SimpleNamespace(auth_enabled=True))
    assert client.post(f"/api/approvals/{approval_id}/dispatch").status_code == 403


@pytest.mark.parametrize("currency", ["USD", "EUR"])
def test_dispatch_records_result_and_only_runs_once(api, monkeypatch, currency):
    client, _, approval_id = prepare_dispatch(api, monkeypatch, currency=currency)
    calls = []

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def create_draft_purchase_order(self, payload, *, on_order_created, reject_existing):
            calls.append(payload)
            assert reject_existing is True
            on_order_created("order-id")
            return {"erp_status": "Draft", "erp_po_id": "order-id",
                    "po_number": "PO-123", "total_matches": True, "duplicate": False}

        def close(self):
            pass

    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", FakeClient)
    response = client.post(f"/api/approvals/{approval_id}/dispatch")
    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert response.json()["attempt"]["erp_order_number"] == "PO-123"
    assert calls[0]["po_reference"] == f"IM-{approval_id}"
    assert calls[0]["currency"] == currency
    assert client.post(f"/api/approvals/{approval_id}/dispatch").status_code == 409
    assert len(calls) == 1
    assert client.get("/api/approvals").json()[0]["dispatch"]["attempt"]["erp_order_id"] == "order-id"


@pytest.mark.parametrize("after_header", [False, True])
def test_uncertain_failure_never_retries(api, monkeypatch, after_header):
    client, sessions, approval_id = prepare_dispatch(api, monkeypatch)
    calls = []

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def create_draft_purchase_order(self, payload, *, on_order_created, reject_existing):
            calls.append(payload)
            if after_header:
                on_order_created("partial-id")
            raise TimeoutError("simulated timeout")

        def close(self):
            pass

    monkeypatch.setattr(purchase_order_dispatch, "BusinessCentralClient", FakeClient)
    response = client.post(f"/api/approvals/{approval_id}/dispatch")
    assert response.status_code == 502
    assert client.post(f"/api/approvals/{approval_id}/dispatch").status_code == 409
    assert len(calls) == 1
    with sessions() as db:
        dispatch = db.get(PurchaseOrderDispatch, approval_id)
        assert dispatch.status == "needs_review"
        assert dispatch.attempt.outcome == "needs_review"
        assert dispatch.attempt.erp_order_id == ("partial-id" if after_header else None)


@pytest.mark.parametrize("change", ["currency", "amount", "mismatch"])
def test_invalid_or_escalated_draft_is_not_sent(api, monkeypatch, change):
    client, sessions, approval_id = prepare_dispatch(api, monkeypatch)
    with sessions() as db:
        dispatch = db.get(PurchaseOrderDispatch, approval_id)
        data = dict(dispatch.approved_payload)
        draft = dict(data["po_draft"])
        if change == "currency":
            draft["currency"] = "GBP"
        elif change == "amount":
            data["unit_cost"] = 5000.0
            data["total_estimated_cost"] = 50000.0
            draft["lines"] = [{"sku": "SKU-1", "quantity": 10, "unit_price": 5000.0}]
        else:
            data["selected_vendor_id"] = "V2"
        data["po_draft"] = draft
        dispatch.approved_payload = data
        db.commit()
    assert client.post(f"/api/approvals/{approval_id}/dispatch").status_code == 422
    with sessions() as db:
        assert db.get(PurchaseOrderDispatch, approval_id).status == "held"


@pytest.mark.parametrize("currency", ["USD", "EUR"])
@pytest.mark.parametrize("amount,allowed", [(49999.99, True), (50000.0, False), (50000.01, False)])
def test_dispatch_currency_limits(currency, amount, allowed):
    payload = {
        "sourcing_strategy": "VENDOR_PO",
        "selected_vendor_id": "V1",
        "sku": "SKU-1",
        "reorder_quantity": 1,
        "unit_cost": amount,
        "total_estimated_cost": amount,
        "cost_currency": currency,
        "po_draft": {
            "po_reference": "original", "vendor_id": "V1", "currency": currency,
            "lines": [{"sku": "SKU-1", "quantity": 1, "unit_price": amount}],
        },
    }
    dispatch = SimpleNamespace(approved_payload=payload, reference="IM-test")
    if allowed:
        assert purchase_order_dispatch._validated_draft(dispatch)["currency"] == currency
    else:
        with pytest.raises(purchase_order_dispatch.HTTPException) as error:
            purchase_order_dispatch._validated_draft(dispatch)
        assert error.value.status_code == 422


@pytest.mark.parametrize("cost_currency", [None, "USD", "GBP"])
def test_eur_draft_requires_matching_explicit_sourcing_currency(cost_currency):
    payload = {
        "sourcing_strategy": "VENDOR_PO", "selected_vendor_id": "V1",
        "sku": "SKU-1", "reorder_quantity": 1, "unit_cost": 10.0,
        "total_estimated_cost": 10.0,
        "po_draft": {
            "po_reference": "original", "vendor_id": "V1", "currency": "EUR",
            "lines": [{"sku": "SKU-1", "quantity": 1, "unit_price": 10.0}],
        },
    }
    if cost_currency is not None:
        payload["cost_currency"] = cost_currency
    with pytest.raises(purchase_order_dispatch.HTTPException) as error:
        purchase_order_dispatch._validated_draft(SimpleNamespace(approved_payload=payload, reference="IM-test"))
    assert error.value.status_code == 422

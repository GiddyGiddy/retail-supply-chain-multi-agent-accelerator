from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.app import app, event_bus
from src.auth import CurrentUser, require_approval_user
from src.database import Base, get_db
from src.models import Approval, PurchaseOrderDispatch
from src import purchase_order_dispatch


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


def prepare_dispatch(api, monkeypatch):
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
            "po_draft": {
                "po_reference": "original", "vendor_id": "V1", "currency": "USD",
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


def test_dispatch_records_result_and_only_runs_once(api, monkeypatch):
    client, sessions, approval_id = prepare_dispatch(api, monkeypatch)
    calls = []

    class FakeClient:
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
    assert client.post(f"/api/approvals/{approval_id}/dispatch").status_code == 409
    assert len(calls) == 1
    assert client.get("/api/approvals").json()[0]["dispatch"]["attempt"]["erp_order_id"] == "order-id"


@pytest.mark.parametrize("after_header", [False, True])
def test_uncertain_failure_never_retries(api, monkeypatch, after_header):
    client, sessions, approval_id = prepare_dispatch(api, monkeypatch)
    calls = []

    class FakeClient:
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
            draft["currency"] = "EUR"
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

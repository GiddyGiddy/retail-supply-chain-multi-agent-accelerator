import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.app import app, event_bus
from src.auth import CurrentUser, require_approval_user
from src.database import Base, get_db
from src.models import Approval, PurchaseOrderDispatch


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

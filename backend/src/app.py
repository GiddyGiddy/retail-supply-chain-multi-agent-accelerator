from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from agents.graph.registry import agent_catalog
from agents.graph.workflow import run_supply_chain_workflow
from src.auth import CurrentUser, get_current_user, require_approval_user
from src.config.settings import settings
from src.database import Base, engine, get_db
from src.event_bus import event_bus
from src.models import Approval, PurchaseOrderDispatch
from src.purchase_order_dispatch import dispatch_held_order, reconcile_sandbox_order
from src.sandbox_proposal import build_sandbox_proposal, sandbox_proposals_enabled
from src.sandbox_target import SandboxVerificationError, SandboxVerifierCredentials, verify_sandbox_target
from agents.tools.business_central import BusinessCentralConfig
from src.schemas import (
    AgentInfo, AgentRunRequest, AgentRunResponse, ApprovalDecision, ApprovalResponse,
    PurchaseOrderDispatchResponse, PurchaseOrderReconciliationRequest,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    await event_bus.start()
    try:
        yield
    finally:
        await event_bus.stop()


app = FastAPI(title="InventarMind API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/agents", response_model=list[AgentInfo])
def list_agents(_: Annotated[CurrentUser, Depends(get_current_user)]) -> list[dict]:
    return agent_catalog()


@app.get("/api/approvals", response_model=list[ApprovalResponse])
def list_approvals(
    _: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[Approval]:
    return list(db.scalars(
        select(Approval).options(
            selectinload(Approval.dispatch).selectinload(PurchaseOrderDispatch.attempt),
            selectinload(Approval.dispatch).selectinload(PurchaseOrderDispatch.reconciliations),
        )
        .order_by(Approval.created_at.desc())
    ).all())


@app.post("/api/approvals/{approval_id}/decision", response_model=ApprovalResponse)
async def decide_approval(
    approval_id: str,
    decision: ApprovalDecision,
    user: Annotated[CurrentUser, Depends(require_approval_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Approval:
    approval = db.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval was already decided")

    updated = db.execute(
        update(Approval)
        .where(Approval.id == approval_id, Approval.status == "pending")
        .values(status=decision.decision)
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval was already decided")

    if decision.decision == "approved" and approval.payload.get("sourcing_strategy") == "VENDOR_PO":
        db.add(PurchaseOrderDispatch(
            approval_id=approval.id,
            reference=f"IM-{approval.id}",
            status="held",
            approved_by=user.subject,
            approved_payload=approval.payload,
        ))
    db.commit()
    db.refresh(approval)
    await event_bus.publish(
        "approval-decisions",
        {"approval_id": approval.id, "decision": approval.status},
    )
    return approval


@app.post("/api/approvals/{approval_id}/dispatch", response_model=PurchaseOrderDispatchResponse)
def dispatch_approval(
    approval_id: str,
    user: Annotated[CurrentUser, Depends(require_approval_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PurchaseOrderDispatch:
    return dispatch_held_order(db, approval_id, user.subject)


@app.post("/api/approvals/{approval_id}/reconcile-no-order", response_model=PurchaseOrderDispatchResponse)
def reconcile_approval(
    approval_id: str,
    request: PurchaseOrderReconciliationRequest,
    user: Annotated[CurrentUser, Depends(require_approval_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PurchaseOrderDispatch:
    return reconcile_sandbox_order(db, approval_id, user.subject, request.evidence, request.attempt_started_at)


@app.get("/api/sandbox/proposals/status")
def sandbox_proposal_status(_: Annotated[CurrentUser, Depends(get_current_user)]) -> dict[str, bool]:
    return {"enabled": sandbox_proposals_enabled()}


@app.post("/api/sandbox/verify-target")
def verify_target(_: Annotated[CurrentUser, Depends(require_approval_user)]) -> dict[str, str]:
    if not settings.auth_enabled:
        raise HTTPException(status_code=503, detail="Sandbox verification requires authentication")
    try:
        verify_sandbox_target(BusinessCentralConfig.from_env(), SandboxVerifierCredentials.from_env())
    except SandboxVerificationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"status": "verified", "scope": "environment type and configured target"}


@app.post("/api/sandbox/proposals", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
def create_sandbox_proposal(
    _: Annotated[CurrentUser, Depends(require_approval_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AgentRunResponse:
    if not sandbox_proposals_enabled():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sandbox proposals are disabled")
    proposal = build_sandbox_proposal()
    approval = Approval(title=proposal["title"], summary=proposal["summary"], status="pending", payload=proposal)
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return AgentRunResponse(approval=approval)


@app.post("/api/agents/runs", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
def run_agent(
    _: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: AgentRunRequest | None = None,
) -> AgentRunResponse:
    proposal = run_supply_chain_workflow(request.command.strip() if request else None)
    approval = Approval(
        title=proposal["title"],
        summary=proposal["summary"],
        status="pending",
        payload=proposal,
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return AgentRunResponse(approval=approval)

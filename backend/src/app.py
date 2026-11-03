from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.graph.registry import agent_catalog
from agents.graph.workflow import run_supply_chain_workflow
from src.auth import CurrentUser, get_current_user
from src.config.settings import settings
from src.database import Base, engine, get_db
from src.event_bus import event_bus
from src.models import Approval
from src.schemas import AgentInfo, AgentRunRequest, AgentRunResponse, ApprovalDecision, ApprovalResponse


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    await event_bus.start()
    try:
        yield
    finally:
        await event_bus.stop()


app = FastAPI(title="Retail Agentic Supply Chain API", version="0.1.0", lifespan=lifespan)
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
    return list(db.scalars(select(Approval).order_by(Approval.created_at.desc())).all())


@app.post("/api/approvals/{approval_id}/decision", response_model=ApprovalResponse)
async def decide_approval(
    approval_id: str,
    decision: ApprovalDecision,
    _: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Approval:
    approval = db.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval was already decided")

    approval.status = decision.decision
    db.commit()
    db.refresh(approval)
    await event_bus.publish(
        "approval-decisions",
        {"approval_id": approval.id, "decision": approval.status},
    )
    return approval


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

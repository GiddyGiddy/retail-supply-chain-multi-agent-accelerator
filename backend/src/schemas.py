from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentRunRequest(BaseModel):
    command: str = Field(default="", max_length=2000)


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected"]


class PurchaseOrderDispatchAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    started_at: datetime
    outcome: str
    erp_order_id: str | None
    erp_order_number: str | None


class PurchaseOrderDispatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reference: str
    status: str
    approved_at: datetime
    attempt: PurchaseOrderDispatchAttemptResponse | None = None


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    summary: str
    status: str
    payload: dict
    created_at: datetime
    dispatch: PurchaseOrderDispatchResponse | None = None


class AgentInfo(BaseModel):
    id: str
    name: str
    uses_llm: bool
    llm_mode: Literal["always", "optional", "never"]
    execution_mode: Literal["llm", "deterministic", "deterministic_fallback"]
    technology: str
    responsibility: str


class AgentRunResponse(BaseModel):
    approval: ApprovalResponse

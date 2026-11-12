from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


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

    @field_serializer("started_at")
    def serialize_started_at(self, value: datetime) -> str:
        if value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()


class PurchaseOrderReconciliationRequest(BaseModel):
    confirmed_no_order: Literal[True]
    attempt_started_at: datetime
    evidence: str = Field(min_length=20, max_length=2000)


class PurchaseOrderReconciliationResponse(PurchaseOrderDispatchAttemptResponse):
    id: str
    started_by: str
    reconciled_by: str
    reconciled_at: datetime
    evidence: str


class PurchaseOrderDraftRecoveryRequest(BaseModel):
    confirmed_exclusive_access: Literal[True]
    erp_order_id: str = Field(min_length=36, max_length=36)
    attempt_started_at: datetime


class PurchaseOrderDraftRecoveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    erp_order_id: str
    started_by: str
    started_at: datetime
    finished_at: datetime | None
    outcome: str


class PurchaseOrderDispatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reference: str
    status: str
    approved_at: datetime
    attempt: PurchaseOrderDispatchAttemptResponse | None = None
    reconciliations: list[PurchaseOrderReconciliationResponse] = Field(default_factory=list)
    recovery: PurchaseOrderDraftRecoveryResponse | None = None


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

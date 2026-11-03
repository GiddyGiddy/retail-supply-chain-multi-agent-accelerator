from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentRunRequest(BaseModel):
    command: str = Field(default="", max_length=2000)


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected"]


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    summary: str
    status: str
    payload: dict
    created_at: datetime


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

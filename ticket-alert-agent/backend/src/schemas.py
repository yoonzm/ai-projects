from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field


CaseType = Literal["it_ticket", "ops_alert", "security_incident", "unknown"]
CaseStatus = Literal[
    "open",
    "waiting_user",
    "waiting_approval",
    "executing",
    "closed",
    "escalated",
    "failed",
]
ApprovalDecision = Literal["approved", "rejected", "modified"]


class Action(BaseModel):
    action_name: str
    target: str
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    approval_required: bool = False


class AgentState(TypedDict, total=False):
    case_id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[dict[str, Any]]
    user_message: str
    resume_mode: str
    case_type: CaseType
    scenario: str
    priority: str
    confidence: float
    required_fields: list[str]
    extracted_fields: dict[str, Any]
    missing_fields: list[str]
    pending_question: str
    tool_results: dict[str, Any]
    diagnosis: dict[str, Any]
    risk_level: str
    proposed_actions: list[dict[str, Any]]
    approval_status: str
    approval_comment: str
    approved_actions: list[dict[str, Any]]
    action_results: list[dict[str, Any]]
    verified: bool
    verify_notes: str
    retry_count: int
    status: CaseStatus
    final_report: str
    error: NotRequired[dict[str, Any]]
    route_reason: str
    model_config_id: str
    model_name: str


class CreateCaseRequest(BaseModel):
    message: str = Field(min_length=1)
    scenario: str = "custom"


class ContinueCaseRequest(BaseModel):
    message: str = Field(min_length=1)


class ApproveCaseRequest(BaseModel):
    decision: ApprovalDecision
    comment: str = ""
    modified_actions: list[Action] | None = None


class ModelConfigCreate(BaseModel):
    name: str
    provider: str = "openai_compatible"
    base_url: str = ""
    model_name: str
    api_key: str = ""
    temperature: float = 0.2
    timeout_seconds: int = 30


class ModelConfigUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    api_key: str | None = None
    temperature: float | None = None
    timeout_seconds: int | None = None

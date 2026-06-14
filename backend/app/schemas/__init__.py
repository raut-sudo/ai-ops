from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


Domain = Literal["sales", "inventory", "marketing", "support"]


class IntentClassification(BaseModel):
    intent_type: Literal[
        "business_diagnosis",
        "inventory_check",
        "marketing_analysis",
        "support_analysis",
        "cross_domain_analysis",
        "memory_recall",
        "direct_action",
        "reporting",
        "irrelevant",
    ]
    required_domains: list[Domain]
    memory_needed: bool
    action_only: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class MetricSnapshot(BaseModel):
    name: str
    value: float | int | str
    unit: str
    period: str
    delta_pct: float | None = None


class DomainFinding(BaseModel):
    domain: Domain
    findings: list[str]
    metrics: list[MetricSnapshot]
    anomalies: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    tool_calls_made: list[str]
    severity: Literal["low", "medium", "high", "critical"]


class PastIncident(BaseModel):
    incident_id: str
    occurred_at: datetime
    summary: str
    root_causes: list[str]
    actions_taken: list[str]
    outcome: str | None
    similarity_score: float = Field(ge=0.0, le=1.0)


class MemoryContext(BaseModel):
    past_incidents: list[PastIncident]
    recommended_actions_from_history: list[str]
    relevant_outcomes: list[str]


class RootCause(BaseModel):
    cause: str
    domain: str
    evidence: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


class SynthesisResult(BaseModel):
    correlated_explanation: str
    root_causes: list[RootCause]
    contributing_factors: dict[str, str]
    confidence_score: float = Field(ge=0.0, le=1.0)
    recommendations: list[str]
    domains_correlated: list[str]


class ReflectionResult(BaseModel):
    verdict: Literal["pass", "retry_with_domains", "fail"]
    critique: str
    confidence: float = Field(ge=0.0, le=1.0)
    domains_to_retry: list[Domain] = []
    missing_information: list[str] = []


class RestockParams(BaseModel):
    action_type: Literal["restock_product"] = "restock_product"
    sku: str
    quantity: int = Field(gt=0)


class DiscountParams(BaseModel):
    action_type: Literal["apply_discount"] = "apply_discount"
    sku: str
    percent: float = Field(gt=0, le=90)


class CampaignParams(BaseModel):
    action_type: Literal["suspend_campaign", "resume_campaign"]
    campaign_id: str


class TicketParams(BaseModel):
    action_type: Literal["create_support_ticket"] = "create_support_ticket"
    subject: str
    priority: Literal["low", "medium", "high"]


class AlertParams(BaseModel):
    action_type: Literal["send_alert"] = "send_alert"
    channel: str
    message: str


ActionParams = RestockParams | DiscountParams | CampaignParams | TicketParams | AlertParams


class ActionProposal(BaseModel):
    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target: str
    parameters: ActionParams = Field(discriminator="action_type")
    risk_level: Literal["low", "medium", "high"]
    justification: str
    estimated_impact: str

    @property
    def action_type(self) -> str:
        return self.parameters.action_type


class HITLDecision(BaseModel):
    approved_action_ids: list[str]
    rejected_action_ids: list[str] = []
    approver: str
    rejection_reason: str | None = None
    decided_at: datetime = Field(default_factory=_utcnow)


class ActionResult(BaseModel):
    action_id: str
    status: Literal["executed", "failed", "skipped"]
    result_payload: dict = {}
    error: str | None = None
    executed_at: datetime = Field(default_factory=_utcnow)


class FinalResponse(BaseModel):
    session_id: str
    query: str
    intent_type: str
    status: Literal["success", "low_confidence", "hitl_pending", "error", "irrelevant"]
    summary: str
    root_causes: list[RootCause] = []
    domain_findings: dict[str, DomainFinding] = {}
    memory_context: MemoryContext | None = None
    recommendations: list[str] = []
    proposed_actions: list[ActionProposal] = []
    executed_actions: list[ActionResult] = []
    confidence_score: float = 0.0
    low_confidence_flag: bool = False
    thread_id: str
    langsmith_run_id: str | None = None
    otel_trace_id: str | None = None
    generated_at: datetime = Field(default_factory=_utcnow)


# ── API request/response schemas (Sprint 7) ──────────────────────────────────


class ChatRequest(BaseModel):
    """POST /chat request body.

    thread_id is optional.  When provided the server reuses the existing
    checkpoint, appending the new HumanMessage so the conversation continues.
    When omitted a fresh thread_id (UUID4) is generated — starts a new chat.
    """

    query: str
    thread_id: str | None = None


class ApproveRequest(BaseModel):
    """POST /approve request body.

    thread_id lives here (not in HITLDecision — that schema is FROZEN).
    """

    thread_id: str
    decision: HITLDecision


__all__ = [
    "ActionParams",
    "ActionProposal",
    "ActionResult",
    "AlertParams",
    "ApproveRequest",
    "CampaignParams",
    "ChatRequest",
    "DiscountParams",
    "Domain",
    "DomainFinding",
    "FinalResponse",
    "HITLDecision",
    "IntentClassification",
    "MemoryContext",
    "MetricSnapshot",
    "PastIncident",
    "ReflectionResult",
    "RestockParams",
    "RootCause",
    "SynthesisResult",
    "TicketParams",
]

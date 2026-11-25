"""Strict, bounded public outputs from new collaboration calls."""
from typing import Any, Literal
from pydantic import BaseModel, Field

class Role(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    focus: str = Field(min_length=1, max_length=400)

class Specialists(BaseModel):
    roles: list[Role] = Field(default_factory=list, max_length=2)

class ProposalSection(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=12000)
    requirement_ids: list[str] = Field(default_factory=list, max_length=80)

class Reply(BaseModel):
    proposal_id: str
    position: Literal["agree", "object", "supplement"]
    reason: str = Field(min_length=1, max_length=4000)
    change: str = Field(default="", max_length=4000)

class Proposal(BaseModel):
    summary: str = Field(min_length=1, max_length=12000)
    sections: list[ProposalSection] = Field(min_length=1, max_length=12)
    replies: list[Reply] = Field(default_factory=list, max_length=20)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    risks: list[str] = Field(default_factory=list, max_length=30)
    questions: list[str] = Field(default_factory=list, max_length=5)
    disagreements: list[str] = Field(default_factory=list, max_length=30)

class Decision(BaseModel):
    proposal_id: str
    disposition: Literal["accepted", "rejected", "needs_evidence"]
    reason: str

class DecisionOption(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)

class DecisionRequest(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    question: str = Field(min_length=1, max_length=2000)
    topic_key: str = Field(default="", max_length=120)
    category: Literal["ordinary", "scope", "acceptance", "constraint"] = "scope"
    recommendation: str = Field(default="", max_length=2000)
    options: list[DecisionOption] = Field(default_factory=list, max_length=4)
    impact: str = Field(default="", max_length=2000)

class AIDecision(BaseModel):
    question: str
    choice: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    impact: str
    topic_key: str = ""

class Synthesis(BaseModel):
    summary: str = Field(min_length=1)
    spec: dict[str, Any]
    assumptions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list, max_length=5)
    disagreements: list[str] = Field(default_factory=list)
    decision_requests: list[DecisionRequest] = Field(default_factory=list, max_length=5)
    decisions: list[Decision] = Field(default_factory=list)
    ai_decisions: list[AIDecision] = Field(default_factory=list, max_length=30)

class Issue(BaseModel):
    category: Literal["ordinary", "scope", "acceptance", "constraint", "technical"] = "technical"
    id: str
    severity: Literal["blocking", "warning"]
    message: str
    requires_user: bool = False

class Review(BaseModel):
    summary: str
    passed: bool
    issues: list[Issue] = Field(default_factory=list)

OUTPUTS = {"specialists": Specialists, "proposal": Proposal, "synthesis": Synthesis, "review": Review}

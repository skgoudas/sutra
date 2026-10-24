"""Policy decision contracts (PDP input/output)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import AuthLevel

PolicyAction = Literal[
    "run.start",
    "dispatch",
    "delegate",
    "tool.invoke",
    "model.invoke",
    "context.include",
    "response.emit",
]

Effect = Literal["allow", "deny", "require_auth", "require_confirmation"]


class PrincipalClaims(BaseModel):
    """Policy-safe projection of a Principal (no raw subject)."""

    actor: str
    subject_hmac: str | None
    tenant: str | None
    auth_level: AuthLevel
    scopes: frozenset[str]
    is_anonymous: bool


class ResourceDescriptor(BaseModel):
    type: Literal["app", "unit", "tool", "model", "context"]
    id: str
    version: str | None = None
    side_effect: Literal["read", "write"] | None = None
    data_classification: str | None = None
    min_auth_level: AuthLevel = AuthLevel.ANONYMOUS
    attributes: dict[str, Any] = Field(default_factory=dict)


class PolicyInput(BaseModel):
    action: PolicyAction
    principal: PrincipalClaims
    resource: ResourceDescriptor
    context: dict[str, Any] = Field(default_factory=dict)


class Obligation(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class PolicyDecision(BaseModel):
    effect: Effect
    reason_code: str = "ok"
    rule_id: str | None = None
    obligations: list[Obligation] = Field(default_factory=list)
    policy_version: str = "builtin"

    @property
    def allowed(self) -> bool:
        return self.effect == "allow"

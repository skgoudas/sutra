"""In-process, synchronous policy decision point.

Evaluation order:
1. Built-in auth-level check (resource.min_auth_level).
2. Declarative rules, first match wins.
3. Built-in confirmation requirement for resources flagged ``requires_confirmation``.
Deny-by-default for capabilities is enforced structurally (unit allowlists for tools,
models and delegation); rules add further restrictions or obligations.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from typing import Any

from pydantic import BaseModel, Field

from sutra.contracts.policy import Effect, Obligation, PolicyAction, PolicyDecision, PolicyInput


class PolicyRule(BaseModel):
    id: str
    actions: list[PolicyAction]
    match: dict[str, Any] = Field(default_factory=dict)
    effect: Effect
    reason_code: str
    obligations: list[Obligation] = Field(default_factory=list)


class PolicyDocument(BaseModel):
    version: str
    rules: list[PolicyRule] = Field(default_factory=list)


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, Mapping):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    else:
        out[prefix] = value


def _attrs(inp: PolicyInput) -> dict[str, Any]:
    out: dict[str, Any] = {"action": inp.action}
    _flatten("principal", inp.principal.model_dump(mode="json"), out)
    _flatten("resource", inp.resource.model_dump(mode="json"), out)
    _flatten("context", inp.context, out)
    return out


def _matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, list):
        if isinstance(actual, list):  # e.g. scopes: any overlap
            return any(_matches(e, a) for e in expected for a in actual)
        return any(_matches(e, actual) for e in expected)
    if isinstance(expected, str) and isinstance(actual, str):
        return fnmatchcase(actual, expected)
    return expected == actual


class PolicyEngine:
    def __init__(self, documents: Iterable[PolicyDocument] = ()) -> None:
        docs = list(documents)
        self.rules: list[PolicyRule] = [r for d in docs for r in d.rules]
        self.version = "+".join(d.version for d in docs) or "builtin"

    @classmethod
    def from_dicts(cls, docs: Iterable[Mapping[str, Any]]) -> PolicyEngine:
        return cls(PolicyDocument.model_validate(d) for d in docs)

    def decide(self, inp: PolicyInput) -> PolicyDecision:
        if not inp.principal.auth_level.satisfies(inp.resource.min_auth_level):
            return self._d("require_auth", "auth_level_insufficient")

        attrs = _attrs(inp)
        for rule in self.rules:
            if inp.action not in rule.actions:
                continue
            if all(_matches(v, attrs.get(k)) for k, v in rule.match.items()):
                if rule.effect != "allow":
                    return self._d(rule.effect, rule.reason_code, rule.id, rule.obligations)
                decision = self._d("allow", rule.reason_code, rule.id, rule.obligations)
                break
        else:
            decision = self._d("allow", "ok")

        if inp.resource.attributes.get("requires_confirmation") and not inp.context.get("confirmed"):
            return self._d("require_confirmation", "confirmation_required", decision.rule_id,
                           decision.obligations)
        return decision

    def _d(self, effect: Effect, reason: str, rule_id: str | None = None,
           obligations: list[Obligation] | None = None) -> PolicyDecision:
        return PolicyDecision(effect=effect, reason_code=reason, rule_id=rule_id,
                              obligations=list(obligations or []), policy_version=self.version)

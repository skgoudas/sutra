"""Entrypoint dispatch: choose which unit handles a turn. Deterministic by default."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from sutra.contracts.identity import Principal
from sutra.contracts.request import AgentRequest
from sutra.contracts.result import DispatchDecision
from sutra.contracts.units import UnitDefinition
from sutra.runtime.stores import ConversationRecord


@dataclass(frozen=True)
class DispatchInput:
    request: AgentRequest
    principal: Principal
    conversation: ConversationRecord | None
    units: Mapping[str, UnitDefinition]


class DispatchStrategy(Protocol):
    name: str

    async def select(self, inp: DispatchInput) -> str | None: ...


class FixedDispatch:
    name = "fixed"

    def __init__(self, unit_id: str) -> None:
        self.unit_id = unit_id

    async def select(self, inp: DispatchInput) -> str | None:
        return self.unit_id


class StickyDispatch:
    """Route back to the unit that asked the user for input on the previous turn."""

    name = "sticky"

    async def select(self, inp: DispatchInput) -> str | None:
        unit = inp.conversation.sticky_unit if inp.conversation else None
        return unit if unit in inp.units else None


class Rule(BaseModel):
    unit: str
    pattern: str | None = None  # case-insensitive regex over the input text
    client_hint: dict[str, str | int | float | bool] = Field(default_factory=dict)


class RulesDispatch:
    """Deterministic rules. Client hints are untrusted: they select a unit, never grant access."""

    name = "rules"

    def __init__(self, rules: Sequence[Rule]) -> None:
        self.rules = list(rules)
        self._compiled = [re.compile(r.pattern, re.IGNORECASE) if r.pattern else None for r in self.rules]

    async def select(self, inp: DispatchInput) -> str | None:
        text = inp.request.input.text[:2000]
        hints = inp.request.client_hints
        for rule, rx in zip(self.rules, self._compiled, strict=True):
            if rule.client_hint and any(hints.get(k) != v for k, v in rule.client_hint.items()):
                continue
            if rx is not None and not rx.search(text):
                continue
            if rule.client_hint or rx is not None:
                return rule.unit
        return None


class Dispatcher:
    """Runs strategies in order; first non-None wins; otherwise the default unit."""

    def __init__(self, strategies: Sequence[DispatchStrategy], default_unit: str) -> None:
        self.strategies = list(strategies)
        self.default_unit = default_unit

    def unit_ids(self) -> set[str]:
        ids = {self.default_unit}
        for s in self.strategies:
            if isinstance(s, FixedDispatch):
                ids.add(s.unit_id)
            elif isinstance(s, RulesDispatch):
                ids.update(r.unit for r in s.rules)
        return ids

    async def dispatch(self, inp: DispatchInput) -> DispatchDecision:
        for strategy in self.strategies:
            unit = await strategy.select(inp)
            if unit is not None and unit in inp.units:
                return DispatchDecision(unit=unit, unit_version=inp.units[unit].version, strategy=strategy.name)
        return DispatchDecision(unit=self.default_unit, unit_version=inp.units[self.default_unit].version,
                                strategy="default")

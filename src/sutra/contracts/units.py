"""Unit (agent) definition contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .common import AuthLevel, Name, SemVer
from .context import ContextProviderRef


class ExecutionLimits(BaseModel):
    max_steps: int = 8
    max_model_calls: int = 6
    max_tool_calls: int = 10
    max_delegation_depth: int = 1
    max_input_tokens: int = 60000
    max_output_tokens: int = 4000
    max_cost_usd_micros: int = 50_000  # $0.05
    # Maximum number of calls with an identical (tool, args) signature in one run.
    max_identical_tool_calls: int = 2
    timeout_ms: int = 20000


class ContextSpec(BaseModel):
    providers: list[ContextProviderRef] = Field(default_factory=list)
    instructions: str | None = None  # prompt id resolved from the bundle
    history_turns: int = 6
    total_input_tokens: int = 12000


class UnitDefinition(BaseModel):
    id: Name
    version: SemVer = "0.1.0"
    description: str = ""
    engine: Literal["function", "adk", "remote", "a2a"] = "function"
    min_auth_level: AuthLevel = AuthLevel.ANONYMOUS
    tools: list[str] = Field(default_factory=list)  # allowlist
    models: list[str] = Field(default_factory=list)  # allowed aliases
    delegates_to: list[str] = Field(default_factory=list)
    context: ContextSpec = Field(default_factory=ContextSpec)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    owner: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

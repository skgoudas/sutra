"""Online validators, interaction records and redaction."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, Field

from sutra.contracts.result import UnitOutput, Usage, VersionStamp

if TYPE_CHECKING:
    from sutra.runtime.context import ExecutionContext

_log = logging.getLogger("sutra.eval")


# -- validators -------------------------------------------------------------------

class ValidationOutcome(BaseModel):
    validator: str
    passed: bool
    action: Literal["block", "warn"] = "block"
    reason: str | None = None


class Validator(Protocol):
    name: str

    async def validate(self, ctx: ExecutionContext, output: UnitOutput) -> ValidationOutcome: ...


class NonEmptyResponse:
    name = "non_empty_response"

    async def validate(self, ctx: ExecutionContext, output: UnitOutput) -> ValidationOutcome:
        ok = output.status != "completed" or any(m.text.strip() for m in output.messages) or bool(output.ui_blocks)
        return ValidationOutcome(validator=self.name, passed=ok, reason=None if ok else "empty")


class MaxLength:
    name = "max_length"

    def __init__(self, max_chars: int = 4000, action: Literal["block", "warn"] = "block") -> None:
        self.max_chars = max_chars
        self.action = action

    async def validate(self, ctx: ExecutionContext, output: UnitOutput) -> ValidationOutcome:
        ok = sum(len(m.text) for m in output.messages) <= self.max_chars
        return ValidationOutcome(validator=self.name, passed=ok, action=self.action,
                                 reason=None if ok else "too_long")


class RequireCitations:
    """Model-generated messages must cite at least one source (grounded answers)."""

    name = "require_citations"

    def __init__(self, action: Literal["block", "warn"] = "block") -> None:
        self.action = action

    async def validate(self, ctx: ExecutionContext, output: UnitOutput) -> ValidationOutcome:
        ok = all(m.citations for m in output.messages if m.generated_by == "model")
        return ValidationOutcome(validator=self.name, passed=ok, action=self.action,
                                 reason=None if ok else "missing_citations")


# -- redaction --------------------------------------------------------------------

class Redactor:
    """Best-effort PII scrubbing for stored interaction records. Extend per app."""

    DEFAULT_PATTERNS: dict[str, str] = {
        "EMAIL": r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
        "CARD": r"\b(?:\d[ \-]?){13,19}\b",
        "PHONE": r"(?<!\d)(?:\+?1[ .\-]?)?\(?\d{3}\)?[ .\-]?\d{3}[ .\-]?\d{4}(?!\d)",
    }

    def __init__(self, patterns: dict[str, str] | None = None) -> None:
        self._patterns = [(name, re.compile(p)) for name, p in (patterns or self.DEFAULT_PATTERNS).items()]

    def __call__(self, text: str) -> str:
        for name, pattern in self._patterns:
            text = pattern.sub(f"[{name}]", text)
        return text


# -- interaction records ----------------------------------------------------------

class EvalRecord(BaseModel):
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    app_id: str
    run_id: str
    unit: str | None
    channel: str
    locale: str
    status: str
    error_code: str | None = None
    subject_hmac: str | None = None
    request_redacted: str
    response_redacted: str
    validators: list[ValidationOutcome] = Field(default_factory=list)
    usage: Usage
    versions: VersionStamp
    latency_ms: int


class EvalSink(Protocol):
    async def emit(self, record: EvalRecord) -> None: ...


class InMemoryEvalSink:
    def __init__(self) -> None:
        self.records: list[EvalRecord] = []

    async def emit(self, record: EvalRecord) -> None:
        self.records.append(record)

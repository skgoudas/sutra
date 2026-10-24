"""Audit trail for side-effecting and denied tool calls (no argument values)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field

_log = logging.getLogger("sutra.audit")


class AuditRecord(BaseModel):
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    app_id: str
    run_id: str
    execution_id: str
    unit: str
    tool: str
    tool_version: str
    side_effect: str
    subject_hmac: str | None
    decision: str  # allow | deny | require_auth | require_confirmation
    reason_code: str
    outcome: str  # ok | <ErrorCode> | not_executed
    args_signature: str
    idempotency_key: str | None = None
    policy_version: str


class AuditSink(Protocol):
    async def write(self, record: AuditRecord) -> None: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    async def write(self, record: AuditRecord) -> None:
        self.records.append(record)


class LoggingAuditSink:
    async def write(self, record: AuditRecord) -> None:
        _log.info(record.model_dump_json())

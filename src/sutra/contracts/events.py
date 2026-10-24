"""Streaming contract. Every channel adapter (SSE, AG-UI, RPC, A2A) maps from ``RunEvent``."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RunEventType = Literal[
    "run.started",
    "progress",
    "message.delta",
    "message.completed",
    "ui.block",
    "input.required",
    "run.completed",
    "run.failed",
]

TERMINAL_EVENTS: frozenset[str] = frozenset({"run.completed", "run.failed"})


class RunEvent(BaseModel):
    seq: int
    run_id: str
    ts: datetime
    type: RunEventType
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.type in TERMINAL_EVENTS

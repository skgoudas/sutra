"""Run event sink and feature flags."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

from sutra.contracts.events import RunEvent, RunEventType
from sutra.contracts.result import UiBlock


class EventSink:
    """Ordered event stream for one run. Shared by all executions within the run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._queue: asyncio.Queue[RunEvent | None] = asyncio.Queue()
        self._seq = 0
        self.closed = False

    def emit(self, type: RunEventType, payload: dict[str, Any] | None = None) -> RunEvent:
        if self.closed:
            raise RuntimeError("event sink closed")
        self._seq += 1
        event = RunEvent(seq=self._seq, run_id=self.run_id, ts=datetime.now(UTC), type=type,
                         payload=payload or {})
        self._queue.put_nowait(event)
        return event

    def progress(self, stage: str, **attrs: Any) -> None:
        self.emit("progress", {"stage": stage, **attrs})

    def delta(self, text: str) -> None:
        self.emit("message.delta", {"text": text})

    def ui_block(self, block: UiBlock) -> None:
        self.emit("ui.block", block.model_dump(mode="json"))

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._queue.put_nowait(None)

    async def next(self) -> RunEvent | None:
        return await self._queue.get()


class FlagProvider(Protocol):
    def enabled(self, key: str) -> bool: ...


class StaticFlags:
    """Kill switches. Keys: ``app``, ``unit.<id>``, ``tool.<name>``, ``model.<alias>``."""

    def __init__(self, disabled: Iterable[str] = ()) -> None:
        self.disabled = set(disabled)

    def enabled(self, key: str) -> bool:
        return key not in self.disabled

"""State stores used by the runtime. In-memory defaults; durable backends implement the protocols."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Literal, Protocol

from sutra.contracts.result import AgentResult
from sutra.contracts.tools import ToolResult


@dataclass
class Turn:
    role: Literal["user", "assistant"]
    text: str
    run_id: str
    unit: str | None = None


@dataclass
class ConversationRecord:
    conversation_id: str
    owner_hmac: str
    sticky_unit: str | None = None  # set when a unit is awaiting user input
    turns: list[Turn] = field(default_factory=list)


class SessionStore(Protocol):
    async def get(self, conversation_id: str) -> ConversationRecord | None: ...
    async def create(self, conversation_id: str, owner_hmac: str) -> ConversationRecord: ...
    async def append(self, conversation_id: str, turns: list[Turn], sticky_unit: str | None) -> None: ...


class ResultStore(Protocol):
    async def get(self, key: str) -> AgentResult | None: ...
    async def put(self, key: str, result: AgentResult) -> None: ...


class ConversationLock(Protocol):
    async def try_acquire(self, conversation_id: str) -> bool: ...
    async def release(self, conversation_id: str) -> None: ...


class IdempotencyStore(Protocol):
    async def get(self, key: str) -> ToolResult | None: ...
    async def put(self, key: str, result: ToolResult) -> None: ...


class InMemorySessionStore:
    def __init__(self, max_turns: int = 100) -> None:
        self._data: dict[str, ConversationRecord] = {}
        self._max_turns = max_turns

    async def get(self, conversation_id: str) -> ConversationRecord | None:
        return self._data.get(conversation_id)

    async def create(self, conversation_id: str, owner_hmac: str) -> ConversationRecord:
        rec = self._data.setdefault(conversation_id, ConversationRecord(conversation_id, owner_hmac))
        return rec

    async def append(self, conversation_id: str, turns: list[Turn], sticky_unit: str | None) -> None:
        rec = self._data[conversation_id]
        rec.turns.extend(turns)
        del rec.turns[: -self._max_turns]
        rec.sticky_unit = sticky_unit


class _TtlDict[V]:
    def __init__(self, ttl_s: float) -> None:
        self._ttl = ttl_s
        self._data: dict[str, tuple[float, V]] = {}

    def get(self, key: str) -> V | None:
        item = self._data.get(key)
        if item is None:
            return None
        if item[0] < time.monotonic():
            self._data.pop(key, None)
            return None
        return item[1]

    def put(self, key: str, value: V) -> None:
        self._data[key] = (time.monotonic() + self._ttl, value)


class InMemoryResultStore:
    def __init__(self, ttl_s: float = 24 * 3600) -> None:
        self._d: _TtlDict[AgentResult] = _TtlDict(ttl_s)

    async def get(self, key: str) -> AgentResult | None:
        return self._d.get(key)

    async def put(self, key: str, result: AgentResult) -> None:
        self._d.put(key, result)


class InMemoryIdempotencyStore:
    def __init__(self, ttl_s: float = 24 * 3600) -> None:
        self._d: _TtlDict[ToolResult] = _TtlDict(ttl_s)

    async def get(self, key: str) -> ToolResult | None:
        return self._d.get(key)

    async def put(self, key: str, result: ToolResult) -> None:
        self._d.put(key, result)


class InMemoryConversationLock:
    """Single-process lock. Multi-instance deployments need a shared lock (e.g. Postgres advisory)."""

    def __init__(self) -> None:
        self._held: set[str] = set()
        self._mutex = asyncio.Lock()

    async def try_acquire(self, conversation_id: str) -> bool:
        async with self._mutex:
            if conversation_id in self._held:
                return False
            self._held.add(conversation_id)
            return True

    async def release(self, conversation_id: str) -> None:
        async with self._mutex:
            self._held.discard(conversation_id)

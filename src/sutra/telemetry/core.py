"""Telemetry facade with enforced attribute hygiene (no content in spans)."""

from __future__ import annotations

import contextvars
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import SecretStr

AttrValue = str | int | float | bool

ALLOWED_PREFIXES = ("sutra.", "gen_ai.")
ALLOWED_KEYS = frozenset({"error.type", "http.route", "http.response.status_code", "server.address"})
MAX_ATTR_LEN = 256


class UnsafeAttribute(ValueError):
    pass


def sanitize_attributes(attrs: Mapping[str, Any] | None, *, strict: bool = False) -> dict[str, AttrValue]:
    """Keep only allowlisted keys with scalar, bounded values.

    Content (prompts, user text, tool payloads) must never be passed here; the key
    allowlist plus type/length checks make accidental leakage fail closed.
    """
    out: dict[str, AttrValue] = {}
    for key, value in (attrs or {}).items():
        problem: str | None = None
        if not (key.startswith(ALLOWED_PREFIXES) or key in ALLOWED_KEYS):
            problem = f"attribute key not allowlisted: {key}"
        elif isinstance(value, SecretStr):
            problem = f"secret value for attribute: {key}"
        elif value is None:
            continue
        elif not isinstance(value, (str, int, float, bool)):
            problem = f"non-scalar value for attribute: {key}"
        elif isinstance(value, str) and len(value) > MAX_ATTR_LEN:
            problem = f"attribute value too long: {key}"
        if problem:
            if strict:
                raise UnsafeAttribute(problem)
            continue
        out[key] = value
    return out


class Span(Protocol):
    def set(self, key: str, value: AttrValue) -> None: ...
    def set_error(self, error_type: str) -> None: ...


class Telemetry(Protocol):
    def span(self, name: str, attributes: Mapping[str, Any] | None = None) -> Any: ...
    def count(self, name: str, value: int = 1, attributes: Mapping[str, Any] | None = None) -> None: ...
    def record(self, name: str, value: float, attributes: Mapping[str, Any] | None = None) -> None: ...


class _NoopSpan:
    def set(self, key: str, value: AttrValue) -> None:
        pass

    def set_error(self, error_type: str) -> None:
        pass


class NoopTelemetry:
    @contextmanager
    def span(self, name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Span]:
        yield _NoopSpan()

    def count(self, name: str, value: int = 1, attributes: Mapping[str, Any] | None = None) -> None:
        pass

    def record(self, name: str, value: float, attributes: Mapping[str, Any] | None = None) -> None:
        pass


@dataclass
class SpanRecord:
    name: str
    attributes: dict[str, AttrValue]
    parent: str | None
    span_id: int
    status: str = "ok"
    error_type: str | None = None
    duration_ms: float = 0.0

    def set(self, key: str, value: AttrValue) -> None:
        self.attributes.update(sanitize_attributes({key: value}, strict=True))

    def set_error(self, error_type: str) -> None:
        self.status = "error"
        self.error_type = error_type


@dataclass
class MetricRecord:
    name: str
    value: float
    attributes: dict[str, AttrValue] = field(default_factory=dict)


_current_span: contextvars.ContextVar[SpanRecord | None] = contextvars.ContextVar("sutra_span", default=None)


class InMemoryTelemetry:
    """Test telemetry. Strict by default: unsafe attributes raise."""

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict
        self.spans: list[SpanRecord] = []
        self.metrics: list[MetricRecord] = []
        self._next = 0

    @contextmanager
    def span(self, name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[SpanRecord]:
        parent = _current_span.get()
        self._next += 1
        rec = SpanRecord(
            name=name,
            attributes=sanitize_attributes(attributes, strict=self.strict),
            parent=parent.name if parent else None,
            span_id=self._next,
        )
        token = _current_span.set(rec)
        start = time.perf_counter()
        try:
            yield rec
        except BaseException as exc:
            if rec.status != "error":
                rec.set_error(getattr(exc, "code", type(exc).__name__))
            raise
        finally:
            rec.duration_ms = (time.perf_counter() - start) * 1000
            _current_span.reset(token)
            self.spans.append(rec)

    def count(self, name: str, value: int = 1, attributes: Mapping[str, Any] | None = None) -> None:
        self.metrics.append(MetricRecord(name, value, sanitize_attributes(attributes, strict=self.strict)))

    def record(self, name: str, value: float, attributes: Mapping[str, Any] | None = None) -> None:
        self.metrics.append(MetricRecord(name, value, sanitize_attributes(attributes, strict=self.strict)))

    def find(self, name: str) -> list[SpanRecord]:
        return [s for s in self.spans if s.name == name]

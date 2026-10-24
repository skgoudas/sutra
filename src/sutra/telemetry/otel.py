"""OpenTelemetry-backed telemetry. Requires the ``otel`` extra."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

from .core import AttrValue, sanitize_attributes


class _OTelSpan:
    def __init__(self, span: trace.Span) -> None:
        self._span = span

    def set(self, key: str, value: AttrValue) -> None:
        for k, v in sanitize_attributes({key: value}).items():
            self._span.set_attribute(k, v)

    def set_error(self, error_type: str) -> None:
        self._span.set_attribute("error.type", error_type)
        self._span.set_status(Status(StatusCode.ERROR))


class OTelTelemetry:
    """Exception messages are deliberately NOT recorded: they may contain content."""

    def __init__(self, service_name: str = "sutra") -> None:
        self._tracer = trace.get_tracer(service_name)
        self._meter = metrics.get_meter(service_name)
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}

    @contextmanager
    def span(self, name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[_OTelSpan]:
        with self._tracer.start_as_current_span(
            name,
            attributes=sanitize_attributes(attributes),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            handle = _OTelSpan(span)
            try:
                yield handle
            except BaseException as exc:
                handle.set_error(str(getattr(exc, "code", type(exc).__name__)))
                raise

    def count(self, name: str, value: int = 1, attributes: Mapping[str, Any] | None = None) -> None:
        if name not in self._counters:
            self._counters[name] = self._meter.create_counter(name)
        self._counters[name].add(value, sanitize_attributes(attributes))

    def record(self, name: str, value: float, attributes: Mapping[str, Any] | None = None) -> None:
        if name not in self._histograms:
            self._histograms[name] = self._meter.create_histogram(name)
        self._histograms[name].record(value, sanitize_attributes(attributes))

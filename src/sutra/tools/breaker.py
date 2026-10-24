"""Circuit breakers keyed by dependency (``breaker_key``)."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Literal

State = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_ratio: float = 0.5,
        min_calls: int = 20,
        window_s: float = 10.0,
        open_s: float = 15.0,
        half_open_probes: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_ratio = failure_ratio
        self.min_calls = min_calls
        self.window_s = window_s
        self.open_s = open_s
        self.half_open_probes = half_open_probes
        self._clock = clock
        self._calls: deque[tuple[float, bool]] = deque()
        self._state: State = "closed"
        self._opened_at = 0.0
        self._probes_in_flight = 0
        self._probe_successes = 0

    @property
    def state(self) -> State:
        if self._state == "open" and self._clock() - self._opened_at >= self.open_s:
            self._state = "half_open"
            self._probes_in_flight = 0
            self._probe_successes = 0
        return self._state

    def allow(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and self._probes_in_flight < self.half_open_probes:
            self._probes_in_flight += 1
            return True
        return False

    def record(self, success: bool) -> None:
        now = self._clock()
        if self._state == "half_open":
            self._probes_in_flight = max(0, self._probes_in_flight - 1)
            if not success:
                self._trip(now)
                return
            self._probe_successes += 1
            if self._probe_successes >= self.half_open_probes:
                self._state = "closed"
                self._calls.clear()
            return
        self._calls.append((now, success))
        while self._calls and now - self._calls[0][0] > self.window_s:
            self._calls.popleft()
        if len(self._calls) >= self.min_calls:
            failures = sum(1 for _, ok in self._calls if not ok)
            if failures / len(self._calls) >= self.failure_ratio:
                self._trip(now)

    def _trip(self, now: float) -> None:
        self._state = "open"
        self._opened_at = now
        self._calls.clear()


class BreakerRegistry:
    def __init__(self, factory: Callable[[], CircuitBreaker] = CircuitBreaker) -> None:
        self._factory = factory
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, key: str) -> CircuitBreaker:
        if key not in self._breakers:
            self._breakers[key] = self._factory()
        return self._breakers[key]

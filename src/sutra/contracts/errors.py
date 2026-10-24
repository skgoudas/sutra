"""Error taxonomy shared by every harness component."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    AUTH_LEVEL_INSUFFICIENT = "AUTH_LEVEL_INSUFFICIENT"
    POLICY_DENIED = "POLICY_DENIED"
    NOT_FOUND_OR_FORBIDDEN = "NOT_FOUND_OR_FORBIDDEN"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    CONFLICT = "CONFLICT"
    KILL_SWITCH = "KILL_SWITCH"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    CANCELLED = "CANCELLED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    LOOP_DETECTED = "LOOP_DETECTED"
    RATE_LIMITED = "RATE_LIMITED"
    DEPENDENCY_TIMEOUT = "DEPENDENCY_TIMEOUT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    INTERNAL = "INTERNAL"


RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.RATE_LIMITED,
        ErrorCode.DEPENDENCY_TIMEOUT,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        ErrorCode.MODEL_UNAVAILABLE,
    }
)

# Client-facing messages are fixed per code so internal details never leak to callers.
PUBLIC_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_REQUEST: "The request was not valid.",
    ErrorCode.UNAUTHENTICATED: "Please sign in to continue.",
    ErrorCode.AUTH_LEVEL_INSUFFICIENT: "Additional verification is required.",
    ErrorCode.POLICY_DENIED: "That action is not allowed.",
    ErrorCode.NOT_FOUND_OR_FORBIDDEN: "That item could not be found or is not available to you.",
    ErrorCode.CONFIRMATION_REQUIRED: "Please confirm to continue.",
    ErrorCode.CONFLICT: "Another request for this conversation is in progress.",
    ErrorCode.KILL_SWITCH: "This capability is temporarily unavailable.",
    ErrorCode.DEADLINE_EXCEEDED: "This took too long to complete.",
    ErrorCode.CANCELLED: "The request was cancelled.",
    ErrorCode.BUDGET_EXCEEDED: "This request needed more work than allowed.",
    ErrorCode.LOOP_DETECTED: "This request could not be completed.",
    ErrorCode.RATE_LIMITED: "The service is busy. Please try again shortly.",
    ErrorCode.DEPENDENCY_TIMEOUT: "A dependent service did not respond in time.",
    ErrorCode.DEPENDENCY_UNAVAILABLE: "A dependent service is unavailable.",
    ErrorCode.MODEL_UNAVAILABLE: "The assistant is temporarily unavailable.",
    ErrorCode.MODEL_OUTPUT_INVALID: "The assistant could not produce a valid answer.",
    ErrorCode.INTERNAL: "Something went wrong.",
}


class ErrorInfo(BaseModel):
    """Serializable, client-safe error description."""

    code: ErrorCode
    message: str
    retryable: bool
    reference: str | None = None


class SutraError(Exception):
    """Base exception. ``message`` is internal (logs); clients only see ``PUBLIC_MESSAGES``."""

    def __init__(
        self,
        code: ErrorCode,
        message: str = "",
        *,
        details: dict[str, Any] | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}" if message else str(code))
        self.code = code
        self.message = message
        self.details = details or {}
        self.retry_after_s = retry_after_s

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES

    def to_info(self, reference: str | None = None) -> ErrorInfo:
        return ErrorInfo(
            code=self.code,
            message=PUBLIC_MESSAGES[self.code],
            retryable=self.retryable,
            reference=reference,
        )


class BudgetExceeded(SutraError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(ErrorCode.BUDGET_EXCEEDED, message, **kw)


class LoopDetected(SutraError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(ErrorCode.LOOP_DETECTED, message, **kw)


class DeadlineExceeded(SutraError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(ErrorCode.DEADLINE_EXCEEDED, message, **kw)


class Cancelled(SutraError):
    def __init__(self, message: str = "", **kw: Any) -> None:
        super().__init__(ErrorCode.CANCELLED, message, **kw)


class PolicyDenied(SutraError):
    def __init__(self, reason_code: str, message: str = "", **kw: Any) -> None:
        super().__init__(ErrorCode.POLICY_DENIED, message or reason_code, **kw)
        self.reason_code = reason_code

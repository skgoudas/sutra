"""Versioned, provider-neutral contracts. This package has no runtime dependencies."""

from .common import AuthLevel, DataClass, new_ulid
from .context import (
    ContextItem,
    ContextKind,
    ContextPacket,
    ContextProviderRef,
    DroppedItem,
)
from .errors import (
    BudgetExceeded,
    Cancelled,
    DeadlineExceeded,
    ErrorCode,
    ErrorInfo,
    LoopDetected,
    PolicyDenied,
    SutraError,
)
from .events import RunEvent
from .identity import Principal
from .models import (
    Message,
    ModelAlias,
    ModelRequest,
    ModelResponse,
    ModelTarget,
    PriceCard,
    TextPart,
    TokenUsage,
    ToolCallPart,
    ToolResultPart,
)
from .policy import (
    Obligation,
    PolicyDecision,
    PolicyInput,
    PrincipalClaims,
    ResourceDescriptor,
)
from .request import AgentRequest, UserInput
from .result import (
    AgentResult,
    Citation,
    ConfirmationChallenge,
    DispatchDecision,
    InputRequest,
    Option,
    ResponseMessage,
    UiBlock,
    UnitOutput,
    Usage,
    VersionStamp,
)
from .tools import RetryPolicy, ToolAuth, ToolDefinition, ToolResult, ToolSpecForModel
from .units import ContextSpec, ExecutionLimits, UnitDefinition

CONTRACT_VERSION = "1.0.0"

__all__ = [
    "CONTRACT_VERSION",
    "AgentRequest",
    "AgentResult",
    "AuthLevel",
    "BudgetExceeded",
    "Cancelled",
    "Citation",
    "ConfirmationChallenge",
    "ContextItem",
    "ContextKind",
    "ContextPacket",
    "ContextProviderRef",
    "ContextSpec",
    "DataClass",
    "DeadlineExceeded",
    "DispatchDecision",
    "DroppedItem",
    "ErrorCode",
    "ErrorInfo",
    "ExecutionLimits",
    "InputRequest",
    "LoopDetected",
    "Message",
    "ModelAlias",
    "ModelRequest",
    "ModelResponse",
    "ModelTarget",
    "Obligation",
    "Option",
    "PolicyDecision",
    "PolicyDenied",
    "PolicyInput",
    "PriceCard",
    "Principal",
    "PrincipalClaims",
    "ResourceDescriptor",
    "ResponseMessage",
    "RetryPolicy",
    "RunEvent",
    "SutraError",
    "TextPart",
    "TokenUsage",
    "ToolAuth",
    "ToolCallPart",
    "ToolDefinition",
    "ToolResult",
    "ToolResultPart",
    "ToolSpecForModel",
    "UiBlock",
    "UnitDefinition",
    "UnitOutput",
    "Usage",
    "UserInput",
    "VersionStamp",
    "new_ulid",
]

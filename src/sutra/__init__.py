"""Sutra — an enterprise agent harness SDK.

Public authoring surface::

    from sutra import unit, tool, tool_loop, build_runtime, UnitOutput
"""

from sutra.contracts import (
    AgentRequest,
    AgentResult,
    AuthLevel,
    ContextItem,
    ContextKind,
    ContextProviderRef,
    ContextSpec,
    DataClass,
    ErrorCode,
    ExecutionLimits,
    InputRequest,
    Message,
    ModelAlias,
    ModelRequest,
    ModelResponse,
    ModelTarget,
    PriceCard,
    Principal,
    RunEvent,
    SutraError,
    ToolAuth,
    UnitDefinition,
    UnitOutput,
    UserInput,
)
from sutra.harness import ConfigError, build_runtime
from sutra.runtime import ExecutionContext, Runtime, RuntimeSettings, StaticFlags, Unit
from sutra.tools import ToolCallContext, tool
from sutra.units import FunctionUnit, tool_loop, unit

__version__ = "0.1.0"

__all__ = [
    "AgentRequest",
    "AgentResult",
    "AuthLevel",
    "ConfigError",
    "ContextItem",
    "ContextKind",
    "ContextProviderRef",
    "ContextSpec",
    "DataClass",
    "ErrorCode",
    "ExecutionContext",
    "ExecutionLimits",
    "FunctionUnit",
    "InputRequest",
    "Message",
    "ModelAlias",
    "ModelRequest",
    "ModelResponse",
    "ModelTarget",
    "PriceCard",
    "Principal",
    "RunEvent",
    "Runtime",
    "RuntimeSettings",
    "StaticFlags",
    "SutraError",
    "ToolAuth",
    "ToolCallContext",
    "Unit",
    "UnitDefinition",
    "UnitOutput",
    "UserInput",
    "build_runtime",
    "tool",
    "tool_loop",
    "unit",
]

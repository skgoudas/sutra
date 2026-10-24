"""The ``@tool`` decorator: build a ToolDefinition from a typed async function."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import BaseModel

from sutra.contracts.tools import ToolDefinition

from .credentials import Credential


@dataclass(frozen=True)
class ToolCallContext:
    """What a tool handler receives besides its arguments. No raw principal."""

    tool: str
    run_id: str
    execution_id: str
    attempt: int
    timeout_ms: int
    locale: str
    idempotency_key: str | None
    credential: Credential | None


def tool(
    name: str | None = None,
    *,
    description: str | None = None,
    **options: Any,
) -> Callable[[Callable[..., Any]], ToolDefinition]:
    """Decorate ``async def fn(args: ArgsModel, call: ToolCallContext) -> ResultModel``.

    ``options`` are passed to :class:`ToolDefinition` (side_effect, auth, bound_args,
    timeout_ms, retry, result_projection, requires_confirmation, ...).
    """

    def wrap(fn: Callable[..., Any]) -> ToolDefinition:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"tool {fn.__name__} must be async")
        hints = get_type_hints(fn)
        params = list(inspect.signature(fn).parameters)
        if not params:
            raise TypeError(f"tool {fn.__name__} must take an args model")
        args_model = hints.get(params[0])
        if not (isinstance(args_model, type) and issubclass(args_model, BaseModel)):
            raise TypeError(f"tool {fn.__name__}: first parameter must be a pydantic model")
        result_model = hints.get("return")
        if not (isinstance(result_model, type) and issubclass(result_model, BaseModel)):
            result_model = None
        desc = description or inspect.getdoc(fn) or ""
        if not desc:
            raise ValueError(f"tool {fn.__name__} needs a description (docstring)")

        async def handler(args: BaseModel, call: ToolCallContext) -> Any:
            if len(params) >= 2:
                return await fn(args, call)
            return await fn(args)

        return ToolDefinition(
            name=name or fn.__name__,
            description=desc,
            args_model=args_model,
            result_model=result_model,
            handler=handler,
            **options,
        )

    return wrap

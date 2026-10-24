"""Google ADK engine adapter.

ADK runs the agent loop; Sutra keeps the choke points:
* every LLM call goes through ``SutraAdkModel`` -> ``ModelClient`` (alias, budget, fallback, cost);
* every tool call goes through ``SutraAdkTool`` -> ``ToolExecutor`` (allowlist, policy, credentials);
* ``SutraGuardPlugin`` blocks any non-Sutra tool and charges a step per model turn;
* ADK sessions are keyed by (subject_hmac, conversation_id), after the runtime's ownership check.

The root may be a single agent tree or an ADK 2.0 ``Workflow`` graph. Workflow function nodes
call Sutra tools with ``call_tool`` so code-driven calls pass the same choke point.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
from collections.abc import AsyncGenerator, Iterator
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent, LoopAgent, ParallelAgent, SequentialAgent
from google.adk.apps import App
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.adk.tools.base_tool import BaseTool
from google.adk.workflow import START, BaseNode, FunctionNode, JoinNode, Workflow
from google.genai import types
from pydantic import BaseModel

from sutra.contracts.errors import ErrorCode, SutraError
from sutra.contracts.models import ModelRequest
from sutra.contracts.result import Citation, ConfirmationChallenge, InputRequest, UnitOutput
from sutra.contracts.tools import ToolDefinition, ToolResult
from sutra.contracts.units import UnitDefinition
from sutra.harness import ConfigError
from sutra.models.genai_translate import (
    from_genai_contents,
    system_text,
    to_genai_contents,
    to_genai_usage,
    tools_from_genai_config,
)
from sutra.runtime.context import ExecutionContext

_CURRENT: contextvars.ContextVar[ExecutionContext | None] = contextvars.ContextVar("sutra_adk_ctx", default=None)
_CHALLENGE = "sutra.adk.challenge"
_CITATIONS = "sutra.adk.citations"
_ABORT = "sutra.adk.abort"
_ERROR = "sutra.adk.error"


def _ctx() -> ExecutionContext:
    ctx = _CURRENT.get()
    if ctx is None:
        raise SutraError(ErrorCode.INTERNAL, "ADK component used outside a Sutra AdkUnit run")
    return ctx


@contextlib.contextmanager
def _keep_error(ctx: ExecutionContext) -> Iterator[None]:
    """ADK workflows turn node exceptions into error events; keep the Sutra error to re-raise it."""
    try:
        yield
    except SutraError as exc:
        ctx.state.setdefault(_ERROR, exc)
        raise


class SutraAdkModel(BaseLlm):
    """ADK model whose ``model`` field is a Sutra model alias."""

    @classmethod
    def supported_models(cls) -> list[str]:
        return []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        ctx = _ctx()
        config = llm_request.config
        request = ModelRequest(
            alias=self.model,
            system=system_text(config),
            messages=from_genai_contents(list(llm_request.contents or [])),
            tools=tools_from_genai_config(config),
            max_output_tokens=config.max_output_tokens if config else None,
            temperature=config.temperature if config else None,
            response_schema=_response_schema(config),
        )
        response = await _generate(ctx, request)
        content = to_genai_contents([response.message])[0]
        yield LlmResponse(
            content=types.Content(role="model", parts=content.parts or [types.Part(text="")]),
            usage_metadata=to_genai_usage(response.usage),
            turn_complete=True,
        )


async def _generate(ctx: ExecutionContext, request: ModelRequest) -> Any:
    with _keep_error(ctx):
        return await ctx.models.generate(request)


class SutraAdkTool(BaseTool):
    """Exposes a Sutra tool to ADK; execution is delegated to the ToolExecutor."""

    def __init__(self, definition: ToolDefinition) -> None:
        spec = definition.spec_for_model()
        super().__init__(name=spec.name, description=spec.description)
        self._parameters = spec.parameters
        self.sutra_name = definition.name

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(name=self.name, description=self.description,
                                         parameters_json_schema=self._parameters)

    async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> Any:
        ctx = _ctx()
        with _keep_error(ctx):
            result = await ctx.tools.invoke(self.sutra_name, args, caller="model")
        _record(ctx, result)
        return result.to_model_payload()


async def call_tool(name: str, args: dict[str, Any] | None = None) -> ToolResult:
    """Call a Sutra tool from workflow code (e.g. a function node), through the ToolExecutor.

    If the tool needs confirmation, the run stops and the user is asked; check ``result.ok``.
    """
    ctx = _ctx()
    with _keep_error(ctx):
        result = await ctx.tools.invoke(name, args or {}, caller="code")
    _record(ctx, result)
    return result


def _record(ctx: ExecutionContext, result: ToolResult) -> None:
    if result.citation is not None:
        ctx.state.setdefault(_CITATIONS, []).append(result.citation)
    if result.challenge is not None:
        ctx.state[_CHALLENGE] = result.challenge
        abort: asyncio.Event | None = ctx.state.get(_ABORT)
        if abort is not None:
            abort.set()  # stop the run; the user must confirm first


def _response_schema(config: types.GenerateContentConfig | None) -> dict[str, Any] | None:
    if config is None:
        return None
    if isinstance(config.response_json_schema, dict):
        return config.response_json_schema
    schema = config.response_schema
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_json_schema()
    if isinstance(schema, dict):
        return schema
    return None


class SutraGuardPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="sutra_guard")

    async def before_model_callback(self, *, callback_context: Any, llm_request: LlmRequest) -> LlmResponse | None:
        ctx = _ctx()
        with _keep_error(ctx):
            ctx.check()
            ctx.budget.step()
        return None

    async def before_tool_callback(self, *, tool: BaseTool, tool_args: dict[str, Any],
                                   tool_context: Any) -> dict[str, Any] | None:
        if not isinstance(tool, SutraAdkTool):
            return {"ok": False, "error": {"code": str(ErrorCode.POLICY_DENIED), "retryable": False}}
        _ctx().events.progress("tool", tool=tool.sutra_name)
        return None


def adk_tools(definitions: list[ToolDefinition]) -> list[SutraAdkTool]:
    return [SutraAdkTool(d) for d in definitions]


# Node kinds that cannot reach a model or tool except through Sutra. Anything else is rejected.
_PLAIN_NODES = (Workflow, FunctionNode, JoinNode, SequentialAgent, ParallelAgent, LoopAgent)


def _validate_tree(root: BaseNode, definition: UnitDefinition) -> None:
    for node in _walk(root):
        if isinstance(node, LlmAgent):
            if not isinstance(node.model, SutraAdkModel):
                raise ConfigError(f"ADK agent {node.name}: model must be SutraAdkModel (got {node.model!r})")
            if node.model.model not in definition.models:
                raise ConfigError(f"ADK agent {node.name}: alias {node.model.model!r} not in unit models")
            for t in node.tools:
                _check_tool(node.name, t, definition)
        elif isinstance(getattr(node, "tool", None), BaseTool):  # workflow tool node
            _check_tool(node.name, node.tool, definition)
        elif not (node is START or isinstance(node, _PLAIN_NODES) or _inner(node) is not None):
            raise ConfigError(f"ADK node {node.name}: unsupported node type {type(node).__name__}")


def _check_tool(owner: str, t: Any, definition: UnitDefinition) -> None:
    if not isinstance(t, SutraAdkTool):
        raise ConfigError(f"ADK node {owner}: tool {t!r} is not a SutraAdkTool")
    if t.sutra_name not in definition.tools:
        raise ConfigError(f"ADK node {owner}: tool {t.sutra_name} not in unit allowlist")


def _inner(node: BaseNode) -> BaseNode | None:
    inner = getattr(node, "_node", None)  # parallel-worker wrapper
    return inner if isinstance(inner, BaseNode) else None


def _walk(node: BaseNode) -> Iterator[BaseNode]:
    yield node
    if isinstance(node, Workflow):
        for child in node.graph.nodes:
            if child is not START:
                yield from _walk(child)
    if (inner := _inner(node)) is not None:
        yield from _walk(inner)
    if isinstance(node, BaseAgent):
        for sub in node.sub_agents:
            yield from _walk(sub)


def _answer(event: Any) -> str | None:
    """The user-facing answer carried by a final event: model text, or a node's output."""
    parts = event.content.parts if event.content and event.content.parts else []
    text = "".join(p.text for p in parts if p.text and not p.thought)
    if text:
        return text
    output = getattr(event, "output", None)
    if output is None:
        return None
    if isinstance(output, str):
        return output
    if isinstance(output, BaseModel):
        return output.model_dump_json()
    return json.dumps(output, default=str)


class AdkUnit:
    """A Sutra Unit backed by an ADK agent tree or an ADK 2.0 ``Workflow`` graph."""

    def __init__(self, definition: UnitDefinition, agent: BaseNode, *,
                 session_service: BaseSessionService | None = None) -> None:
        if definition.engine != "adk":
            definition = definition.model_copy(update={"engine": "adk"})
        _validate_tree(agent, definition)
        self.definition = definition
        # Workflow nodes only see the previous node's output, not the ADK session history.
        self._needs_history = not isinstance(agent, BaseAgent)
        self._app_name = definition.id.replace("-", "_").replace(".", "_")
        self._sessions = session_service or InMemorySessionService()
        self._runner = Runner(
            app=App(name=self._app_name, root_agent=agent, plugins=[SutraGuardPlugin()]),
            session_service=self._sessions,
        )

    async def run(self, ctx: ExecutionContext) -> UnitOutput:
        token = _CURRENT.set(ctx)
        abort = asyncio.Event()
        ctx.state[_ABORT] = abort
        watcher = asyncio.create_task(self._watch_cancel(ctx, abort))
        try:
            user_id = ctx.principal.subject_hmac or "anonymous"
            session_id = ctx.request.conversation_id
            if await self._sessions.get_session(app_name=self._app_name, user_id=user_id,
                                                session_id=session_id) is None:
                await self._sessions.create_session(app_name=self._app_name, user_id=user_id,
                                                    session_id=session_id)
            text = ctx.input_text
            if ctx.unit.context.providers or self._needs_history:
                packet = await ctx.build_context()
                text = packet.messages[-1].text  # fenced data + user input
                if self._needs_history and len(packet.messages) > 1:
                    transcript = "\n".join(f"{m.role}: {m.text}" for m in packet.messages[:-1])
                    text = f"Conversation so far:\n{transcript}\n\nCurrent message:\n{text}"
            message = types.Content(role="user", parts=[types.Part(text=text)])

            answer = ""
            async for event in self._runner.run_async(user_id=user_id, session_id=session_id,
                                                      new_message=message, abort_signal=abort):
                if event.error_code:
                    if abort.is_set() and event.error_code == "INVOCATION_ABORTED":
                        break  # we stopped it: confirmation pending or run cancelled
                    if (original := ctx.state.get(_ERROR)) is not None:
                        raise original
                    raise SutraError(ErrorCode.MODEL_OUTPUT_INVALID, f"adk event error {event.error_code}")
                if event.partial:
                    for p in event.content.parts if event.content and event.content.parts else []:
                        if p.text and not p.thought:
                            ctx.events.delta(p.text)
                    continue
                if (node_info := getattr(event, "node_info", None)) and node_info.path:
                    ctx.events.progress("node", node=node_info.path.rsplit("/", 1)[-1].split("@")[0])
                if event.is_final_response() and (a := _answer(event)) is not None:
                    answer = a  # each workflow node emits a final event; the last one is the answer
            ctx.check()

            challenge: ConfirmationChallenge | None = ctx.state.pop(_CHALLENGE, None)
            if challenge is not None:
                return UnitOutput.ask(InputRequest(kind="confirm", prompt_key=challenge.summary_key,
                                                   confirmation=challenge))
            citations: list[Citation] = ctx.state.pop(_CITATIONS, [])
            return UnitOutput.text(answer, generated_by="model", citations=citations)
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            ctx.state.pop(_ABORT, None)
            ctx.state.pop(_ERROR, None)
            _CURRENT.reset(token)

    @staticmethod
    async def _watch_cancel(ctx: ExecutionContext, abort: asyncio.Event) -> None:
        await ctx.cancel.wait()
        abort.set()


__all__ = ["AdkUnit", "SutraAdkModel", "SutraAdkTool", "SutraGuardPlugin", "adk_tools", "call_tool"]

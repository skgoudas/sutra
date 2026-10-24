"""Translation between Sutra model contracts and ``google.genai`` types.

Shared by the Vertex/Gemini provider and the ADK engine adapter.
"""

from __future__ import annotations

import base64
from typing import Any

from google.genai import types

from sutra.contracts.models import (
    Message,
    ModelRequest,
    TextPart,
    TokenUsage,
    ToolCallPart,
    ToolResultPart,
)
from sutra.contracts.tools import ToolSpecForModel

_CHOICE = {"auto": "AUTO", "none": "NONE", "required": "ANY"}


def to_genai_contents(messages: list[Message]) -> list[types.Content]:
    out: list[types.Content] = []
    for m in messages:
        parts: list[types.Part] = []
        for p in m.parts:
            if isinstance(p, TextPart):
                parts.append(types.Part(text=p.text))
            elif isinstance(p, ToolCallPart):
                parts.append(types.Part(
                    function_call=types.FunctionCall(id=p.id, name=p.name, args=p.args),
                    thought_signature=base64.b64decode(p.provider_signature) if p.provider_signature else None,
                ))
            elif isinstance(p, ToolResultPart):
                parts.append(types.Part(function_response=types.FunctionResponse(
                    id=p.id, name=p.name, response=p.result)))
        out.append(types.Content(role="model" if m.role == "assistant" else "user", parts=parts))
    return out


def from_genai_content(content: types.Content | None) -> Message:
    parts: list[Any] = []
    has_tool_result = False
    for p in (content.parts if content and content.parts else []):
        if p.thought:
            continue  # never surface model reasoning
        if p.function_call is not None:
            fc = p.function_call
            sig = base64.b64encode(p.thought_signature).decode() if p.thought_signature else None
            parts.append(ToolCallPart(id=fc.id, name=fc.name or "", args=dict(fc.args or {}),
                                      provider_signature=sig))
        elif p.function_response is not None:
            fr = p.function_response
            has_tool_result = True
            parts.append(ToolResultPart(id=fr.id, name=fr.name or "", result=dict(fr.response or {})))
        elif p.text:
            parts.append(TextPart(text=p.text))
    role = (content.role if content else None) or "model"
    if role == "model":
        return Message(role="assistant", parts=parts)
    return Message(role="tool" if has_tool_result else "user", parts=parts)


def from_genai_contents(contents: list[types.Content]) -> list[Message]:
    return [from_genai_content(c) for c in contents]


def to_genai_config(request: ModelRequest) -> types.GenerateContentConfig:
    kwargs: dict[str, Any] = {
        "system_instruction": request.system or None,
        "max_output_tokens": request.max_output_tokens,
        "temperature": request.temperature,
    }
    if request.tools:
        kwargs["tools"] = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=t.name, description=t.description,
                                      parameters_json_schema=t.parameters)
            for t in request.tools
        ])]
        kwargs["tool_config"] = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
            mode=_CHOICE[request.tool_choice]))
        kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
    if request.response_schema:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_json_schema"] = request.response_schema
    return types.GenerateContentConfig(**{k: v for k, v in kwargs.items() if v is not None})


def tools_from_genai_config(config: types.GenerateContentConfig | None) -> list[ToolSpecForModel]:
    specs: list[ToolSpecForModel] = []
    for tool in (config.tools if config and config.tools else []):
        for fd in getattr(tool, "function_declarations", None) or []:
            if fd.parameters_json_schema is not None:
                params = dict(fd.parameters_json_schema)
            elif fd.parameters is not None:
                params = fd.parameters.model_dump(mode="json", exclude_none=True)
            else:
                params = {"type": "object", "properties": {}}
            specs.append(ToolSpecForModel(name=fd.name or "", description=fd.description or "",
                                          parameters=params))
    return specs


def system_text(config: types.GenerateContentConfig | None) -> str | None:
    si = config.system_instruction if config else None
    if si is None:
        return None
    if isinstance(si, str):
        return si
    if isinstance(si, types.Content):
        return "".join(p.text or "" for p in si.parts or [])
    if isinstance(si, list):
        return "".join(x if isinstance(x, str) else (getattr(x, "text", "") or "") for x in si)
    return str(si)


def usage_from_genai(meta: types.GenerateContentResponseUsageMetadata | None) -> TokenUsage:
    if meta is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=meta.prompt_token_count or 0,
        cached_input_tokens=meta.cached_content_token_count or 0,
        output_tokens=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
    )


def to_genai_usage(usage: TokenUsage) -> types.GenerateContentResponseUsageMetadata:
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=usage.input_tokens,
        cached_content_token_count=usage.cached_input_tokens,
        candidates_token_count=usage.output_tokens,
        total_token_count=usage.input_tokens + usage.output_tokens,
    )

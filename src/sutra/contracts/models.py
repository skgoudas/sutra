"""Model-call contracts. Provider-neutral; adapters translate to vendor SDKs."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from .tools import ToolSpecForModel


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolCallPart(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str | None = None
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    # Opaque provider token that must be echoed back with the call (e.g. Gemini 3 thought signature, base64).
    provider_signature: str | None = None


class ToolResultPart(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    id: str | None = None
    name: str
    result: dict[str, Any] = Field(default_factory=dict)


Part = Annotated[TextPart | ToolCallPart | ToolResultPart, Field(discriminator="type")]


class Message(BaseModel):
    role: Literal["user", "assistant", "tool"]
    parts: list[Part]

    @classmethod
    def user(cls, text: str) -> Message:
        return cls(role="user", parts=[TextPart(text=text)])

    @classmethod
    def assistant(cls, text: str) -> Message:
        return cls(role="assistant", parts=[TextPart(text=text)])

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        return [p for p in self.parts if isinstance(p, ToolCallPart)]


class TokenUsage(BaseModel):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0


class PriceCard(BaseModel):
    """USD per million tokens. Cost in micro-USD = tokens * price."""

    input_per_mtok_usd: float = 0.0
    cached_input_per_mtok_usd: float = 0.0
    output_per_mtok_usd: float = 0.0

    def cost_micros(self, usage: TokenUsage) -> int:
        uncached = max(0, usage.input_tokens - usage.cached_input_tokens)
        return int(
            uncached * self.input_per_mtok_usd
            + usage.cached_input_tokens * self.cached_input_per_mtok_usd
            + usage.output_tokens * self.output_per_mtok_usd
        )


class ModelTarget(BaseModel):
    provider: str  # registered provider name, e.g. "vertex"
    model: str  # provider model id
    region: str | None = None
    timeout_ms: int = 15000
    max_input_tokens: int = 128000
    supports_tools: bool = True
    supports_json_schema: bool = True
    price: PriceCard = Field(default_factory=PriceCard)

    @property
    def id(self) -> str:
        return f"{self.provider}/{self.model}"


class ModelAlias(BaseModel):
    alias: str
    targets: list[ModelTarget]  # primary first, then fallbacks
    max_output_tokens: int = 1024


class ModelRequest(BaseModel):
    alias: str
    system: str | None = None
    messages: list[Message]
    tools: list[ToolSpecForModel] = Field(default_factory=list)
    tool_choice: Literal["auto", "none", "required"] = "auto"
    response_schema: dict[str, Any] | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    prompt_versions: list[str] = Field(default_factory=list)
    provider_options: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    message: Message
    finish_reason: str = "stop"
    usage: TokenUsage = Field(default_factory=TokenUsage)
    target: str = ""  # resolved provider/model
    latency_ms: int = 0

    @property
    def text(self) -> str:
        return self.message.text

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        return self.message.tool_calls

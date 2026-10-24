"""Gemini / Vertex AI provider via ``google-genai``. Requires the ``vertex`` extra."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from google import genai
from google.auth import exceptions as auth_errors
from google.genai import errors as genai_errors

from sutra.contracts.errors import ErrorCode, SutraError
from sutra.contracts.models import ModelRequest, ModelResponse, ModelTarget

from .genai_translate import from_genai_content, to_genai_config, to_genai_contents, usage_from_genai

log = logging.getLogger("sutra.models.google_genai")


class GoogleGenAIProvider:
    """One ``genai.Client`` per region. Auth uses Application Default Credentials."""

    def __init__(self, *, project: str | None = None, default_location: str = "us-central1",
                 vertexai: bool = True, client_factory: Any = None) -> None:
        self._project = project
        self._default_location = default_location
        self._vertexai = vertexai
        self._factory = client_factory or (
            lambda location: genai.Client(vertexai=vertexai, project=project, location=location)
        )
        self._clients: dict[str, Any] = {}

    def _client(self, location: str) -> Any:
        if location not in self._clients:
            self._clients[location] = self._factory(location)
        return self._clients[location]

    async def generate(self, target: ModelTarget, request: ModelRequest) -> ModelResponse:
        try:
            client = self._client(target.region or self._default_location)
            resp = await client.aio.models.generate_content(
                model=target.model,
                contents=to_genai_contents(request.messages),
                config=to_genai_config(request),
            )
        except genai_errors.APIError as exc:
            if exc.code in (401, 403):
                log.warning("Vertex AI rejected credentials for %s (HTTP %s)", target.id, exc.code)
            raise SutraError(_map_status(exc.code), f"genai {exc.code}") from None
        except auth_errors.GoogleAuthError as exc:
            # e.g. RefreshError (expired ADC: run `gcloud auth application-default login`)
            # or DefaultCredentialsError (no ADC configured).
            log.warning("Google credentials unavailable for %s: %s: %s", target.id, type(exc).__name__, exc)
            raise SutraError(ErrorCode.MODEL_UNAVAILABLE, "model credentials unavailable") from None
        except httpx.TransportError as exc:
            log.warning("Network error calling %s: %s", target.id, type(exc).__name__)
            raise SutraError(ErrorCode.MODEL_UNAVAILABLE, "model unreachable") from None
        if not resp.candidates:
            raise SutraError(ErrorCode.MODEL_OUTPUT_INVALID, "no candidates (blocked or empty)")
        candidate = resp.candidates[0]
        finish = str(candidate.finish_reason.value if candidate.finish_reason else "STOP").lower()
        return ModelResponse(
            message=from_genai_content(candidate.content),
            finish_reason=finish,
            usage=usage_from_genai(resp.usage_metadata),
        )


def _map_status(code: int | None) -> ErrorCode:
    if code == 429:
        return ErrorCode.RATE_LIMITED
    if code is not None and code >= 500:
        return ErrorCode.MODEL_UNAVAILABLE
    if code in (401, 403):
        return ErrorCode.MODEL_UNAVAILABLE
    if code == 408:
        return ErrorCode.DEPENDENCY_TIMEOUT
    return ErrorCode.MODEL_OUTPUT_INVALID

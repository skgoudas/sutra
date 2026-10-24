from __future__ import annotations

import pytest
from google.auth.exceptions import RefreshError

from sutra import ErrorCode, ModelRequest, ModelTarget, SutraError
from sutra.contracts.models import Message
from sutra.models.google_genai import GoogleGenAIProvider


class _ExpiredCredsClient:
    class aio:
        class models:
            @staticmethod
            async def generate_content(**kw):
                raise RefreshError("Reauthentication is needed.")


async def test_expired_credentials_map_to_model_unavailable(caplog):
    provider = GoogleGenAIProvider(project="p", client_factory=lambda loc: _ExpiredCredsClient())
    target = ModelTarget(provider="vertex", model="gemini-2.5-flash")
    with pytest.raises(SutraError) as exc:
        await provider.generate(target, ModelRequest(alias="a", messages=[Message.user("hi")]))
    assert exc.value.code == ErrorCode.MODEL_UNAVAILABLE and exc.value.retryable
    assert "RefreshError" in caplog.text


def test_thought_signature_round_trips():
    from google.genai import types

    from sutra.models.genai_translate import from_genai_content, to_genai_contents

    sig = b"\x00opaque\xff"
    content = types.Content(role="model", parts=[types.Part(
        function_call=types.FunctionCall(id="c1", name="kb__search", args={"q": "x"}), thought_signature=sig)])
    msg = from_genai_content(content)
    back = to_genai_contents([Message.model_validate_json(msg.model_dump_json())])
    assert back[0].parts[0].thought_signature == sig

from __future__ import annotations

import os
import time

import jwt
import pytest
from pydantic import SecretStr

from sutra import AuthLevel, SutraError
from sutra.identity import JwtAssertionVerifier, SubjectHasher
from sutra.telemetry import InMemoryTelemetry, UnsafeAttribute, sanitize_attributes

KEY = os.urandom(32)


def _verifier():
    return JwtAssertionVerifier(audience="app", issuer="frontdoor", algorithms=["HS256"], key=KEY,
                                hasher=SubjectHasher(KEY))


def _token(**overrides):
    now = int(time.time())
    claims = {"sub": "u-42", "aud": "app", "iss": "frontdoor", "iat": now, "exp": now + 60,
              "auth_level": "authenticated", "scope": "orders:read"} | overrides
    return jwt.encode(claims, KEY, algorithm="HS256")


def test_valid_assertion_maps_to_principal():
    p = _verifier().verify(_token())
    assert p.auth_level == AuthLevel.AUTHENTICATED
    assert p.subject.get_secret_value() == "u-42"
    assert p.scopes == {"orders:read"}
    assert "u-42" not in repr(p)
    assert p.subject_hmac == SubjectHasher(KEY)("u-42")


def test_anonymous_assertion_has_no_subject():
    p = _verifier().verify(_token(auth_level="anonymous", sub="guest-session-1"))
    assert p.is_anonymous and p.assertion is None and p.subject_hmac


@pytest.mark.parametrize("token", [
    lambda: _token(aud="other"),
    lambda: _token(iss="evil"),
    lambda: _token(exp=int(time.time()) - 3600),
    lambda: _token(auth_level="godmode"),
    lambda: jwt.encode({"sub": "x", "aud": "app", "iss": "frontdoor", "iat": 0, "exp": 9999999999},
                       os.urandom(32), algorithm="HS256"),
    lambda: jwt.encode({"sub": "x", "aud": "app", "iss": "frontdoor", "iat": 0, "exp": 9999999999},
                       None, algorithm="none"),
])
def test_invalid_assertions_rejected(token):
    with pytest.raises(SutraError) as exc:
        _verifier().verify(token())
    assert exc.value.code == "UNAUTHENTICATED"


def test_verifier_refuses_alg_none_configuration():
    with pytest.raises(ValueError):
        JwtAssertionVerifier(audience="a", issuer="i", algorithms=["none"], key=KEY, hasher=SubjectHasher(KEY))


def test_attribute_allowlist():
    assert sanitize_attributes({"sutra.unit.id": "x", "user.email": "a@b.c", "gen_ai.request.model": "m"}) == {
        "sutra.unit.id": "x", "gen_ai.request.model": "m"}
    with pytest.raises(UnsafeAttribute):
        sanitize_attributes({"sutra.prompt": "x" * 1000}, strict=True)
    with pytest.raises(UnsafeAttribute):
        sanitize_attributes({"sutra.subject": SecretStr("s")}, strict=True)
    with pytest.raises(UnsafeAttribute):
        with InMemoryTelemetry().span("s", {"input.text": "hello"}):
            pass

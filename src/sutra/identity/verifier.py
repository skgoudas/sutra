"""Principal assertion verification. Identity is never taken from request bodies."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import SecretStr

from sutra.contracts.common import AuthLevel
from sutra.contracts.errors import ErrorCode, SutraError
from sutra.contracts.identity import Principal


class SubjectHasher:
    """Keyed hash of subjects: stable pseudonymous ids for keys, logs and scoping."""

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("subject hash key must be at least 32 bytes")
        self._key = key

    def __call__(self, subject: str) -> str:
        return hmac.new(self._key, subject.encode(), hashlib.sha256).hexdigest()[:32]


class AssertionVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class JwtAssertionVerifier:
    """Verifies a signed principal assertion (JWT) issued by the trusted front door.

    * Algorithms are pinned (no ``none``, no algorithm confusion).
    * ``aud``, ``iss``, ``exp``, ``iat`` and ``sub`` are required.
    * Use ``jwks_url`` for asymmetric keys in production; ``key`` (HS256 secret or PEM)
      is supported for development and tests.
    Requires the ``jwt`` extra (PyJWT).
    """

    def __init__(
        self,
        *,
        audience: str,
        issuer: str,
        hasher: SubjectHasher,
        algorithms: Sequence[str] = ("RS256", "ES256"),
        key: str | bytes | None = None,
        jwks_url: str | None = None,
        leeway_s: int = 30,
        auth_level_claim: str = "auth_level",
        tenant_claim: str = "tenant",
        actor_claim: str = "actor",
    ) -> None:
        import jwt  # noqa: PLC0415 - optional dependency

        if not algorithms or any(a.lower() == "none" for a in algorithms):
            raise ValueError("algorithms must be pinned and must not include 'none'")
        if (key is None) == (jwks_url is None):
            raise ValueError("provide exactly one of key or jwks_url")
        self._jwt = jwt
        self._audience = audience
        self._issuer = issuer
        self._hasher = hasher
        self._algorithms = list(algorithms)
        self._key = key
        self._jwks = jwt.PyJWKClient(jwks_url, cache_keys=True) if jwks_url else None
        self._leeway = leeway_s
        self._auth_level_claim = auth_level_claim
        self._tenant_claim = tenant_claim
        self._actor_claim = actor_claim

    def verify(self, token: str) -> Principal:
        jwt = self._jwt
        try:
            key: Any = self._jwks.get_signing_key_from_jwt(token).key if self._jwks else self._key
            claims = jwt.decode(
                token,
                key=key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise SutraError(ErrorCode.UNAUTHENTICATED, type(exc).__name__) from None
        return self._to_principal(claims, token)

    def _to_principal(self, claims: Mapping[str, Any], token: str) -> Principal:
        try:
            level = AuthLevel(claims.get(self._auth_level_claim, AuthLevel.ANONYMOUS))
        except ValueError:
            raise SutraError(ErrorCode.UNAUTHENTICATED, "unknown auth level") from None
        actor = claims.get(self._actor_claim, "end_user")
        if actor not in ("end_user", "agent", "service"):
            raise SutraError(ErrorCode.UNAUTHENTICATED, "unknown actor")
        sub = str(claims["sub"])
        scope = claims.get("scope", "")
        scopes = frozenset(scope.split()) if isinstance(scope, str) else frozenset(map(str, scope))
        anonymous = level == AuthLevel.ANONYMOUS
        return Principal(
            actor=actor,
            subject=None if anonymous else SecretStr(sub),
            subject_hmac=self._hasher(("anon:" if anonymous else "") + sub),
            tenant=claims.get(self._tenant_claim),
            issuer=claims["iss"],
            auth_level=level,
            scopes=scopes,
            expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC),
            assertion=None if anonymous else SecretStr(token),
        )

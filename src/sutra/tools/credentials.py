"""Credential brokering. The ToolExecutor is the only component that obtains credentials."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from sutra.contracts.errors import ErrorCode, SutraError
from sutra.contracts.identity import Principal
from sutra.contracts.tools import ToolAuth


@dataclass(frozen=True)
class Credential:
    token: SecretStr
    kind: str  # "bearer" | "api_key"
    expires_at: float  # time.time() epoch seconds

    def __repr__(self) -> str:
        return f"Credential(kind={self.kind!r}, token=***)"


class TokenExchanger(Protocol):
    """RFC 8693 token exchange: user assertion -> audience-scoped, short-lived token."""

    async def exchange(self, subject_token: SecretStr, audience: str, scopes: tuple[str, ...]) -> Credential: ...


class ServiceTokenSource(Protocol):
    """Workload identity token for an audience (e.g. a Google ID token)."""

    async def token(self, audience: str) -> Credential: ...


class SecretSource(Protocol):
    async def secret(self, name: str) -> SecretStr: ...


class CredentialBroker:
    def __init__(
        self,
        *,
        exchanger: TokenExchanger | None = None,
        service_tokens: ServiceTokenSource | None = None,
        secrets: SecretSource | None = None,
        refresh_margin_s: float = 30.0,
    ) -> None:
        self._exchanger = exchanger
        self._service = service_tokens
        self._secrets = secrets
        self._margin = refresh_margin_s
        self._cache: dict[tuple[str, str, tuple[str, ...]], Credential] = {}
        self._lock = asyncio.Lock()

    def supports(self, auth: ToolAuth) -> bool:
        return {
            "none": True,
            "delegated": self._exchanger is not None,
            "service": self._service is not None,
            "api_key": self._secrets is not None,
        }[auth.mode]

    async def get(self, principal: Principal, auth: ToolAuth) -> Credential | None:
        if auth.mode == "none":
            return None
        if not auth.audience:
            raise SutraError(ErrorCode.INTERNAL, "tool auth requires an audience")
        if auth.mode == "service":
            assert self._service is not None
            return await self._service.token(auth.audience)
        if auth.mode == "api_key":
            assert self._secrets is not None
            return Credential(await self._secrets.secret(auth.audience), "api_key", float("inf"))
        # delegated
        assert self._exchanger is not None
        if principal.assertion is None or principal.subject_hmac is None:
            raise SutraError(ErrorCode.AUTH_LEVEL_INSUFFICIENT, "delegated auth needs a user principal")
        key = (principal.subject_hmac, auth.audience, tuple(sorted(auth.scopes)))
        async with self._lock:
            cached = self._cache.get(key)
            if cached and cached.expires_at - self._margin > time.time():
                return cached
            cred = await self._exchanger.exchange(principal.assertion, auth.audience, key[2])
            self._cache[key] = cred
            return cred

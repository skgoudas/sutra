"""Identity contract. A ``Principal`` is only ever built from a verified assertion."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .common import AuthLevel


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor: Literal["end_user", "agent", "service"] = "end_user"
    subject: SecretStr | None = None  # raw subject; never logged or sent to models
    subject_hmac: str | None = None  # stable pseudonymous id for keys, logs, scoping
    tenant: str | None = None
    issuer: str | None = None
    auth_level: AuthLevel = AuthLevel.ANONYMOUS
    scopes: frozenset[str] = frozenset()
    expires_at: datetime | None = None
    # Raw verified assertion, kept only for delegated token exchange.
    assertion: SecretStr | None = Field(default=None, repr=False)

    @property
    def is_anonymous(self) -> bool:
        return self.subject is None

    @classmethod
    def anonymous(cls, session_hmac: str | None = None) -> Principal:
        return cls(subject_hmac=session_hmac)

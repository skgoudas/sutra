"""Shared primitive types used across all contracts."""

from __future__ import annotations

import os
import time
from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Identifiers supplied by clients are constrained to a conservative charset so they
# are safe to use as keys, log fields (after hashing where needed) and span attributes.
SafeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_\-:.]{1,128}$")]
LocaleTag = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")]
SemVer = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.\-]+)?$")]
Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_\-.]{0,79}$")]


def new_ulid() -> str:
    """Return a 26-char, time-sortable, Crockford base32 identifier (ULID layout)."""
    value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80 | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


class AuthLevel(StrEnum):
    """Ordered authentication ladder. Compare with :meth:`satisfies`."""

    ANONYMOUS = "anonymous"
    VERIFIED = "verified"
    AUTHENTICATED = "authenticated"
    STEP_UP = "step_up"

    @property
    def rank(self) -> int:
        return _AUTH_RANK[self]

    def satisfies(self, required: AuthLevel) -> bool:
        return self.rank >= required.rank


_AUTH_RANK = {
    AuthLevel.ANONYMOUS: 0,
    AuthLevel.VERIFIED: 1,
    AuthLevel.AUTHENTICATED: 2,
    AuthLevel.STEP_UP: 3,
}


class DataClass(StrEnum):
    """Data classification of a tool result or context item."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"  # e.g. user-owned personal data
    RESTRICTED = "restricted"  # e.g. payment, regulated data

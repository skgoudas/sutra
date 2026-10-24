"""Confirmation tokens for side-effecting tools: HMAC-bound, expiring, single use."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import UTC, datetime

from sutra.contracts.result import ConfirmationChallenge
from sutra.runtime.primitives import call_signature


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class ConfirmationService:
    """Binds a token to (principal, tool, canonical args, expiry).

    Single-use tracking is in-process; multi-instance deployments should back
    ``_used`` with a shared store.
    """

    def __init__(self, secret: bytes, ttl_s: int = 300) -> None:
        if len(secret) < 32:
            raise ValueError("confirmation secret must be at least 32 bytes")
        self._secret = secret
        self._ttl = ttl_s
        self._used: dict[str, float] = {}

    def _mac(self, principal_hmac: str, tool: str, sig: str, exp: int, nonce: str) -> str:
        msg = f"{principal_hmac}|{tool}|{sig}|{exp}|{nonce}".encode()
        return _b64(hmac.new(self._secret, msg, hashlib.sha256).digest())

    def issue(self, principal_hmac: str, tool: str, args: dict) -> ConfirmationChallenge:
        exp = int(time.time()) + self._ttl
        nonce = _b64(os.urandom(12))
        body = _b64(json.dumps({"exp": exp, "n": nonce}).encode())
        mac = self._mac(principal_hmac, tool, call_signature(tool, args), exp, nonce)
        return ConfirmationChallenge(
            token=f"{body}.{mac}",
            tool=tool,
            summary_key=f"confirm.{tool}",
            expires_at=datetime.fromtimestamp(exp, UTC),
        )

    def consume(self, tokens: tuple[str, ...], principal_hmac: str, tool: str, args: dict) -> bool:
        """Return True and burn the token if any token matches this exact call."""
        now = time.time()
        self._used = {t: e for t, e in self._used.items() if e > now}
        sig = call_signature(tool, args)
        for token in tokens:
            try:
                body, mac = token.split(".", 1)
                payload = json.loads(_unb64(body))
                exp, nonce = int(payload["exp"]), str(payload["n"])
            except (ValueError, KeyError, TypeError):
                continue
            if exp < now or token in self._used:
                continue
            if hmac.compare_digest(mac, self._mac(principal_hmac, tool, sig, exp, nonce)):
                self._used[token] = exp
                return True
        return False

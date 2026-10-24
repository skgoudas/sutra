"""Serve the IT help desk workflow as a Sutra HTTP API.

Environment variables:
- GOOGLE_CLOUD_PROJECT: Vertex AI project id.
- SUTRA_CONFIRMATION_SECRET: 32+ byte secret used for confirmation token signing.
- SUTRA_SUBJECT_HASH_KEY: 32+ byte secret for principal subject hashing.
- SUTRA_JWT_AUDIENCE: expected JWT audience (default: it-desk-app).
- SUTRA_JWT_ISSUER: expected JWT issuer.
- SUTRA_JWT_ALGORITHMS: comma-separated JWT algorithms (default: RS256,ES256).
- SUTRA_JWKS_URL: JWKS endpoint for asymmetric token verification.
- SUTRA_JWT_KEY: shared secret or PEM key when JWKS is not used.
- SUTRA_TELEMETRY_BACKEND: telemetry backend (otel or noop, default: otel).
- SUTRA_TELEMETRY_SERVICE_NAME: telemetry service name (default: it-helpdesk).
- SUTRA_TELEMETRY_SERVICE_VERSION: service version label in telemetry (default: dev).
- SUTRA_DEPLOYMENT_ENVIRONMENT: deployment environment label in telemetry (default: dev).
- HOST: bind host (default: 0.0.0.0).
- PORT: bind port (default: 8080).
"""

from __future__ import annotations

from collections.abc import Mapping
import os

import uvicorn

from sutra.identity import JwtAssertionVerifier, SubjectHasher
from sutra.models.google_genai import GoogleGenAIProvider
from sutra.server import create_app
from sutra.telemetry import NoopTelemetry
from sutra.telemetry.core import Telemetry

from it_helpdesk_workflow import build


def _required_secret(name: str) -> bytes:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    secret = value.encode()
    if len(secret) < 32:
        raise RuntimeError(f"{name} must be at least 32 bytes")
    return secret


class _TaggedTelemetry:
    """Adds stable deployment labels to every span and metric."""

    def __init__(self, inner: Telemetry, common: Mapping[str, str]) -> None:
        self._inner = inner
        self._common = dict(common)

    def span(self, name: str, attributes: Mapping[str, object] | None = None):
        merged = {**self._common, **(attributes or {})}
        return self._inner.span(name, merged)

    def count(self, name: str, value: int = 1, attributes: Mapping[str, object] | None = None) -> None:
        merged = {**self._common, **(attributes or {})}
        self._inner.count(name, value, merged)

    def record(self, name: str, value: float, attributes: Mapping[str, object] | None = None) -> None:
        merged = {**self._common, **(attributes or {})}
        self._inner.record(name, value, merged)


def _build_telemetry() -> Telemetry:
    backend = os.environ.get("SUTRA_TELEMETRY_BACKEND", "otel").strip().lower()
    service_name = os.environ.get("SUTRA_TELEMETRY_SERVICE_NAME", "it-helpdesk")
    common_attrs = {
        "sutra.service.name": service_name,
        "sutra.service.version": os.environ.get("SUTRA_TELEMETRY_SERVICE_VERSION", "dev"),
        "sutra.deployment.environment": os.environ.get("SUTRA_DEPLOYMENT_ENVIRONMENT", "dev"),
    }

    if backend == "noop":
        return _TaggedTelemetry(NoopTelemetry(), common_attrs)
    if backend != "otel":
        raise RuntimeError("SUTRA_TELEMETRY_BACKEND must be either 'otel' or 'noop'")

    try:
        from sutra.telemetry.otel import OTelTelemetry
    except ImportError as exc:
        raise RuntimeError("OTel telemetry selected but optional dependencies are missing") from exc
    return _TaggedTelemetry(OTelTelemetry(service_name=service_name), common_attrs)


def _build_verifier(subject_hash_key: bytes) -> JwtAssertionVerifier:
    audience = os.environ.get("SUTRA_JWT_AUDIENCE", "it-desk-app")
    issuer = os.environ.get("SUTRA_JWT_ISSUER")
    if not issuer:
        raise RuntimeError("Missing required environment variable: SUTRA_JWT_ISSUER")

    algorithms = [a.strip() for a in os.environ.get("SUTRA_JWT_ALGORITHMS", "RS256,ES256").split(",") if a.strip()]
    jwks_url = os.environ.get("SUTRA_JWKS_URL")
    jwt_key = os.environ.get("SUTRA_JWT_KEY")
    if bool(jwks_url) == bool(jwt_key):
        raise RuntimeError("Set exactly one of SUTRA_JWKS_URL or SUTRA_JWT_KEY")

    return JwtAssertionVerifier(
        audience=audience,
        issuer=issuer,
        algorithms=algorithms,
        jwks_url=jwks_url,
        key=jwt_key,
        hasher=SubjectHasher(subject_hash_key),
    )


def main() -> None:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("Missing required environment variable: GOOGLE_CLOUD_PROJECT")

    confirm_secret = _required_secret("SUTRA_CONFIRMATION_SECRET")
    subject_hash_key = _required_secret("SUTRA_SUBJECT_HASH_KEY")

    runtime = build(
        GoogleGenAIProvider(project=project),
        confirm_secret=confirm_secret,
        telemetry=_build_telemetry(),
    )
    verifier = _build_verifier(subject_hash_key)

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(create_app(runtime, verifier), host=host, port=port)


if __name__ == "__main__":
    main()

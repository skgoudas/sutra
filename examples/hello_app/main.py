"""Run the hello app locally (development only).

    SUTRA_DEV_SECRET=$(python -c 'import secrets;print(secrets.token_hex(32))') \
    GOOGLE_CLOUD_PROJECT=<project> \
    python -m hello_app.main

Production uses an asymmetric/JWKS assertion verifier and secrets from a secret manager.
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from sutra.config import load_bundle, runtime_from_bundle
from sutra.identity import JwtAssertionVerifier, SubjectHasher
from sutra.models.google_genai import GoogleGenAIProvider
from sutra.server import create_app
from sutra.tools import ConfirmationService

from .tools import TOOLS


def main() -> None:
    secret = os.environ["SUTRA_DEV_SECRET"].encode()
    bundle = load_bundle(Path(__file__).parent / "config")
    runtime = runtime_from_bundle(
        bundle,
        tools=TOOLS,
        providers={"vertex": GoogleGenAIProvider(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))},
        confirmations=ConfirmationService(secret),
    )
    verifier = JwtAssertionVerifier(audience="hello-app", issuer="dev-frontdoor", algorithms=["HS256"],
                                    key=secret, hasher=SubjectHasher(secret))
    uvicorn.run(create_app(runtime, verifier), host="127.0.0.1", port=8080)


if __name__ == "__main__":
    main()

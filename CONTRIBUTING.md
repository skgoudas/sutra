# Contributing to Sutra

Thanks for your interest in Sutra. This guide covers how to set up a development environment, the conventions the
codebase follows, and how changes get reviewed.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report security issues privately as
described in [SECURITY.md](SECURITY.md), not in public issues.

## Ways to contribute

- **Bug reports.** Open an issue with a minimal reproduction (ideally a failing test using `sutra.testing`).
- **Fixes and features.** For anything larger than a small fix, open an issue first so the design can be agreed
  before you write code. Changes to contracts (`sutra.contracts`) or security behaviour always need an issue.
- **Documentation and examples.** Corrections, clarifications and new examples are welcome.

## Development setup

Requirements: Python 3.12 or newer. [uv](https://docs.astral.sh/uv/) is recommended but plain `pip` works.

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q
```

With pip:

```sh
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

The test suite runs fully offline. It uses the scripted model provider in `sutra.testing`, so no cloud credentials are
needed. Only the examples that call a real model (for example `examples/it_helpdesk_server.py`) need Google Cloud
credentials.

## Project layout

| Path | Contents |
|---|---|
| `src/sutra/` | The SDK. See the package map in [README.md](README.md#package-map). |
| `tests/` | Pytest suite (`asyncio_mode = auto`). |
| `examples/` | Runnable example apps. They are imported by tests, so keep them working. |
| `deploy/` | Example deployment assets (Dockerfile, Cloud Run manifest). |
| `docs/` | User guides and design documents. |

## Conventions

- **Contracts first.** Public data shapes live in `sutra.contracts` as pydantic models. Do not leak framework types
  (ADK, OpenTelemetry, FastAPI) into contracts.
- **One choke point per concern.** Model calls go through `ModelClient`, tool calls through `ToolExecutor`, context
  through `ContextBuilder` and authorization through `PolicyEngine`. Do not add side paths around them.
- **Deny by default.** Units can only use the tools, models and delegates they list. Never widen access implicitly.
- **Identity comes from the verified principal.** Never read identity from the request body or from model output.
  Use `bound_args` to inject identity into tool arguments.
- **Telemetry hygiene.** Only emit allowlisted attributes (`sutra.*`, `gen_ai.*`). Never put prompts, responses,
  tool arguments, raw subjects, secrets or exception messages into spans, metrics or logs.
- **Optional dependencies stay optional.** `import sutra` must work with only `pydantic` installed. Import ADK,
  FastAPI, OpenTelemetry, PyJWT, PyYAML and google-genai lazily inside the modules that need them.
- **Typing.** The package ships `py.typed`. Add type hints to all new public functions.
- **Style.** Follow the existing code: `from __future__ import annotations`, small modules, no base classes for
  user code, docstrings on public APIs.

## Tests

- Every bug fix needs a regression test. Every feature needs tests for its success path and its denial or error paths.
- Security-relevant behaviour (policy, confirmation, identity, telemetry sanitisation) needs explicit negative tests.
- Use `sutra.testing.build_test_runtime`, which uses strict in-memory telemetry. A test fails if code emits a
  non-allowlisted attribute.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Keep each PR focused on one change. Separate refactors from behaviour changes.
3. Make sure `.venv/bin/python -m pytest -q` passes.
4. Update the docs and add an entry under **Unreleased** in [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
5. Fill in the PR template, including how you tested the change.

A maintainer reviews every PR. Changes to `sutra.contracts`, `sutra.policy`, `sutra.identity`, `sutra.tools`
(executor, confirmation, credentials) or `sutra.telemetry` need review from a maintainer familiar with the security
model.

## AI-assisted contributions

AI-assisted contributions are welcome. You are responsible for every line you submit: read it, test it, and make sure
it follows the conventions above. [AGENTS.md](AGENTS.md) contains guidance for coding agents.

## Licensing of contributions

Unless you state otherwise, any contribution you intentionally submit for inclusion is licensed under the
[Apache License, Version 2.0](LICENSE) (section 5), without any additional terms or conditions.

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, minor versions may contain breaking changes.

## [Unreleased]

### Added

- Licensed under the Apache License, Version 2.0.
- OpenTelemetry wiring for the IT helpdesk server example, with deployment labels on all spans and metrics.
- Example Cloud Run deployment for the IT helpdesk workflow (`deploy/it-helpdesk`).
- Open-source project documentation: contributing guide, security policy, code of conduct, agent guide, user guides.

### Fixed

- The IT helpdesk Docker image now installs the `otel` extra, which the server's default telemetry backend needs,
  and runs as a non-root user.
- The Cloud Run example is pinned to one instance while stores are in-memory.

### Removed

- Use-case-specific design material under `docs/use-cases/`. The design documents are now domain-neutral.

## [0.1.0]

First release: the minimum viable harness.

### Added

- Versioned pydantic contracts for requests, results, events, units, tools, models, context, policy and errors.
- `Runtime` with deadlines, cancellation, budgets, conversation locking and idempotency (in-memory stores).
- Unit authoring: `@unit`, `FunctionUnit`, `tool_loop`. Fixed, sticky and rules-based dispatch.
- Governed tools: `@tool`, `ToolExecutor` with allowlists, bound arguments, retries, circuit breakers, credentials,
  HMAC-bound confirmations and audit.
- `ModelClient` with aliases, fallback targets, token and cost budgets, and a Google GenAI / Vertex AI provider.
- `ContextBuilder` with authorization, fencing of untrusted data and token budgets.
- In-process `PolicyEngine` with auth-level checks, declarative rules and obligations.
- JWT principal verification and keyed subject hashing.
- Telemetry facade with an attribute allowlist, plus an OpenTelemetry backend.
- Online evaluation hooks with redaction.
- YAML config bundles (`load_bundle`, `runtime_from_bundle`).
- Google ADK 2.0 adapter (`AdkUnit`, `SutraAdkModel`, ADK tool bridge).
- FastAPI server (`POST /v1/runs` with JSON or SSE, `/healthz`, `/readyz`).
- Offline test kit (`sutra.testing`).

# Security Policy

Sutra is a harness for running LLM agents with real tools and real user identity, so security reports are taken
seriously.

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):
on the repository page, open the **Security** tab and select **Report a vulnerability**.

Please include:

- the affected version or commit;
- a description of the issue and its impact;
- a minimal reproduction (a failing test using `sutra.testing` is ideal);
- any suggested fix.

We aim to acknowledge reports within 3 business days and to agree on a disclosure timeline with you. We credit
reporters in the advisory unless you ask us not to.

## Supported versions

Sutra is pre-1.0. Only the latest released minor version receives security fixes.

| Version | Supported |
|---|---|
| 0.1.x | Yes |

## Scope

These count as vulnerabilities:

- A unit calls a tool, model or delegate that is not in its allowlist.
- A tool receives an identity value that came from the request body or from model output instead of the verified
  principal (`bound_args` bypass).
- A side-effecting tool marked `requires_confirmation` runs without a valid, matching, unexpired confirmation token,
  or a token can be reused within one process.
- The JWT verifier accepts a token with a wrong algorithm, issuer, audience or signature, or one that has expired.
- Execution limits (steps, model calls, tool calls, tokens, cost, deadline) can be exceeded.
- Prompts, responses, tool arguments, raw subjects, secrets or exception messages leak into telemetry, audit records
  or error responses.
- Untrusted context (tool results, retrieved documents) escapes its fencing in the `ContextBuilder`.

These are out of scope:

- Prompt injection that only changes model wording but cannot get past a runtime control. The model is untrusted by
  design. A report is in scope if prompt injection bypasses one of the controls above.
- Vulnerabilities in your own tools, prompts or deployment configuration.
- Issues that need a compromised host, compromised secrets or a malicious operator.
- Multi-instance replay of confirmation tokens when you scale out with the default in-memory stores. This is a known
  limitation; see the [production checklist](docs/production-checklist.md).

## Security model in brief

| Control | Where |
|---|---|
| Verified identity (pinned algorithms; `iss`, `aud`, `exp`, `iat`, `sub` required) | `sutra.identity` |
| Subjects stored and emitted only as keyed HMACs | `sutra.identity.SubjectHasher` |
| Deny-by-default tool, model and delegate allowlists per unit | `sutra.runtime`, `sutra.tools` |
| Identity-bound tool arguments hidden from the model | `ToolDefinition.bound_args` |
| HMAC-bound, expiring, single-use confirmation for writes | `sutra.tools.confirmation` |
| Policy decisions (auth level, rules, obligations) | `sutra.policy` |
| Budgets and deadlines | `ExecutionLimits`, `ExecutionContext` |
| Telemetry attribute allowlist; no content in spans | `sutra.telemetry` |

The full model is in [docs/design/05-security-and-policy.md](docs/design/05-security-and-policy.md).

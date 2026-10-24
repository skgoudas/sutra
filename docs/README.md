# Sutra documentation

| Guide | What it covers |
|---|---|
| [Getting started](getting-started.md) | Install, write a tool and a unit, run it offline, then against a real model. |
| [Concepts](concepts.md) | Units, tools, models, context, policy, identity, runtime, dispatch, telemetry. |
| [Production checklist](production-checklist.md) | What to configure and what to watch out for before serving real users. |
| [Example deployment](../deploy/it-helpdesk/README.md) | The IT helpdesk ADK workflow on Cloud Run. |

## Design documents

The [design set](design/README.md) explains why the harness is built the way it is:

1. [Architecture](design/01-architecture.md)
2. [Contracts](design/02-contracts.md)
3. [Runtime and execution](design/03-runtime-and-execution.md)
4. [Context and state](design/04-context-and-state.md)
5. [Security and policy](design/05-security-and-policy.md)
6. [Observability, reliability and evaluation](design/06-observability-reliability-evaluation.md)
7. [Configuration and control plane](design/07-configuration-and-control-plane.md)
8. [Technology decisions](design/08-technology-decisions.md)
9. [Minimum viable harness, NFRs and roadmap](design/09-mvh-nfr-roadmap.md)

## Examples

| Example | Shows |
|---|---|
| [examples/leave_assistant.py](../examples/leave_assistant.py) | Smallest end-to-end app: read tool, confirmed write tool, identity-bound args. |
| [examples/hello_app](../examples/hello_app) | Config bundle (YAML) with a policy rule, a plain-Python unit and an ADK unit. |
| [examples/it_helpdesk_workflow.py](../examples/it_helpdesk_workflow.py) | Multi-agent Google ADK 2.0 workflow running as a Sutra `AdkUnit`. |
| [examples/it_helpdesk_server.py](../examples/it_helpdesk_server.py) | The workflow served over HTTP with JWT auth and OpenTelemetry. |

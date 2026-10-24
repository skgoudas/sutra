# IT Helpdesk Deployment (Cloud Run)

This deploys the ADK workflow example as an HTTP API using `sutra.server`.

## Files

- `examples/it_helpdesk_server.py`: runtime + verifier + FastAPI app entrypoint.
- `deploy/it-helpdesk/Dockerfile`: image build for Cloud Run.
- `deploy/it-helpdesk/cloudrun.service.yaml`: Cloud Run service spec.

## Prerequisites

- GCP project with Vertex AI enabled.
- Artifact Registry repository.
- Secret Manager secrets for:
  - `it-helpdesk-confirmation-secret` (32+ bytes)
  - `it-helpdesk-subject-hash-key` (32+ bytes)
- JWT issuer and JWKS endpoint from your trusted front door.

## Build and push image

```sh
export PROJECT_ID="your-project"
export REGION="us-central1"
export REPO="agents"
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/it-helpdesk:$(date +%Y%m%d-%H%M%S)"

gcloud auth configure-docker "${REGION}-docker.pkg.dev"
docker build -f deploy/it-helpdesk/Dockerfile -t "${IMAGE}" .
docker push "${IMAGE}"
```

## Prepare Cloud Run manifest

```sh
cp deploy/it-helpdesk/cloudrun.service.yaml /tmp/it-helpdesk.service.yaml
sed -i '' "s|IMAGE_PLACEHOLDER|${IMAGE}|g" /tmp/it-helpdesk.service.yaml
sed -i '' "s|PROJECT_ID_PLACEHOLDER|${PROJECT_ID}|g" /tmp/it-helpdesk.service.yaml
sed -i '' "s|ISSUER_PLACEHOLDER|https://issuer.example.com|g" /tmp/it-helpdesk.service.yaml
sed -i '' "s|JWKS_URL_PLACEHOLDER|https://issuer.example.com/.well-known/jwks.json|g" /tmp/it-helpdesk.service.yaml
```

## Deploy

```sh
gcloud run services replace /tmp/it-helpdesk.service.yaml --region "${REGION}" --project "${PROJECT_ID}"
```

## Runtime contract

- Endpoint: `POST /v1/runs` (JSON or SSE)
- Health: `GET /healthz`
- Readiness: `GET /readyz`
- Auth: bearer JWT validated with `JwtAssertionVerifier`

## Telemetry

- Default backend is OpenTelemetry (`SUTRA_TELEMETRY_BACKEND=otel`).
- Set `SUTRA_TELEMETRY_BACKEND=noop` to disable telemetry emission.
- Deployment labels are attached to every span and metric via:
  - `SUTRA_TELEMETRY_SERVICE_NAME`
  - `SUTRA_TELEMETRY_SERVICE_VERSION`
  - `SUTRA_DEPLOYMENT_ENVIRONMENT`
- The implementation only emits allowlisted attributes (`sutra.*`, `gen_ai.*`), and does not include prompt or response content in spans.

## Notes

- The service is pinned to one instance (`maxScale: 1`). Session history, idempotency, conversation locks and
  confirmation-token single-use tracking are in-memory. Scaling out before backing them with a shared store would
  lose history across instances and allow a confirmation token to be replayed on another instance.
- `UNIT` allowlist/limits still apply at runtime; registering tools/models globally does not bypass unit policy.
- Keep `SUTRA_CONFIRMATION_SECRET` and `SUTRA_SUBJECT_HASH_KEY` stable across revisions to avoid token and identity drift.
- For production, prefer `SUTRA_JWKS_URL` over shared symmetric JWT keys.

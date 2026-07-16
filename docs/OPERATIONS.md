# RepoLume Operations

**Status:** Milestone 2 local authentication/GitHub App operations are implemented and verified with mocked provider responses. No live GitHub App, hosted environment, dashboard, alert, backup, or production runbook has been verified.

## Service inventory

| Service | Exposure | Planned owner responsibility | Current state |
| --- | --- | --- | --- |
| React frontend | Public via Vercel | User interface and safe rendering | Not created |
| FastAPI API | Public via Railway | Authenticated API, GitHub webhooks, health | Milestone 2 routes verified locally; not deployed |
| Indexing worker | Railway private service | Durable job execution and cleanup | Not created |
| Embedding service | Railway private service | Authenticated bounded embeddings | Not created |
| PostgreSQL | Neon private credentials | Durable identity, access, delivery, application, and job state | Two-revision schema/migration verified on disposable PostgreSQL 18.4; managed production instance not provisioned |
| Redis | Private managed service | Queue, ephemeral cache, rate-limit support | Not provisioned |
| Qdrant | Qdrant Cloud authenticated | Scoped vectors | Not provisioned |
| Hosted LLM | Server-side provider API | Tool selection and answer synthesis | Not configured |
| GitHub App | GitHub | Authentication, installation tokens, webhooks | Adapter and mocked tests complete; real App not configured or verified |

## Health contract

- `GET /api/v1/health/live` indicates API process liveness only, returns `200 {"status":"ok"}`, and does not probe or reveal configuration/dependencies.
- `GET /api/v1/health/ready` runs a bounded PostgreSQL `SELECT 1`; it returns `200` with database `ready` or a shared safe `503` error when unavailable.
- Worker health will use durable heartbeat timestamps plus process metrics; an HTTP endpoint is not required if the platform and reconciler can observe it privately.
- The embedding service will expose a private authenticated health response with model identity, version, dimension, and load state, but no raw data.

Both implemented responses include a generated or validated `X-Request-ID` and API security headers. PostgreSQL is the only current readiness dependency. Liveness remains successful when PostgreSQL is unavailable.

## Logging and metrics contract

Structured logs currently include request IDs plus safe startup and HTTP metadata: environment name, boolean/count configuration summaries, route path, method, status, and duration. Later product logs may add opaque actor/installation/repository/session/job identifiers, stage, tool name, result count, and safe error code.

Logs must exclude tokens, cookies, secrets, clone credential material, full repository chunks, full prompts/responses, private file contents, and complete chat messages.

Uvicorn access logging is disabled because its raw target can include OAuth codes and other unbounded query data. `httpx`, `httpx2`, and `httpcore` INFO logs are suppressed for the same reason. Host and container JSON logs were inspected during Milestone 2; a callback with an OAuth-code sentinel logged the route path only. No database URL, credential, cookie, token, private key, webhook secret, prompt, private content, or response body was observed.

Metrics remain planned: request and tool latency/error rates, job queue age/duration, worker heartbeat/stuck jobs, embedding throughput, vector operations, model token/cost usage, indexing stages, webhook outcomes, and deletion backlog. Names, cardinality limits, alert thresholds, and retention require deployed telemetry.

## Planned runbooks

The following sections are operational requirements, not executed procedures. Exact provider commands, dashboard links, escalation contacts, recovery-point objectives, and evidence fields will be filled in during implementation/deployment.

### Failed or stuck indexing

1. Locate the job through its safe ID and inspect durable stage, heartbeat, attempt, and safe error code.
2. Confirm repository access is still authorized before retrying.
3. Verify the last successful index remains active.
4. Clean incomplete vectors/graph data idempotently.
5. Requeue within the retry policy or mark permanently failed with a user-safe message.
6. Verify the temporary clone no longer exists.

### Database migration rollback

1. Stop incompatible application rollout and block concurrent migration runners.
2. Confirm the exact current and target Alembic revisions.
3. Restore from a verified backup when a migration is not safely reversible; do not improvise destructive downgrades.
4. Run the documented downgrade only when its data effects were reviewed and tested.
5. Verify schema revision, API readiness, and data invariants before resuming traffic.

Migration upgrade, current-revision, consistency, and downgrade commands are available. Production backup recovery is not.

### Key rotation

1. Identify the credential and all consumers without printing its value.
2. Create a replacement in the provider/platform secret store.
3. Support overlap when the credential type permits it; otherwise schedule a controlled restart.
4. Redeploy/restart affected services and verify safe health and authentication behavior.
5. Revoke the old credential and monitor failures.
6. Record rotation time and affected secret name, never the value.

`ACCESS_TOKEN_SECRET` rotation currently invalidates access tokens (maximum default lifetime 15 minutes). `TOKEN_HASH_SECRET` rotation invalidates all refresh tokens and outstanding OAuth state, requiring users to sign in again. GitHub client secret, private key, and webhook-secret rotation must follow GitHub's supported overlap/replacement sequence. Overlapping RepoLume key IDs are not implemented, so schedule and communicate the session invalidation.

### GitHub webhook troubleshooting

1. Correlate GitHub delivery ID with the safe webhook record and logs.
2. Distinguish signature failure, duplicate delivery, unsupported event, queue failure, and handler failure.
3. Never bypass signature validation to replay production payloads.
4. Redeliver through GitHub only after idempotency is confirmed.
5. Verify revocation events have blocked access even if downstream purge is pending.

The delivery table stores the safe delivery ID, event/action, optional external installation/repository IDs, status, safe error code, and processing time. It deliberately does not store webhook bodies. A `queued` push/repository delivery is expected to remain without a consumer until Milestone 3+; do not mark it processed manually.

### Refresh-token replay response

1. Correlate the safe request ID and `token_reuse_detected` response without collecting the cookie or bearer token.
2. Treat the complete refresh family as revoked; do not restore individual rows.
3. Require a fresh GitHub sign-in and investigate whether parallel tabs, a retried client, or token theft caused replay.
4. Rotate `TOKEN_HASH_SECRET` only for a broad compromise because it invalidates all sessions and outstanding OAuth state.
5. Verify that no raw token or cookie was copied into logs or incident notes.

### Qdrant outage

1. Keep the last PostgreSQL active-version record unchanged.
2. Fail retrieval with a safe tool/dependency status; do not search without required filters or substitute another tenant's data.
3. Leave indexing retryable and clean incomplete version data after recovery.
4. Verify collection health and repository/version filter behavior before resuming.

### Redis outage

1. Preserve PostgreSQL job state and return a clear temporary queue error for new mutations.
2. Do not mark unenqueued jobs as running or complete.
3. After recovery, reconcile durable queued/retryable jobs idempotently.
4. Detect duplicate delivery at the job transition layer.

### LLM provider outage

1. Do not fabricate an answer from unavailable synthesis.
2. Return a safe retryable tool/provider failure; retain successful evidence only according to the data policy.
3. Enforce timeouts and prevent retry storms.
4. Verify usage/cost records do not charge failed work incorrectly.

### Embedding-service outage

1. Do not activate the in-progress index version.
2. Mark the stage safely, retry within policy, and retain the last successful index.
3. Confirm model identity/dimension matches the collection before resuming batches.

### Repository deletion verification

1. Confirm access is blocked and pending jobs cannot continue.
2. Confirm Qdrant has no points for any version of the repository.
3. Confirm symbols, edges, sessions/messages, repository caches, retained job data, and temporary artifacts are removed per policy.
4. Confirm repeated deletion is safe and no webhook/retry can recreate data without fresh authorization.
5. Mark completion only after every required store reports success.

## Backup and recovery

Neon and Qdrant backup policies, Redis persistence, recovery objectives, restore drills, and responsible operators are undecided and unverified. Before production launch, RepoLume must document actual provider settings and execute recovery tests. Raw clones are intentionally not backed up.

## Deployment and migrations

Deployments will run only after CI. Production migrations will be a deliberate, separately observable step and not an accidental side effect of every web process startup. Worker termination must allow retry/recovery. No local filesystem is permanent storage.

The API never runs Alembic during application startup. From the repository root, with `DATABASE_URL` set to a PostgreSQL `postgresql+asyncpg://` URL:

```sh
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/alembic -c backend/alembic.ini current
.venv/bin/alembic -c backend/alembic.ini check
```

Review generated SQL and backup/restore compatibility before any future production migration. The initial migration downgrade was exercised by the integration suite against a disposable database; that does not make production downgrades universally safe.

## Local Milestone 2 procedure

Use Python 3.13 for the reproducible baseline. Install the hashed development lock:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes --requirement backend/requirements-dev.lock
```

Supply local values through an untracked `.env` or process environment. Configure a real PostgreSQL URL plus test-only GitHub/RepoLume authentication values. Integration tests require `TEST_DATABASE_URL`, truncate it, and fail instead of falling back to SQLite.

```sh
export APP_ENV=development
export DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:5432/<database>'
export TEST_DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:5432/<disposable_test_database>'
export GITHUB_APP_ID='<app-id>'
export GITHUB_CLIENT_ID='<client-id>'
export GITHUB_CLIENT_SECRET='<secret-store-value>'
export GITHUB_APP_PRIVATE_KEY='<PEM-private-key>'
export GITHUB_WEBHOOK_SECRET='<secret-store-value>'
export GITHUB_OAUTH_CALLBACK_URL='http://127.0.0.1:8000/api/v1/auth/github/callback'
export ACCESS_TOKEN_SECRET='<independent-random-value-at-least-32-characters>'
export TOKEN_HASH_SECRET='<independent-random-value-at-least-32-characters>'
export CORS_ORIGINS='["http://127.0.0.1:3000"]'
```

Apply migrations and start the API:

```sh
.venv/bin/alembic -c backend/alembic.ini upgrade head
cd backend
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

From another shell:

```sh
curl --fail-with-body http://127.0.0.1:8000/api/v1/health/live
curl --fail-with-body http://127.0.0.1:8000/api/v1/health/ready
```

Stop Uvicorn with `Ctrl-C`; lifespan shutdown disposes the async engine. Docker Compose can start the `postgres` and `api` services after a non-empty local `POSTGRES_PASSWORD` and a container-addressable `DATABASE_URL` are supplied. Do not commit those values.

For live GitHub verification, configure the App callback and webhook URLs to the public HTTPS API; grant read-only Metadata, Contents, and Pull requests permissions; subscribe to Installation, Installation repositories, Push, and Repository events; then install it on a controlled test account/organization. Automated tests require no real credentials and use injected mocked responses.

## Milestone 2 verification commands

```sh
.venv/bin/ruff format --check backend
.venv/bin/ruff check backend
.venv/bin/mypy backend/app backend/tests
APP_ENV=test DATABASE_URL="$DATABASE_URL" .venv/bin/alembic -c backend/alembic.ini upgrade head
APP_ENV=test DATABASE_URL="$DATABASE_URL" .venv/bin/alembic -c backend/alembic.ini current
APP_ENV=test DATABASE_URL="$DATABASE_URL" .venv/bin/alembic -c backend/alembic.ini check
cd backend
APP_ENV=test DATABASE_URL="$DATABASE_URL" TEST_DATABASE_URL="$TEST_DATABASE_URL" ../.venv/bin/pytest
cd ..
.venv/bin/pip-audit --requirement backend/requirements.lock --disable-pip
podman build --tag repolume-api:milestone2 backend
podman image inspect --format '{{.Config.User}}' repolume-api:milestone2
```

The Milestone 2 execution results are recorded in `docs/BUILD_STATUS.md`. GitHub Actions repeats the quality, PostgreSQL, audit, and image-user checks on Python 3.13 with test-only placeholder authentication settings. Hosted CI has not run because no remote workflow run exists.

## Incident evidence policy

Incident notes may reference opaque identifiers, timestamps, status transitions, versions, counts, and safe error categories. They must not paste secrets, private repository content, complete chat messages, full prompts, or full provider responses.

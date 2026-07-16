# RepoLume Operations

**Status:** Milestone 3 local API/worker/PostgreSQL/Redis/safe-clone operations are implemented and verified with mocked GitHub responses and a controlled Git fixture. No live GitHub App, hosted environment, dashboard, alert, backup, or production runbook has been verified.

## Service inventory

| Service | Exposure | Planned owner responsibility | Current state |
| --- | --- | --- | --- |
| React frontend | Public via Vercel | User interface and safe rendering | Not created |
| FastAPI API | Public via Railway | Authenticated API, GitHub webhooks, repository selection/status, health | Milestone 3 routes verified locally; not deployed |
| Indexing worker | Railway private service | Durable claim/heartbeat/retry, safe clone/discovery, cleanup | Implemented and locally verified; not deployed |
| Embedding service | Railway private service | Authenticated bounded embeddings | Not created |
| PostgreSQL | Neon private credentials | Durable identity, access, delivery, application, and job state | Three-revision schema/migration verified on disposable PostgreSQL 18.4; managed production instance not provisioned |
| Redis | Private managed service | Opaque job-ID Stream delivery; later cache/rate-limit support | Redis 8.8 locally verified; managed authenticated TLS service not provisioned |
| Qdrant | Qdrant Cloud authenticated | Scoped vectors | Not provisioned |
| Hosted LLM | Server-side provider API | Tool selection and answer synthesis | Not configured |
| GitHub App | GitHub | Authentication, installation tokens, webhooks | Adapter and mocked tests complete; real App not configured or verified |

## Health contract

- `GET /api/v1/health/live` indicates API process liveness only, returns `200 {"status":"ok"}`, and does not probe or reveal configuration/dependencies.
- `GET /api/v1/health/ready` runs bounded PostgreSQL and Redis probes; it returns `200` only when both report `ready`, otherwise a content-free `503` readiness body.
- Worker health uses durable job heartbeat timestamps, pending-entry reclaim, stuck-job recovery, process supervision, and later metrics; it has no public HTTP endpoint.
- The embedding service will expose a private authenticated health response with model identity, version, dimension, and load state, but no raw data.

Both implemented responses include a generated or validated `X-Request-ID` and API security headers. PostgreSQL and Redis are readiness dependencies. Liveness remains successful when either dependency is unavailable.

## Logging and metrics contract

Structured logs include request IDs plus safe startup/HTTP metadata and worker job/repository IDs, attempt, stage, counts, and safe error code. They do not include repository owner/name/path/content.

Logs must exclude tokens, cookies, secrets, clone credential material, full repository chunks, full prompts/responses, private file contents, and complete chat messages.

Uvicorn access logging is disabled because its raw target can include OAuth codes and other unbounded query data. `httpx`, `httpx2`, and `httpcore` INFO logs are suppressed for the same reason. Milestone 3 tests and final verification inspect logs for database/Redis URLs, credential/token/cookie/private-key/webhook sentinels, repository paths/content, askpass values, and provider bodies. Only allowlisted operational metadata is permitted.

Metrics remain planned: request and tool latency/error rates, job queue age/duration, worker heartbeat/stuck jobs, embedding throughput, vector operations, model token/cost usage, indexing stages, webhook outcomes, and deletion backlog. Names, cardinality limits, alert thresholds, and retention require deployed telemetry.

## Runbooks

The job/Redis procedures below reflect implemented local behavior but have not been exercised on a managed production platform. Exact dashboard links, escalation contacts, recovery objectives, and evidence fields remain deployment work.

### Failed or stuck indexing

1. Locate the job through its safe ID and inspect durable stage, heartbeat, attempt, and safe error code.
2. Confirm repository access is still authorized before retrying.
3. Verify the last successful index remains active.
4. Clean incomplete vectors/graph data idempotently.
5. Requeue within the retry policy or mark permanently failed with a user-safe message.
6. Verify the temporary clone no longer exists.

Implemented state behavior:

- `queued`/due `retrying` jobs are reconciled back into Redis when delivery is absent or stale.
- `running` jobs whose heartbeat exceeds `WORKER_ABANDONED_AFTER_SECONDS` become retryable until `WORKER_MAX_ATTEMPTS`; exhausted jobs become permanently `failed`.
- Retry uses bounded exponential backoff plus jitter. Do not manually change attempts or mark a job complete.
- Duplicate Redis delivery is acknowledged after a conditional PostgreSQL claim fails; it does not start another clone.
- Access-revoked work becomes `cancelled` before token minting or clone.

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

The delivery table stores the safe delivery ID, event/action, optional external installation/repository IDs, status, safe error code, and processing time. It deliberately does not store webhook bodies. Webhook-triggered reindex wiring is still deferred; do not mark queued webhook records processed manually.

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

Implemented local recovery procedure:

1. Confirm PostgreSQL readiness separately and leave queued/retrying rows unchanged.
2. Restore the configured Redis endpoint; do not copy repository data or credentials into Redis.
3. Restart at least one worker. Startup runs abandoned recovery and due-job reconciliation before reading deliveries.
4. Confirm readiness reports Redis `ready`, due jobs receive a fresh enqueue timestamp, and exactly one worker wins each conditional claim.
5. Check only safe job IDs/stages/attempts and clone-root cleanup; never inspect tokens or repository bytes in Redis/logs.

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

## Local Milestone 3 procedure

Use Python 3.13 for the reproducible baseline. Install the hashed development lock:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes --requirement backend/requirements-dev.lock
```

Supply local values through an untracked `.env` or process environment. Configure real PostgreSQL and Redis URLs plus test-only GitHub/RepoLume authentication values. Integration tests require `TEST_DATABASE_URL` and `TEST_REDIS_URL`; they truncate/flush them and never fall back to SQLite or an in-memory queue.

```sh
export APP_ENV=development
export DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:5432/<database>'
export TEST_DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:5432/<disposable_test_database>'
export REDIS_URL='redis://127.0.0.1:6379/0'
export TEST_REDIS_URL='redis://127.0.0.1:6379/15'
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

Start the worker separately from `backend/`:

```sh
../.venv/bin/python -m app.worker
```

From another shell:

```sh
curl --fail-with-body http://127.0.0.1:8000/api/v1/health/live
curl --fail-with-body http://127.0.0.1:8000/api/v1/health/ready
```

Stop Uvicorn/worker with `Ctrl-C`; shutdown closes database, GitHub, and Redis clients. Docker Compose can start `postgres`, `redis`, `api`, and `worker` after a non-empty local `POSTGRES_PASSWORD` and container-addressable `DATABASE_URL`/`REDIS_URL` are supplied. Do not commit those values.

For live GitHub verification, configure the App callback and webhook URLs to the public HTTPS API; grant read-only Metadata, Contents, and Pull requests permissions; subscribe to Installation, Installation repositories, Push, and Repository events; then install it on a controlled test account/organization. Automated tests require no real credentials and use injected mocked responses.

## Milestone 3 verification commands

```sh
.venv/bin/ruff format --check backend
.venv/bin/ruff check backend
.venv/bin/mypy backend/app backend/tests
.venv/bin/python -m pip check
APP_ENV=test DATABASE_URL="$DATABASE_URL" REDIS_URL="$REDIS_URL" .venv/bin/alembic -c backend/alembic.ini upgrade head
APP_ENV=test DATABASE_URL="$DATABASE_URL" REDIS_URL="$REDIS_URL" .venv/bin/alembic -c backend/alembic.ini current
APP_ENV=test DATABASE_URL="$DATABASE_URL" REDIS_URL="$REDIS_URL" .venv/bin/alembic -c backend/alembic.ini check
cd backend
APP_ENV=test DATABASE_URL="$DATABASE_URL" TEST_DATABASE_URL="$TEST_DATABASE_URL" REDIS_URL="$REDIS_URL" TEST_REDIS_URL="$TEST_REDIS_URL" ../.venv/bin/pytest
cd ..
.venv/bin/pip-audit --requirement backend/requirements.lock --disable-pip
podman build --tag repolume-api:milestone3 backend
podman image inspect --format '{{.Config.User}}' repolume-api:milestone3
```

The Milestone 3 execution results are recorded in `docs/BUILD_STATUS.md`. GitHub Actions repeats quality, PostgreSQL 18, Redis 8.8, migration, audit, and image-user checks on Python 3.13 with test-only authentication settings. Hosted CI has not run because no remote workflow run exists.

## Incident evidence policy

Incident notes may reference opaque identifiers, timestamps, status transitions, versions, counts, and safe error categories. They must not paste secrets, private repository content, complete chat messages, full prompts, or full provider responses.

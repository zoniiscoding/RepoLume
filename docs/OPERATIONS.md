# RepoLume Operations

**Status:** Milestone 5 local API/worker/PostgreSQL/Redis/Qdrant/private-embedding/static-index operations are implemented and verified with mocked GitHub responses and controlled Git fixtures. No live GitHub App, hosted environment, dashboard, alert, backup, or production runbook has been verified.

## Service inventory

| Service | Exposure | Planned owner responsibility | Current state |
| --- | --- | --- | --- |
| React frontend | Public via Vercel | User interface and safe rendering | Not created |
| FastAPI API | Public via Railway | Authenticated API, GitHub webhooks, repository selection/status, health | Milestone 5 searchable status and three-dependency readiness verified locally; not deployed |
| Indexing worker | Railway private service | Durable jobs, safe static ingestion, embedding/vector validation, atomic activation, cleanup | Implemented and locally verified; not deployed |
| Embedding service | Railway private service | Authenticated bounded fixed-model embeddings | Implemented, real pinned model verified, UID 10002 image built; not deployed |
| PostgreSQL | Neon private credentials | Durable identity, access, delivery, job/build/count/activation/cleanup truth, symbols | Five-revision schema verified on disposable PostgreSQL 18; managed production instance not provisioned |
| Redis | Private managed service | Opaque job-ID Stream delivery; later cache/rate-limit support | Redis 8.8 locally verified; managed authenticated TLS service not provisioned |
| Qdrant | Qdrant Cloud authenticated | Installation/repository/version-scoped vectors and private citation chunks | Qdrant 1.18.2 locally verified; managed authenticated service not provisioned |
| Hosted LLM | Server-side provider API | Tool selection and answer synthesis | Not configured |
| GitHub App | GitHub | Authentication, installation tokens, webhooks | Adapter and mocked tests complete; real App not configured or verified |

## Health contract

- `GET /api/v1/health/live` indicates API process liveness only, returns `200 {"status":"ok"}`, and does not probe or reveal configuration/dependencies.
- `GET /api/v1/health/ready` runs bounded PostgreSQL, Redis, and exact Qdrant collection/configuration probes; it returns `200` only when all report `ready`, otherwise a topology-minimizing `503` body.
- Worker startup requires authenticated exact embedding-model readiness, Qdrant collection compatibility, Redis consumer-group setup, and PostgreSQL reconciliation. Ongoing health uses durable heartbeats/process supervision; there is no public worker HTTP endpoint.
- Private `GET /health/live` on the embedding service is dependency-independent. Credentialed `GET /health/ready` returns only load state and fixed model/revision/dimension/normalization/token ceiling; unauthenticated requests return 401.

API responses include a generated or validated `X-Request-ID` and security headers. PostgreSQL, Redis, and Qdrant are API readiness dependencies. API and model liveness remain successful during dependency/model readiness failure.

## Logging and metrics contract

Structured logs include request IDs plus safe startup/HTTP metadata and worker job/repository IDs, attempt, stage, counts, model identity, and safe error code. The embedding service logs request ID, document/query kind, count, duration, and safe model state only. They do not include repository owner/name/path/content or vectors.

Logs must exclude tokens, cookies, secrets, clone credential material, full repository chunks, full prompts/responses, private file contents, and complete chat messages.

Uvicorn access logging is disabled because its raw target can include OAuth codes and unbounded query data. `httpx`, `httpx2`, and `httpcore` INFO logs are suppressed. Milestone 5 tests and final verification inspect logs for database/Redis/Qdrant URLs or keys, GitHub/browser/service credentials, paths/source/chunks/vectors, parser/model internals, askpass values, and provider bodies. Only allowlisted operational metadata is permitted.

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
- Static processing exposes `parsing` and `chunking` durable stages. File-local malformed/oversized/unsupported cases increment safe categories; repository chunk overflow and unsafe paths fail closed.
- `parser_timeout` and `internal_parser_failure` are nonretryable for the same immutable commit/config. Operators must inspect capacity/configuration without collecting source or raw parser exceptions before submitting fresh work.
- M5 additionally exposes `embedding`, `storing_vectors`, `validating_index`, and `activating_index`. Do not mark a build ready/active unless expected, embedded, and vector counts match exactly with zero failed/skipped chunks.
- Before activation, failed inactive vectors are deleted only by trusted installation/repository/version scope and the previous active build remains untouched. After activation, a failed superseded cleanup is recorded as pending and must be retried with that exact scope.
- Embedding/Qdrant transport outages and timeouts follow bounded retry policy. Authentication, model/collection/dimension/metadata/count/scope mismatches, token/input limits, invalid responses, and activation races fail closed and require review rather than blind retry.

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
2. API readiness becomes unavailable; do not bypass readiness or substitute another collection/tenant.
3. Keep pre-activation indexing retryable where classified and record failed/pending cleanup state. Never activate from Qdrant's count alone.
4. After recovery, verify 768/cosine/model/revision/L2 collection metadata and all payload index types.
5. Validate the exact installation/repository/version count and metadata, then retry only the durable job/cleanup transition. Never issue collection-wide deletion.

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
2. Confirm private liveness separately from authenticated model readiness without printing the service bearer.
3. Mark the stage safely, retry within policy, and retain the last successful index.
4. Confirm exact model identifier, immutable revision, 768 dimensions, L2 normalization, and preprocessing compatibility before resuming batches.
5. If model/cache load fails, replace or rebuild the service; never enable remote code or silently change model/revision to restore readiness.

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

## Local Milestone 5 procedure

Use Python 3.13 for the reproducible baseline. Install the hashed development lock:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes --requirement backend/requirements-dev.lock
.venv/bin/python -m pip install --require-hashes --requirement embedding_service/requirements-dev.lock
```

Supply local values through an untracked `.env` or process environment. Configure real PostgreSQL, Redis, Qdrant, and private embedding endpoints plus test-only GitHub/RepoLume authentication values. Integration tests require `TEST_DATABASE_URL`, `TEST_REDIS_URL`, `TEST_QDRANT_URL`, `TEST_EMBEDDING_SERVICE_URL`, and `TEST_EMBEDDING_SERVICE_TOKEN`; they destroy disposable test state and never fall back to SQLite or in-memory queue/vector/model substitutes.

Parser defaults and documentation are in `.env.example`. Tune them as one validated set: parser input cannot exceed the discovery file ceiling; chunks cannot exceed symbol/document-section ceilings; child CPU cannot exceed the parent wall timeout. Do not increase bounds for untrusted repositories without capacity and failure testing.

```sh
export APP_ENV=development
export DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:5432/<database>'
export TEST_DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:5432/<disposable_test_database>'
export REDIS_URL='redis://127.0.0.1:6379/0'
export TEST_REDIS_URL='redis://127.0.0.1:6379/15'
export QDRANT_URL='http://127.0.0.1:6333'
export TEST_QDRANT_URL='http://127.0.0.1:6333'
export QDRANT_COLLECTION_NAME='repolume_test_chunks'
export EMBEDDING_SERVICE_URL='http://127.0.0.1:8100'
export TEST_EMBEDDING_SERVICE_URL='http://127.0.0.1:8100'
export EMBEDDING_SERVICE_TOKEN='<independent-random-value-at-least-32-characters>'
export TEST_EMBEDDING_SERVICE_TOKEN="$EMBEDDING_SERVICE_TOKEN"
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

Start the pinned model service before the worker. Artifact download needs network access only during initial cache population; runtime is local-files-only:

```sh
cd embedding_service
EMBEDDING_MODEL_CACHE_DIR=/tmp/repolume-models ../.venv/bin/python -m app.download_model
EMBEDDING_ENVIRONMENT=development EMBEDDING_LOG_JSON=true \
EMBEDDING_MODEL_CACHE_DIR=/tmp/repolume-models EMBEDDING_MODEL_LOCAL_FILES_ONLY=true \
HF_HUB_OFFLINE=1 ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8100 --no-access-log
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

Stop processes with `Ctrl-C`; shutdown closes database, GitHub, Redis, embedding HTTP, Qdrant, and model resources. Docker Compose can start `postgres`, `redis`, `qdrant`, `embedding-service`, `api`, and `worker` after non-empty local secrets and container-addressable URLs are supplied. The embedding image preloads the model during build. Do not commit any credential.

For live GitHub verification, configure the App callback and webhook URLs to the public HTTPS API; grant read-only Metadata, Contents, and Pull requests permissions; subscribe to Installation, Installation repositories, Push, and Repository events; then install it on a controlled test account/organization. Automated tests require no real credentials and use injected mocked responses.

## Milestone 5 verification commands

```sh
.venv/bin/ruff format --check backend
.venv/bin/ruff check backend
.venv/bin/mypy --config-file backend/pyproject.toml backend/app backend/tests
.venv/bin/ruff format --check embedding_service
.venv/bin/ruff check embedding_service
.venv/bin/mypy --config-file embedding_service/pyproject.toml embedding_service/app embedding_service/tests
.venv/bin/python -m pip check
APP_ENV=test DATABASE_URL="$DATABASE_URL" REDIS_URL="$REDIS_URL" .venv/bin/alembic -c backend/alembic.ini upgrade head
APP_ENV=test DATABASE_URL="$DATABASE_URL" REDIS_URL="$REDIS_URL" .venv/bin/alembic -c backend/alembic.ini current
APP_ENV=test DATABASE_URL="$DATABASE_URL" REDIS_URL="$REDIS_URL" .venv/bin/alembic -c backend/alembic.ini check
cd backend
APP_ENV=test DATABASE_URL="$DATABASE_URL" TEST_DATABASE_URL="$TEST_DATABASE_URL" REDIS_URL="$REDIS_URL" TEST_REDIS_URL="$TEST_REDIS_URL" TEST_QDRANT_URL="$TEST_QDRANT_URL" TEST_EMBEDDING_SERVICE_URL="$TEST_EMBEDDING_SERVICE_URL" TEST_EMBEDDING_SERVICE_TOKEN="$TEST_EMBEDDING_SERVICE_TOKEN" ../.venv/bin/pytest
cd ..
HF_HUB_OFFLINE=1 REPOLUME_TEST_MODEL_CACHE=/tmp/repolume-models .venv/bin/pytest embedding_service
.venv/bin/pip-audit --requirement backend/requirements.lock --disable-pip
.venv/bin/pip-audit --requirement embedding_service/requirements.lock --disable-pip
podman build --tag repolume-api:milestone5 backend
podman build --tag repolume-embedding-service:milestone5 embedding_service
podman image inspect --format '{{.Config.User}}' repolume-api:milestone5
podman image inspect --format '{{.Config.User}}' repolume-embedding-service:milestone5
```

The actual Milestone 5 results, including full downgrade/upgrade, Qdrant/real-model pipeline, archive scans, coverage, and failures/fixes, are recorded in `docs/BUILD_STATUS.md`. GitHub Actions repeats strict quality, PostgreSQL 18, Redis, Qdrant 1.18.2, the exact real model, migrations, complete suites, audits, both builds/non-root checks, and immutable-action image scans on Python 3.13. Hosted CI has not run because no remote workflow run exists.

## Incident evidence policy

Incident notes may reference opaque identifiers, timestamps, status transitions, versions, counts, and safe error categories. They must not paste secrets, private repository content, complete chat messages, full prompts, or full provider responses.

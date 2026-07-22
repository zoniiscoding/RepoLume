# RepoLume Operations

**Status:** Milestone 12 repository deployment configuration and runbooks are locally verified. No live GitHub/hosted-LLM acceptance, hosted frontend/API/private service, dashboard, alert, managed backup, restore, rollback, or final deletion drill has been verified because provider access is unavailable.

## Service inventory

| Service | Exposure | Planned owner responsibility | Current state |
| --- | --- | --- | --- |
| React frontend | Public via Vercel | Authenticated UI, safe rendering, repository questions and status | Vercel config/CSP implemented locally; not deployed |
| FastAPI API | Public via Railway | Authenticated API, GitHub webhooks, repository selection/status/questions/manual refresh, bounded three-tool agent, health | Railway manifest/release migration implemented locally; not deployed |
| Indexing worker | Railway private service | Durable jobs, safe static ingestion, embedding/vector validation, atomic activation, cleanup | Role-scoped secrets and graceful drain implemented locally; not deployed |
| Embedding service | Railway private service | Authenticated bounded fixed-model embeddings | Private manifest and digest-pinned non-root image source verified; not deployed |
| PostgreSQL | Neon private credentials | Durable identity, access, delivery/generation, job/build/count/activation/cleanup truth, symbols and call edges | Eight-revision schema through `da6b47f8cd61` verified on disposable PostgreSQL 18; managed production instance not provisioned |
| Redis | Private managed service | Opaque job-ID Stream delivery; later cache/rate-limit support | Redis 8.8 locally verified; managed authenticated TLS service not provisioned |
| Qdrant | Qdrant Cloud authenticated | Installation/repository/version-scoped vectors and private citation chunks | Qdrant 1.18.2 locally verified; managed authenticated service not provisioned |
| Hosted LLM | Server-side provider API | Strict structured tool/final decisions | OpenAI adapter/pinned model configured in code; no real credential or hosted acceptance run |
| GitHub App | GitHub | Authentication, installation tokens, webhooks | Adapter and mocked tests complete; real App not configured or verified |

## Health contract

- `GET /api/v1/health/live` indicates API process liveness only, returns `200 {"status":"ok"}`, and does not probe or reveal configuration/dependencies.
- `GET /api/v1/health/ready` runs bounded PostgreSQL, Redis, and exact Qdrant collection/configuration probes; it returns `200` only when all report `ready`, otherwise a topology-minimizing `503` body.
- Worker startup requires authenticated exact embedding-model readiness, Qdrant collection compatibility, Redis consumer-group setup, and PostgreSQL reconciliation. Ongoing health uses durable heartbeats/process supervision; there is no public worker HTTP endpoint.
- Private `GET /health/live` on the embedding service is dependency-independent. Credentialed `GET /health/ready` returns only load state and fixed model/revision/dimension/normalization/token ceiling; unauthenticated requests return 401.

API responses include a generated or validated `X-Request-ID` and security headers. PostgreSQL, Redis, and Qdrant are API readiness dependencies. The hosted LLM is deliberately not probed by readiness; an LLM outage affects only question requests. API and model liveness remain successful during dependency/model readiness failure.

## Frontend local operation

Install only from the committed lockfile and run the Vite client separately from the API:

```sh
cd frontend
npm ci
npm run dev
```

`VITE_API_BASE_URL` defaults to `http://127.0.0.1:8000/api/v1`. The browser client must use an API whose `FRONTEND_URL` is its exact origin and whose `CORS_ORIGINS` includes that origin. In production both must be HTTPS. The API callback, not the frontend, receives GitHub's OAuth query; after server-side exchange it writes the HTTP-only refresh cookie and redirects only to `<FRONTEND_URL>/auth/callback`. Never add OAuth codes, access tokens, refresh tokens, or private keys to Vite variables: only public API base configuration belongs there.

Before a production process is allowed to start, verify these configuration invariants:

- PostgreSQL uses `postgresql+asyncpg`, managed credentials, a non-local host, and `ssl=require`, `ssl=verify-ca`, or `ssl=verify-full`; Redis uses authenticated `rediss://`.
- CORS and `FRONTEND_URL` are credential-free HTTPS origins. GitHub and enabled Google callbacks use their exact `/api/v1/auth/.../callback` paths and their hosts appear in `TRUSTED_HOSTS`.
- The LLM base is exactly `https://api.openai.com/v1` or `https://generativelanguage.googleapis.com/v1beta/openai`; do not route private evidence through a generic proxy without a code/security review.
- GitHub, enabled Google, RepoLume token, embedding, Qdrant, and LLM credentials come from the platform secret store, meet minimum length, and are not documentation/test placeholders.
- Qdrant and the embedding service use authenticated HTTPS private endpoints. The embedding image has the pinned model preloaded at an absolute cache path and `EMBEDDING_MODEL_LOCAL_FILES_ONLY=true`.

The process refuses unsafe critical settings with a generic configuration error. Do not bypass this validation to recover a deployment.

The frontend build is static output from `npm run build`; deployment/hosting configuration is intentionally not implemented in this milestone. CI runs locked install, `npm ls`, formatting, lint, TypeScript build, Vitest, Chromium-only Playwright, and a high-severity npm audit. Run `npx playwright install chromium` once locally, then `npm run test:e2e`; screenshots/traces/reports remain ignored. Live GitHub and deployed-browser acceptance remain required.

## Logging and metrics contract

Structured logs include request IDs plus safe startup/HTTP metadata and worker job/repository IDs, attempt, stage, counts, model identity, and safe error code. The embedding service logs request ID, document/query kind, count, duration, and safe model state only. Agent processing logs only repository ID, tool name, argument fingerprint, safe counts/timing/status, and timeout/provider category. Logs do not include questions, answers, prompts, repository owner/name/path/content, commit/PR bodies, patches, evidence, or vectors.

Logs must exclude tokens, cookies, secrets, clone credential material, full repository chunks, full prompts/responses, private file contents, and complete chat messages.

Uvicorn access logging is disabled because its raw target can include OAuth codes and unbounded query data. `httpx`, `httpx2`, and `httpcore` INFO logs are suppressed. Milestone 9 verification inspects logs for webhook bodies, source, paths, patches, commit messages, questions/answers/prompts, call expressions, evidence/vectors, infrastructure URLs/keys, credentials, askpass values, and provider bodies. Only allowlisted operational metadata is permitted.

Metrics remain planned: request and tool latency/error rates, job queue age/duration, worker heartbeat/stuck jobs, embedding throughput, vector operations, model token/cost usage, indexing stages, webhook outcomes, and deletion backlog. Names, cardinality limits, alert thresholds, and retention require deployed telemetry.

## Runbooks

The job/Redis procedures below reflect implemented local behavior but have not been exercised on a managed production platform. Exact dashboard links, escalation contacts, recovery objectives, and evidence fields remain deployment work.

Milestone 12 now defines those required production fields and procedures in `docs/DEPLOYMENT_M12.md`, but no dashboard, alert destination, managed backup, provider project, or domain exists from this workspace. Operators must not substitute this source runbook for actual provider evidence.

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
- A job whose `refresh_generation` no longer matches the locked repository is terminally superseded before external work or before activation. Do not manually lower generations or force activation.
- Only one job per repository may be `running`; newer queued jobs are reconciled after the running job exits. PostgreSQL uniqueness and row locks, not process-local locks, are the concurrency boundary.
- `comparison_unavailable`, `non_fast_forward`, `comparison_file_limit`, `changed_bytes_limit`, `unsafe_comparison`, `missing_active_index`, `requested_full_rebuild`, `default_branch_changed`, and `previous_artifact_missing` are visible full-fallback categories, not reasons to guess a delta.
- Access-revoked work becomes `cancelled` before token minting or clone.
- `clone_cleanup_failed` is security-relevant: the job cannot activate or become successfully terminal. Keep the worker volume isolated, verify/destroy the residual workspace through host controls, and retry only after cleanup is trustworthy. Logs intentionally omit the clone path and repository content; do not manually activate the prepared build.
- Static processing exposes `parsing`, `chunking`, and `building_graph` durable stages. File-local malformed/oversized/unsupported cases increment safe categories; repository chunk/call-site overflow and unsafe paths fail closed.
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

Replay tooling must preserve `Content-Type: application/json`, the original raw body, delivery ID, event name, and exact lowercase `sha256=<64 hex>` signature. Supported event families accept only reviewed actions and field combinations; a 400 for an unexpected signed combination is intentional and must be investigated rather than bypassed.

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
2. Return `temporarily_unavailable` with no citations; do not persist the question, evidence, prompt, partial answer, or provider body.
3. Correlate only request ID, repository ID, and the safe error category. Never copy a prompt, key, or provider response into incident notes.
4. Check provider status, API-only secret injection, pinned model availability, bounded concurrency, and retry/rate-limit telemetry. Do not switch to the deterministic test provider in production.
5. Keep liveness/readiness semantics unchanged and prevent retry storms. Verify recovery with a controlled authorized repository before resuming full traffic.

### GitHub history outage or rate limiting

1. Confirm the question route remains authorized and that `search_code` still works for the active index. Do not copy installation tokens, question text, commit messages, PR bodies, or patches into incident notes.
2. Inspect only the safe `github_history_unavailable` category, tool timing, result count, and request ID. History HTTP retries are limited to transient transport, 429, and selected 5xx failures.
3. Expect history-only questions to return a safe non-answer, and mixed questions to return `partially_answered` only when code evidence independently supports the returned claims.
4. Do not add a personal access token, widen repository permissions, bypass the repository-restricted token, or cache installation tokens/history bodies as a workaround.
5. After recovery, run the mocked history client/tool/API tests and a controlled live GitHub App acceptance on a non-sensitive fixture before enabling private traffic.

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

Production Alembic commands use `MIGRATION_DATABASE_URL`, a direct least-privilege connection separate from the pooled API/worker `DATABASE_URL`. The Railway API pre-deploy command runs the migration before health-gated rollout. A failed release command must block deployment; do not inject OAuth, GitHub, model, or browser secrets into the migration process.

The worker registers SIGTERM/SIGINT handlers. A termination request sets the worker stop event, preventing the next delivery loop while the currently claimed durable job continues. Railway's configured drain window is 900 seconds; if the platform kills a longer job, PostgreSQL heartbeat/lease recovery and Redis reconciliation remain authoritative. Start with one worker replica until a managed restart/recovery drill proves the intended semantics.

## Local Milestone 9 procedure

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
export LLM_PROVIDER='openai'
export LLM_API_KEY='<api-only-secret-store-value-at-least-32-characters>'
export LLM_MODEL='gpt-5.4-mini-2026-03-17'
export LLM_PROMPT_VERSION='repolume-grounded-v1'
export FRESHNESS_MAX_CHANGED_FILES=300
export FRESHNESS_MAX_CHANGED_BYTES=67108864
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

Use `LLM_PROVIDER=deterministic` only for automated acceptance under `APP_ENV=test` when no hosted credential is available. It verifies protocol, retrieval, citation, refusal, isolation, and operational behavior; it is not a quality or production substitute. Run the content-free evaluation aggregator with observations produced by the controlled harness:

```sh
PYTHONPATH=backend .venv/bin/python -m app.rag.evaluation \
  --cases backend/evaluation/milestone7_cases.json \
  --observations /path/to/content-free-observations.json
PYTHONPATH=backend .venv/bin/python -m app.indexing.evaluation \
  --cases backend/evaluation/milestone9_cases.json \
  --observations backend/evaluation/milestone9_fixture_observations.json
```

Stop processes with `Ctrl-C`; shutdown closes database, GitHub, Redis, embedding HTTP, Qdrant, and model resources. Docker Compose can start `postgres`, `redis`, `qdrant`, `embedding-service`, `api`, and `worker` after non-empty local secrets and container-addressable URLs are supplied. The embedding image preloads the model during build. Do not commit any credential.

For live GitHub verification, configure the App callback and webhook URLs to the public HTTPS API; grant read-only Metadata, Contents, and Pull requests permissions; subscribe to Installation, Installation repositories, Push, and Repository events; then install it on a controlled test account/organization. Automated tests require no real credentials and use injected mocked responses.

## Milestone 9 verification commands

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
PYTHONPATH=backend .venv/bin/python -m app.rag.evaluation --cases backend/evaluation/milestone7_cases.json --observations /path/to/content-free-observations.json
PYTHONPATH=backend .venv/bin/python -m app.rag.evaluation --cases backend/evaluation/milestone8_cases.json --observations backend/evaluation/milestone8_fixture_observations.json
PYTHONPATH=backend .venv/bin/python -m app.indexing.evaluation --cases backend/evaluation/milestone9_cases.json --observations backend/evaluation/milestone9_fixture_observations.json
```

The actual Milestone 9 results, including migration downgrade/re-upgrade, dependency-backed tests, coverage, signed A-to-B refresh, active-version isolation, health requests, and external-provider limits, are recorded in `docs/BUILD_STATUS.md`. GitHub Actions repeats Python 3.13 quality gates, PostgreSQL/Redis/Qdrant/private-model integration, migrations, complete suites, audits, image builds/non-root checks, and immutable-action scans. Baseline Milestone 8 commit `8f222dd2e9a7675c098cca4bd3687916a99461d3` has successful hosted run `29652564767`; the local Milestone 9 commit is intentionally not pushed, so no Milestone 9 hosted run exists. Local container verification is not retried because the prior contradictory Podman VM/socket state remains a host-runtime block.

### Call-graph validation failure

1. Inspect only repository/job/build IDs, version, stage, graph counts, fingerprint mismatch category, and safe timing. Do not print source, call expressions, questions, or credentials.
2. Keep the prior active build queryable. A failed graph must never be marked ready or active; verify `graph_validated=false` and inactive symbol/edge cleanup state.
3. Confirm parser call-site and process bounds, deterministic symbol IDs, edge counts, commit identity, and migration head. Do not bypass validation or set the flag manually.
4. Retry through a server-authorized manual full reindex after fixing the deterministic cause. Never mutate graph rows or invoke refresh through a model tool.

### Webhook freshness incident

1. Use only delivery ID, internal repository/job IDs, event, bounded ref/SHA prefix, generation, requested/actual mode, status, counts, safe error code, and timing. Never capture the body, token, source, patch, or commit message.
2. Confirm the delivery is unique, the installation/repository remain active, the ref equals the server-owned default branch, and the job generation equals the repository generation.
3. A duplicate needs no new work. `stale`, `superseded`, `ignored`, and `unauthorized` are terminal. `retryable` queue/provider failures remain durable and are recovered by reconciliation within bounded attempts.
4. During a replacement, verify the old `active` build stays queryable and the new build is `building` or `ready`, never returned by question scope. After completion, verify one active build/commit and scoped superseded cleanup.
5. For compare uncertainty or missing prior artifacts, allow the recorded full fallback. Do not manually copy vectors, broaden Qdrant filters, change target branches, or mark a delivery complete.

### Caller-query outage or semantic non-answer

1. `caller_target_ambiguous` or an empty target/result is a semantic insufficient-evidence result. `call_graph_unavailable`, `caller_query_unavailable`, `caller_scope_changed`, and `caller_scope_revoked` are operational/unavailable states.
2. Verify current membership, installation/repository status, repository active version/SHA, active build state, graph fingerprint, and `graph_validated` without widening access or querying another version.
3. Never copy a private call expression or full question into logs/incident notes. Use safe trace failure code, result count, opaque IDs, and request ID only.
4. A pre-Milestone-8 active build intentionally has no validated graph; schedule a normal full re-index rather than fabricating/backfilling caller readiness.

## Incident evidence policy

Incident notes may reference opaque identifiers, timestamps, status transitions, versions, counts, and safe error categories. They must not paste secrets, private repository content, complete chat messages, full prompts, or full provider responses.

The provider-oriented API, worker, dependency, auth, deployment, migration, backup, restore, and secret-rotation procedures required for Milestone 12 are in `docs/RUNBOOKS_M12.md`. They are source-complete but unexercised until production infrastructure and alert ownership exist.

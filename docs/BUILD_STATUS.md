# RepoLume Build Status

**Last updated:** 2026-07-16
**Authorized milestone:** Milestone 5 — private embedding service, Qdrant vector storage, and atomic searchable index versions
**Overall status:** Milestone 5 implementation and local acceptance verification complete
**Production readiness:** Not production-ready. The local searchable indexing data plane is complete, but live GitHub acceptance, hosted CI/deployment, public retrieval/RAG, deletion, quotas/rate limits, backups/restores, telemetry/alerts, and production private-network/capacity controls remain absent or unverified.

## Implemented through Milestone 5

- All Milestone 1–4 foundation, authentication, authorization, webhook, durable job, Redis Stream, safe clone/discovery, isolated Tree-sitter parsing, symbol extraction, and deterministic AST/document chunking behavior.
- Independently deployable private FastAPI embedding service with typed schemas, body/document/count/token/time/concurrency bounds, constant-time bearer authentication, background load/warm-up, dependency-free liveness, authenticated model readiness, graceful shutdown, content-free logs, and UID/GID `10002:10002` image runtime.
- Exact Apache-2.0 `jinaai/jina-embeddings-v2-base-code` model revision `516f4baf13dec4ddddda8631e019b5737c8bc250`, reviewed revision-bound tokenizer artifacts with `tokenizers==0.22.2`, FastEmbed 0.8.0/ONNX CPU inference, 768 L2-normalized dimensions, 8,192-token ceiling, no remote model code, and local-files-only production runtime.
- Central `repolume-embedding-v1` preprocessing that canonicalizes trusted code/document/citation metadata, preserves complete chunk content, distinguishes query/code/document inputs, rejects instead of truncating, and produces stable policy/input SHA-256 fingerprints.
- Typed async worker client with authenticated bounded batches, independent connect/read/write timeouts, retry/permanent classification, exponential backoff with jitter, cancellation propagation, exact result-ID/count/model/revision/dimension/normalization validation, and finite/unit-vector checks.
- Typed Qdrant adapter using Qdrant client 1.18.0 and server 1.18.2. It creates or validates one 768/cosine collection with fixed model metadata and payload indexes, performs bounded idempotent upserts, exact filtered counts/scroll validation, and scoped idempotent deletes.
- Vector payloads contain trusted installation/tenant ID, repository ID, index version, commit SHA, relative path, language, chunk/symbol/parent metadata, exact line range, complete content for later citations, stable chunk hash, preprocessing fingerprints, and model/revision/fingerprint. They contain no GitHub/service/database/Redis/Qdrant credentials.
- Deterministic UUIDv5 point IDs bind installation, repository, index version, path, stable chunk hash, chunk type, and ordinal. Different repositories/versions cannot collide; identical retry inputs overwrite the same points.
- Durable worker stages `embedding`, `storing_vectors`, `validating_index`, and `activating_index`, with PostgreSQL count/model/preprocessing/build/cleanup state and bounded heartbeat/progress updates.
- Exact pre-activation vector count and metadata validation followed by one PostgreSQL transaction that supersedes the prior build, activates the ready build, advances repository version/SHA/vector count/searchability, and completes the job. Failure preserves the prior active version and deletes only trusted inactive scope.
- API repository/detail/status responses expose safe stage/progress/active SHA/version/job/active-vector counts/searchability/failure category. They expose no source, chunk, vector, credential, or Qdrant topology.
- API readiness now checks PostgreSQL, Redis, and exact Qdrant collection compatibility. Worker startup additionally requires exact authenticated embedding-model readiness.
- Python 3.13 production and development locks for both backend and embedding components, supported Debian 13 runtime images, pinned Qdrant Compose server, and immutable Anchore scan-action reference.

## Database and migration

Alembic revision `d06a6455fcd7` follows `f9389ed2964e` and adds:

- `repository_index_builds`, unique by repository/version and job, with build/cleanup lifecycle, commit/model/revision/dimension/preprocessing identity, expected/embedded/vector/failed/skipped counts, activation/cleanup timestamps, and nonnegative/exact-ready-count constraints;
- a partial unique index allowing only one `active` build per repository;
- job target version, embedding/vector counts, and model/preprocessing metadata;
- repository `active_vector_count` with a nonnegative constraint.

PostgreSQL remains the active-version source of truth; vector arrays are not stored there. The complete five-revision chain was downgraded to base and upgraded to `d06a6455fcd7 (head)` against real disposable PostgreSQL, followed by `current` and drift-free `alembic check`.

## Acceptance evidence

| Gate | Actual result |
| --- | --- |
| Runtime | Python 3.13.14 locally and in both production images; CI selects Python 3.13 |
| Services | Real local PostgreSQL 18, Redis 8.8, Qdrant 1.18.2, private embedding service, API, and separate worker process |
| Locks | Four Python 3.13 hash lock regeneration dry-runs exited 0; `pip check` reported no broken requirements |
| Dependency audit | Backend and embedding production locks each reported `No known vulnerabilities found` |
| Quality | Backend: 95 files formatted, Ruff passed, strict mypy passed 94 files. Embedding: 13 files formatted, Ruff passed, strict mypy passed 13 files |
| Backend suite | 166 passed, 0 failed, 0 skipped in 7.36 seconds; 90.76% coverage |
| Embedding suite | 15 passed, 0 failed, 0 skipped in 1.68 seconds; 92.24% coverage, including the real immutable model |
| Migrations | Full downgrade to base, upgrade through all five revisions, `current` at `d06a6455fcd7`, and `alembic check` all passed |
| Controlled indexing | Real PostgreSQL/Redis/Qdrant/private model plus API TestClient and the production worker class indexed a controlled local Git fixture: authenticated batches, inactive vectors, exact validation, atomic activation, searchable status, opaque Redis payload, inert executable source, and empty clone root all passed |
| Replacement behavior | Failed replacement preserved the prior active version; successful replacement activated first and then removed only the superseded vector scope; the order-dependent regression pair passed 2/2 |
| Isolation/idempotency | Real Qdrant tests passed repository/version filtered counts and deletes, deterministic/repeated upserts, metadata validation, scope mismatch rejection, configuration mismatch rejection, and no cross-scope exposure |
| Startup/health | Separate worker logged `worker_started`; API liveness and PostgreSQL/Redis/Qdrant readiness returned HTTP 200; embedding liveness/authenticated readiness returned 200 and unauthenticated readiness returned 401 |
| Containers | Backend/API/worker and embedding images built on `python:3.13.14-slim-trixie`; users are `10001:10001` and `10002:10002`; Python 3.13.14, fixed Git, and worker import were verified |
| Image scan | Checksum-verified Grype 0.112.0 OCI-archive scans exited 0 for fixed High/Critical findings after moving from unsupported Debian 12 to Debian 13 and applying the exact documented CPython 3.13.14 non-reachability rule |
| Logs/secrets | Actual API/worker/embedding logs contained only allowlisted IDs, states, counts, durations, model identity, and safe categories; automated sentinels and final secret-pattern inspection found no real credential or repository-source leak |
| Hosted CI | Three-job workflow covers all strict checks, real services/model, migrations/tests/audits, both image builds/non-root checks, and two immutable-action image scans; no hosted run exists yet, so only the relevant workflow was reproduced locally |

## Failures encountered and fixed

1. The first complete backend run exposed an order-dependent `IntegrityError` during replacement activation: SQLAlchemy could flush the new `active` build before the prior row became `superseded`. The prior row is now explicitly flushed inside the same transaction first; the paired regression and complete suite pass.
2. A later complete run used a different disposable embedding bearer value from the still-running local service. Authentication correctly failed. The service was restarted with one consistent test value; the focused real-model pipeline and complete suite passed.
3. Initial lock consistency commands used an unwritable default pip-tools cache and then sandbox-blocked DNS. They were rerun with `/private/tmp/repolume-pip-tools` and approved network access; all four dry-runs exited 0.
4. The first container scan found the `bookworm` base past standard support and a new CPython advisory. Both images moved to the explicit supported `3.13.14-slim-trixie` base and were rebuilt. `CVE-2026-15308`, published after Python 3.13.14, is temporarily suppressed only for the exact Python binary/version because the affected `html.parser` code is outside these services' execution path; the exception is documented and must be removed on the next fixed 3.13 release.
5. Direct scanner-container access to the Podman control socket was rejected as excessive privilege. Images were exported as disposable OCI archives and scanned with the official checksum-verified native Grype binary instead.
6. Two exploratory embedding health requests used `/live` and `/ready` and correctly returned 404. The documented `/health/live` and `/health/ready` routes were then called and returned the expected 200/200/401 results.
7. Final code review found that the worker client classified read/write timeouts but not every HTTPX connection/pool timeout subtype. It now handles every `httpx.TimeoutException`; five explicit connection/timeout regressions were added and the complete suite passes.
8. A final 166-test run initially reported 165 passed and one duplicate-delivery failure because the separately running startup-verification worker was consuming the same disposable Redis database while the suite reset its consumer group. The live worker was stopped and the isolated complete suite passed 166/166.
9. Several verification commands failed before exercising application behavior: the local image tags were `:m5`, not `:milestone5`; pinned constants live in `app.constants`, not `app.model`; the first Alembic rerun omitted required `REDIS_URL`; and the first worker command used the root virtualenv path from `backend/`. Each command was corrected and its intended check then passed.

## External configuration still required

- A real least-privilege GitHub App and controlled live OAuth/installation-token clone/webhook/index acceptance.
- Managed credentialed PostgreSQL, authenticated TLS Redis, authenticated HTTPS Qdrant, and private authenticated embedding-service networking in platform secret stores.
- Hosted CI, deployment, image registry/digest policy, production capacity/load tests, telemetry/alerts, backups/restores, and incident drills.
- Removal of the exact Python 3.13.14 image-scan exception when the upstream fix is released.

## Current limitations

- Searchable means the active vector version is complete and authorized; there is deliberately no public semantic retrieval, evidence API, LLM, agent, repository chat, or frontend yet.
- Qdrant contains private chunk text for future citations. Full repository deletion/purge and retained backup policy remain Milestone 9/later work; access revocation blocks API use immediately but does not yet implement full cross-store deletion.
- Webhook-triggered automatic reindex scheduling remains disconnected. Initial selection/manual durable jobs are the verified path.
- Static Python analysis and embeddings cannot establish runtime behavior, dynamic dispatch, reflection, generated code, dependency semantics, or historical intent.
- Live GitHub and hosted production behavior are not claimed. The controlled fixture injects mocked GitHub authorization and a controlled cloner while using the real database, queue, vector store, model service, and production worker orchestration.

## Production-readiness statement

Milestone 5 is a tested local searchable-indexing foundation, not a production SaaS. Do not provide production GitHub credentials or private repository traffic until live provider acceptance, hosted CI/deployment, deletion, monitoring, backup/restore, rate/usage controls, and remaining security gates are complete.

## Next milestone

Milestone 6 — semantic retrieval and grounded evidence plumbing. It has not started and requires explicit authorization.

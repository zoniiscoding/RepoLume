# RepoLume Build Status

**Last updated:** 2026-07-16
**Authorized milestone:** Milestone 3 — Durable jobs and safe cloning
**Overall status:** Milestone 3 implementation and local acceptance verification complete
**Production readiness:** Not production-ready. The durable clone/discovery boundary exists, but live GitHub credentials, hosted CI/deployment, parsing, embeddings, vectors, retrieval, agents, chat, frontend, rate limits, backups, and production operations remain absent or unverified.

## Implemented through Milestone 3

- All Milestone 1 foundation and Milestone 2 GitHub authentication, session, authorization, installation, and webhook behavior.
- Authenticated repository selection/list/detail/status endpoints:
  - `POST /api/v1/repositories`
  - `GET /api/v1/repositories`
  - `GET /api/v1/repositories/{repository_id}`
  - `GET /api/v1/repositories/{repository_id}/status`
- Selection rechecks current user membership, active installation state, and the current installation repository list before committing work.
- PostgreSQL-first idempotent job creation. Repository row locking plus a partial unique index prevents concurrent active jobs; repeated initial selection returns the same initial job.
- Redis 8 Streams at-least-once delivery containing only `job_id`. PostgreSQL remains the source of status, attempt, stage, heartbeat, retry, error, and discovery summary state.
- A separate private worker that reloads the job/repository/installation/membership, claims conditionally, heartbeats, persists progress, classifies safe errors, retries only retryable failures with bounded exponential backoff and jitter, recovers abandoned work, and treats duplicate delivery as a no-op.
- Fixed `github.com` shallow single-branch clone using an absolute Git executable, disabled system/global config, hooks, templates, submodules, file/ext protocols, prompts, redirects through arbitrary hosts, and LFS smudge behavior.
- Short-lived GitHub installation tokens exist only in the worker process environment through a private askpass helper. They never appear in Git argv, Redis, PostgreSQL, responses, or logs.
- Per-process CPU/address-space/file-size/file-descriptor limits, a 120-second clone timeout, a 500 MiB clone ceiling, a fresh mode-0700 temporary workspace, and unconditional cleanup.
- Non-following file discovery with root containment and symlink-escape rejection; ignored dependency/build/cache directories; supported text/Python/docs/config allowlist; binary, unsupported, and oversized-file skipping; 20,000-file and 250 MiB discovery ceilings; persisted counts only, never contents.
- Readiness now requires both PostgreSQL and Redis. Liveness remains dependency-free.
- Python `redis==8.0.1` is pinned in both hashed locks; Redis 8.8 is the verified local/CI service baseline.
- Compose includes PostgreSQL, Redis, non-root API, and non-root worker services. The backend image includes Git and continues to run as UID/GID `10001:10001`.

## Database and migration

Alembic revision `94b0f7ce7782` follows `f8eba5464d8c` and adds to `indexing_jobs`:

- `locked_by`, `next_attempt_at`, and `last_enqueued_at` for conditional claims, delayed retry, recovery, and reconciliation;
- non-negative `discovered_file_count` and `discovered_total_bytes`;
- content-free `skipped_files_json` category counts;
- a due-job index on status/next-attempt;
- a PostgreSQL partial unique index allowing at most one queued/running/retrying job per repository.

No repository file paths, file contents, installation tokens, or clone credentials are persisted.

## Acceptance evidence to date

| Gate | Actual result |
| --- | --- |
| Runtime | Python 3.13.14 local baseline |
| PostgreSQL | Disposable PostgreSQL 18.4; migration applied successfully; no SQLite |
| Redis | Disposable Redis 8.8.0; real Stream delivery and consumer-group tests |
| Migrations | Full downgrade to base and clean upgrade through `94b0f7ce7782 (head)` succeeded; `alembic check` reported no new operations |
| Tests | Final complete run: 111 passed, 0 failed, 0 skipped in 2.75 seconds; 93.45% branch-aware coverage |
| Controlled repository | A real local Git fixture was shallow-cloned by the separate worker, discovered, and deleted; a deliberate Python execution marker was not created |
| API/worker live flow | Real API and separate worker processes with PostgreSQL/Redis: POST returned in 25.91 ms before worker start; one job; retry then completion at attempt 2; live/ready HTTP 200; both processes exited 0 |
| API latency/idempotency | PostgreSQL/Redis integration test proves repeated selection creates one job; concurrent claim test allows one winner; Redis payload contains only `job_id` |
| Retry/recovery/revocation | Retry exhaustion, permanent terminal failure, abandoned recovery, duplicate delivery, cross-user denial, and pre-clone installation suspension passed |
| Security limits | Argument injection, invalid identity/branch, timeout, repository/file/file-count/total-byte limits, binary/type filters, symlink escape, cleanup, and credential-in-argv tests passed |
| Quality/dependencies | Ruff format/lint, strict mypy, and `pip check` passed; `pip-audit` reported no known production-lock vulnerabilities |
| Container | Podman built Python 3.13.14/Git image; API and worker ran UID 10001, readiness returned 200 for PostgreSQL+Redis, and sensitive log scan passed |
| Hosted CI | Workflow updated for Python 3.13, PostgreSQL 18, Redis 8.8, locks, migrations, tests, audit, and non-root container build; no hosted run exists yet |

## Failures encountered and fixed

1. Lock generation first failed because the sandbox could not resolve PyPI and its default pip-tools cache was not writable. It was rerun with an isolated temporary cache and approved network access; both hashed locks were generated successfully.
2. The disposable PostgreSQL cluster initially restarted on port 5432 instead of its isolated port. It was stopped and restarted explicitly on `127.0.0.1:55432`; subsequent migration/test connections succeeded.
3. Existing readiness tests failed after Redis became a required readiness dependency. An injected fake queue was added to unit construction and expected readiness contracts were updated.
4. The first new test helper left its fake timeout process waiting after cancellation. The fake process now models termination correctly; the focused clone/worker suite dropped from about 60 seconds to sub-second execution.
5. Strict Ruff/mypy surfaced import, exception-boundary, async filesystem, Redis typing, and mock return annotations. Causes were fixed without lowering the quality or coverage gates.
6. The first Podman worker-container run rejected Docker-style tmpfs `uid`/`gid` mount options. The Compose/verification mount now uses a writable sticky tmpfs; each clone immediately creates its own mode-0700 workspace. The rerun passed with both API and worker at UID 10001.

## External configuration still required

- A real least-privilege GitHub App with the documented callback/webhook URLs, read-only metadata/contents/pull-request permissions, and installation on a controlled account.
- Managed PostgreSQL and authenticated TLS Redis (`rediss://`) credentials in platform secret stores.
- Private worker networking/egress restricted to GitHub, PostgreSQL, Redis, DNS, and required telemetry; no public worker endpoint.
- Hosted CI execution, image vulnerability scanning/digest pinning, deployment, backups/restore drills, alerting, capacity tests, and live GitHub clone/revocation acceptance.

## Current limitations

- No live GitHub authentication, installation-token clone, or webhook delivery was claimed; automated tests use mocked GitHub API responses and an operator-controlled local Git fixture.
- Milestone 3 completes only safe acquisition and discovery. `discovery_complete` does not mean a searchable index exists; repository status remains `not_indexed`.
- No Tree-sitter, chunking, symbol extraction, call graph, embeddings, Qdrant, LLM, agents, repository chat, frontend, or connected-repository code execution exists.
- Redis is delivery/coordination only. Losing Redis does not lose PostgreSQL jobs; reconciliation re-enqueues eligible jobs after recovery.

## Production-readiness statement

Milestone 3 is a tested local foundation for later static indexing. RepoLume is not ready for production credentials or private repository traffic. A real GitHub App acceptance pass and the remaining security, data-plane, deployment, and operations milestones are still mandatory.

## Next milestone

Milestone 4 — Python parsing and chunking. It has not started and requires explicit authorization.

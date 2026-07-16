# RepoLume Build Status

**Last updated:** 2026-07-16
**Authorized milestone:** Milestone 1 — Monorepo and backend foundation
**Overall status:** Milestone 1 implementation and local acceptance verification complete
**Production readiness:** Not production-ready; the foundation is runnable, but authentication, authorization services, GitHub integration, indexing, retrieval, frontend, deployment, and production operations are intentionally absent

## Baseline and scope

The workspace began as an empty, non-Git directory. Milestone 0 created the durable specification and engineering documents. Milestone 1 initialized Git and added the production-oriented backend foundation without implementing Milestone 2 behavior.

The following are deliberately out of scope and remain unimplemented: GitHub OAuth, GitHub App installation synchronization, repository access, webhook processing, Redis/ARQ workers, repository cloning, Tree-sitter parsing, embeddings, Qdrant, agent tools, chat/product endpoints, and frontend functionality.

## Implemented in Milestone 1

- Monorepo root configuration, safe environment example, ignore rules, Docker Compose baseline, README, and Python 3.13 production baseline.
- FastAPI application factory, versioned router, lifespan cleanup, liveness, and PostgreSQL readiness.
- Pydantic Settings validation with secret-valued database configuration and strict production invariants.
- Structured JSON/console logging, validated/generated request IDs, content-minimizing request completion logs, and disabled Uvicorn access logs.
- Shared error envelope, sanitized validation failures, opaque internal errors, restricted CORS/trusted hosts, and API security headers.
- SQLAlchemy 2 async engine/session lifecycle with `asyncpg` and PostgreSQL-only configuration.
- Foundational relational models for users, installations/memberships, repositories, jobs, chat, usage, symbols, call edges, and webhook-delivery idempotency.
- Generated Alembic revision `d2eea490eb59` with complete upgrade and downgrade operations.
- Hashed production and development dependency lockfiles generated under Python 3.13.
- Ruff formatting/linting, strict mypy, pytest/pytest-asyncio, branch coverage, migration consistency checks, and dependency audit configuration.
- GitHub Actions jobs for Python 3.13/PostgreSQL 18 quality and test checks plus non-root backend image verification; Dependabot configuration for pip, Actions, and Docker.
- Non-root Python 3.13 container using locked production dependencies.

## Acceptance evidence

| Gate | Actual result |
| --- | --- |
| Python production baseline | Python 3.13.14 used for lock generation, static checks, migration, tests, and host application startup |
| PostgreSQL baseline | Disposable PostgreSQL 18.4 server used; no SQLite substitution |
| Clean migration | `alembic upgrade head` succeeded from an empty database to `d2eea490eb59` |
| Migration consistency | `alembic check` reported `No new upgrade operations detected.` |
| Tests | 48 passed in 0.58 seconds: 43 unit and 5 PostgreSQL integration; 98.12% branch-aware coverage |
| Formatting/lint/type checking | Ruff format, Ruff lint, and strict mypy passed |
| Host application | Uvicorn started and shut down cleanly; live and ready returned HTTP 200 through actual curl requests |
| Dependency audit | `pip-audit` reported `No known vulnerabilities found` for the production lock |
| Container | Podman 6.0.1 built the image; configured runtime user was `10001:10001`; container live and PostgreSQL ready returned HTTP 200 |
| Log safety | Host/container startup and request logs were inspected; they contained no database URL, credential, cookie, token, prompt, or private content |
| Secret scan | Repository pattern scan found no token/private-key signatures; `.env` and virtual environments are ignored |
| CI | Workflow syntax and local equivalents are verified; GitHub-hosted Actions has not run because no remote repository/run exists |

The first unit run exposed an invalid non-ASCII HTTP-header test input that the HTTP client correctly rejected before the app; the test was changed to an ASCII value that reaches request-ID validation. The first full integration run exposed an Alembic test harness calling `asyncio.run()` from an active event loop; the migration test was made synchronous and the async inspection isolated. The final complete suite passed without skipped tests.

## Schema and tenancy groundwork

Application primary keys are UUIDv4. External GitHub IDs have uniqueness constraints. Foreign keys and deletion behavior are explicit. Status fields use string enums backed by database check constraints. Repository/index-version composite constraints prevent call edges from pointing across a repository or index version. Indexes cover installation membership, repository ownership, job lookup, chat ownership, usage aggregation, symbol lookup, call-edge traversal, and webhook delivery identity.

These relations are necessary groundwork, not an authorization implementation. No protected operation is exposed, and a client-supplied identifier is not treated as proof of access.

## Current risks and limitations

- There is no authentication or server-side authorization service yet, so the API exposes health only.
- GitHub App credentials, callbacks, installation state, revocation, webhook verification, and repository selection do not exist.
- No worker, queue, clone sandbox, parser, embedding model, vector store, LLM, retrieval, or frontend exists.
- The container base tag is version-constrained but not digest-pinned; release-time image pinning and scanning remain Milestone 12 work.
- CI exists but has not executed on GitHub-hosted runners; local commands reproduced both workflow jobs.
- No staging/production service, managed database, backups, restore drill, monitoring, alerting, or deployment evidence exists.
- The schema has not yet been exercised with production-scale data or concurrency.

## Production-readiness statement

Milestone 1 meets its foundation acceptance gates and is suitable as the base for the next authorized milestone. RepoLume as a SaaS is not production-ready and must not receive GitHub credentials, private repository data, or public traffic.

## Next milestone

Milestone 2 — Authentication and GitHub App installation access. It has not started and requires explicit authorization.

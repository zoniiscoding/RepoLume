# RepoLume Security

**Status:** Milestone 1 foundation controls implemented and locally verified. Repository access and product security controls remain gated to later milestones.

## Security invariants

1. A connected repository is untrusted input and is never executed.
2. No repository data is available without current server-side authorization.
3. Every vector operation is scoped to one repository and its active index version.
4. Retrieved content can supply evidence but can never supply instructions.
5. Secrets and private content are excluded from logs, errors, images, and client-visible traces.
6. Revocation blocks use immediately; deletion is a verified asynchronous purge.
7. Production readiness cannot be claimed until controls have implementation and executed evidence.

Milestone 1 introduces no repository connector, protected product route, credential flow, worker, parser, vector operation, model call, or frontend. Its callable surface is limited to health endpoints.

## Primary assets

- GitHub identities, installation access, and private repository contents.
- OAuth, refresh, JWT, webhook, provider, database, Redis, Qdrant, and internal-service secrets.
- Tenant boundaries in PostgreSQL and Qdrant.
- Chat history, retrieval evidence, symbols, call edges, and usage records.
- Queue integrity and index-version activation state.

Only schema groundwork for these assets exists today. No GitHub, repository, chat, vector, or model data has been ingested.

## Threat actors and entry points

- An authenticated user attempting cross-tenant access.
- An unauthenticated internet client targeting public API or webhook endpoints.
- A malicious connected repository containing path, parser, prompt-injection, resource-exhaustion, or credential-exfiltration payloads.
- Malicious GitHub metadata, commit messages, pull-request content, or user chat messages.
- Compromised or misconfigured external services and leaked credentials.
- Model output attempting to alter repository scope, call unapproved tools, or create unsafe links.

The implemented public surface is only `/api/v1/health/live` and `/api/v1/health/ready`. The planned frontend, webhook, worker, embedding, Redis, and administrative interfaces do not exist.

## Control checklist

Legend: **Verified foundation** means the Milestone 1 subset was implemented and passed its listed checks; it does not imply the later complete control exists.

| Area | Required control | Status | Evidence or remaining milestone |
| --- | --- | --- | --- |
| Authentication | OAuth state, server-side exchange, short access token, hashed rotating refresh token, reuse detection | Planned | Milestone 2 |
| Browser security | Restricted CORS, trusted hosts, HTTPS/HSTS; later secure token/cookie/origin/CSRF behavior | Verified foundation | `test_http_foundation.py`; token/cookie controls Milestone 2 |
| Authorization | Join actor through active membership, installation, repository, and session | Planned | Milestone 2 cross-tenant tests |
| Revocation | Fail closed on installation suspension/deletion or repository removal | Planned | Milestones 2 and 9 |
| Webhooks | HMAC before parsing, delivery-ID idempotency, fast durable queueing | Schema only | Content-free uniqueness groundwork; processing Milestone 2 |
| SSRF | GitHub-derived identity, approved hosts/schemes, no arbitrary/model-directed fetch | Planned | Milestones 2 and 3 |
| Clone isolation | Fixed shallow clone, no submodules/hooks, askpass, limits, cleanup | Planned | Milestone 3 |
| Filesystem | Fresh temporary root, traversal/symlink rejection, file/count/byte/type limits | Planned | Milestone 3 |
| Code execution | No import, eval, exec, install, build, test, service, plugin, or config evaluation of connected code | Policy active; implementation absent | `AGENTS.md`; repository handling starts Milestone 3 |
| Prompt injection | Structured escaped data and exactly three read-only analysis tools | Planned | Milestones 6–8 |
| Vector isolation | Mandatory repository and active-version filters | Planned | Milestone 5 |
| Index integrity | Inactive build, explicit activation, rollback/cleanup | Schema groundwork | Milestone 5 |
| Database | Async parameterized ORM, FKs, unique/check/composite constraints, Alembic | Verified foundation | PostgreSQL migration/model/integration tests; production least privilege later |
| API abuse | Request/body limits, rate/usage controls, mutation idempotency | Planned | Milestones 2 and 13 |
| Error safety | Stable envelope, sanitized validation issues, hidden internal messages, request correlation | Verified foundation | `test_http_foundation.py` |
| Output safety | Sanitized Markdown and safe links/attributes | Planned | Milestone 10 |
| Secrets | Environment stores, secret-valued DB URL, redacted settings, no raw secrets in logs/errors/examples | Verified foundation | Config/log/error tests, manual logs, repository scan |
| Observability | Structured IDs/timing without prompts, chunks, cookies, tokens, or secrets | Verified foundation | `test_logging.py`, request middleware tests, manual host/container logs |
| Containers | Hashed install and non-root runtime; later digest pin, capabilities, scanning | Verified foundation | Local Podman image build/user/startup; Milestone 12 hardening |
| Headers | API CSP, production HSTS, nosniff, frame, referrer, permissions policy | Verified foundation | `test_http_foundation.py`, live curl headers |
| Deletion | Durable retryable purge across all stores | Planned | Milestone 9 |
| Supply chain | Locked dependencies, Dependabot, audit, minimal workflow permissions | Verified foundation | Hashed locks, `pip-audit`, CI inspection; hosted CI run pending |

## Implemented Milestone 1 controls

- `DATABASE_URL` is a Pydantic `SecretStr`; safe configuration summaries never contain it.
- Configuration accepts only `postgresql+asyncpg` and applies stricter production validation: non-local credentialed database, JSON logs, disabled interactive docs, explicit trusted hosts, and HTTPS CORS origins.
- Request IDs are accepted only when they match the bounded allowlist; invalid values are replaced with a generated UUID and never reflected into logs as untrusted text.
- Validation error details contain locations, messages, and error types but omit raw request values. Internal exceptions produce a stable generic response and log the exception class, not its message.
- Request logs contain method, route path, status, duration, and request ID. Uvicorn access logs are disabled to avoid unbounded query strings and headers.
- Security headers are applied centrally. HSTS is enabled in production only so local HTTP development remains usable.
- SQLAlchemy uses bound expressions and explicit async session scopes. The generated PostgreSQL migration includes foreign keys, uniqueness, checks, and tenant/version-relevant indexes.
- Cross-repository/index-version call edges are rejected by a composite foreign key to symbol identity and scope.
- Production and development dependencies are locked with hashes; the production lock passed dependency audit.
- The container runs as UID/GID `10001:10001`; its startup and request logs were inspected during real PostgreSQL-backed health requests.
- `.env`, virtual environments, caches, and local database artifacts are ignored. `.env.example` contains variable names, documentation, and blank secret fields only.

## Connected-repository sandbox rules

Allowed future operations are identity validation, access verification, fixed safe Git clone, bounded byte/text reads, static syntax parsing, embedding, static relationship construction, approved GitHub metadata retrieval, and cleanup.

Forbidden operations include executing repository files or commands; dynamic imports; `eval`/`exec`; installing dependencies; running package scripts, tests, Makefiles, Dockerfiles, hooks, plugins, or services; evaluating configuration; following embedded instructions; and constructing shell commands from repository-controlled data.

Git clone credentials must use a short-lived mechanism such as an askpass helper. Credentials must not appear in process arguments, persistent remotes, exceptions, or logs. That implementation is not present in Milestone 1.

## Prompt-injection boundary

Source, documentation, commits, issues, pull requests, and tool results will be serialized as escaped data inside explicit metadata delimiters. They cannot alter system instructions, tools, identity, tenant filters, or destinations. The future model will be offered only `search_code`, `get_history`, and `find_callers`. None of those tools or any model integration exists yet.

## Data classification and logging

Private repository text, chat content, prompts, full model responses, cookies, tokens, and secrets are sensitive and must not be logged. Structured logs may include opaque actor, installation, repository, session, job, and request IDs; route; status; duration; stage; tool name; result count; and safe error code.

The Milestone 1 manual host and container logs contained startup state, counts, request IDs, methods, paths, statuses, and durations only. No database URL, credential, cookie, token, prompt, response, or repository content was observed.

## Security verification policy

Every milestone updates this file with implementation and executed evidence. Milestone 11 will audit every requirement using the matrix: requirement, implementation path, test path, executed result, and remaining limitation. A document-only plan does not count as a control.

## Current security posture

The verified foundation reduces configuration, error, log, database, HTTP-header, dependency, CI, and container risks. It is not a complete product security boundary. RepoLume must not receive GitHub credentials, private repository data, or public production traffic until authentication, authorization, revocation, webhook, repository-isolation, deployment, and operational controls are implemented and verified in their authorized milestones.

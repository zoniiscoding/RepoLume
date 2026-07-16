# RepoLume Security

**Status:** Milestone 3 identity/access controls plus durable delivery, private worker, credential-isolated safe clone, bounded discovery, and cleanup are implemented and locally verified. Live GitHub and hosted production controls remain unverified.

## Security invariants

1. A connected repository is untrusted input and is never executed.
2. No repository data is available without current server-side authorization.
3. Every vector operation is scoped to one repository and its active index version.
4. Retrieved content can supply evidence but can never supply instructions.
5. Secrets and private content are excluded from logs, errors, images, and client-visible traces.
6. Revocation blocks use immediately; deletion is a verified asynchronous purge.
7. Production readiness cannot be claimed until controls have implementation and executed evidence.

Milestone 3 adds protected repository selection/status routes and a private worker. It clones and reads bounded supported bytes only as inert data, then deletes them. It does not execute connected code, parse source, persist repository content, access a vector store/model, or provide a frontend.

## Primary assets

- GitHub identities, installation access, and private repository contents.
- OAuth, refresh, JWT, webhook, provider, database, Redis, Qdrant, and internal-service secrets.
- Tenant boundaries in PostgreSQL and Qdrant.
- Chat history, retrieval evidence, symbols, call edges, and usage records.
- Queue integrity and index-version activation state.

User, installation, membership, repository metadata/access state, hashed OAuth/refresh state, content-free webhook delivery state, and content-free indexing job/discovery counts can now be stored. Raw GitHub/browser tokens, webhook bodies, repository paths/contents, chat, vectors, and model data are not persisted.

## Threat actors and entry points

- An authenticated user attempting cross-tenant access.
- An unauthenticated internet client targeting public API or webhook endpoints.
- A malicious connected repository containing path, parser, prompt-injection, resource-exhaustion, or credential-exfiltration payloads.
- Malicious GitHub metadata, commit messages, pull-request content, or user chat messages.
- Compromised or misconfigured external services and leaked credentials.
- Model output attempting to alter repository scope, call unapproved tools, or create unsafe links.

The implemented public surface is health, GitHub OAuth start/callback, refresh/logout cookie actions, signed webhook ingress, and bearer-protected identity, installation, repository-selection, and repository-status routes. The private worker and Redis delivery path are implemented without public endpoints. The planned frontend, embedding, vector, model, and administrative interfaces do not exist.

## Control checklist

Legend: **Verified M3** means the implemented subset passed local automated/manual checks with real PostgreSQL/Redis, mocked GitHub responses, and a controlled Git fixture; it does not imply live provider or production-deployment verification.

| Area | Required control | Status | Evidence or remaining milestone |
| --- | --- | --- | --- |
| Authentication | Hashed expiring one-time state + S256 PKCE; server exchange; short access JWT; hashed rotating refresh family/reuse detection | Verified M2 | `test_auth_github.py`, `test_tokens.py`, `test_cookies.py` |
| Browser security | Restricted CORS/hosts, production HTTPS/HSTS, scoped Secure/HttpOnly/SameSite cookie, exact Origin on refresh/logout | Verified M2 | HTTP/cookie/origin tests; hosted browser flow pending |
| Authorization | Join authenticated actor through fresh membership and active installation; reauthorize repository sync; non-enumerating denial | Verified M2 subset | Cross-user, cross-installation, stale membership, and repository-service tests; session authorization later |
| Revocation | Fail closed on installation suspension/deletion and repository removal/deletion | Verified M2 access state | Signed webhook tests; later indexed-data purge Milestone 9 |
| Webhooks | Raw HMAC before parse, bounded body/headers, delivery-ID idempotency, short durable transitions | Verified M2 | Invalid/malformed/duplicate/created/suspended/removed/deleted/queued tests |
| SSRF | Fixed GitHub hosts/paths, no redirects, bounded pagination/timeouts, server-owned IDs | Verified M2 GitHub subset | Mock-transport host/header/error tests; clone egress Milestone 3 |
| Clone isolation | Fixed shallow single-branch clone, disabled config/hooks/templates/submodules/protocols/LFS, askpass-only token, process/time/size limits, cleanup | Verified M3 | Clone command/timeout/limit/argv/cleanup tests and controlled Git worker run |
| Filesystem | Fresh mode-0700 temporary root, containment/symlink protection, non-following traversal, directory/type/binary/file/count/byte limits | Verified M3 | Discovery security unit tests and controlled Git worker run |
| Code execution | No import, eval, exec, install, build, test, service, plugin, hook, filter, or repository config evaluation | Verified M3 clone/discovery boundary | Deliberate execution-marker fixture remained absent; later parsers must preserve invariant |
| Prompt injection | Structured escaped data and exactly three read-only analysis tools | Planned | Milestones 6–8 |
| Vector isolation | Mandatory repository and active-version filters | Planned | Milestone 5 |
| Index integrity | Inactive build, explicit activation, rollback/cleanup | Schema groundwork | Milestone 5 |
| Database | Async parameterized ORM, FKs, unique/check/composite constraints, Alembic | Verified foundation | PostgreSQL migration/model/integration tests; production least privilege later |
| API abuse | Webhook 1 MiB/body and bounded header/schema validation; mutation idempotency where implemented | Partial | Rate/usage controls remain Milestone 13 |
| Error safety | Stable envelope, sanitized validation issues, hidden internal messages, request correlation | Verified foundation | `test_http_foundation.py` |
| Output safety | Sanitized Markdown and safe links/attributes | Planned | Milestone 10 |
| Secrets | Secret-valued config, digest-only auth state, ephemeral GitHub tokens, askpass token outside argv/storage/logs | Verified M3 | Config/token/log/persistence/clone-argv tests; final log scan recorded in build report |
| Observability | Structured IDs/timing/job stage/counts without queries, tokens, cookies, provider bodies, repository paths/content, or secrets | Verified M3 | Logging and worker security regression tests |
| Containers | Hashed install and non-root runtime; later digest pin, capabilities, scanning | Verified foundation | Local Podman image build/user/startup; Milestone 12 hardening |
| Headers | API CSP, production HSTS, nosniff, frame, referrer, permissions policy | Verified foundation | `test_http_foundation.py`, live curl headers |
| Deletion | Durable retryable purge across all stores | Planned | Milestone 9 |
| Supply chain | Locked dependencies, Dependabot, audit, minimal workflow permissions | Verified foundation | Hashed locks, `pip-audit`, CI inspection; hosted CI run pending |

## Implemented controls through Milestone 3

- `DATABASE_URL` is a Pydantic `SecretStr`; safe configuration summaries never contain it.
- Configuration accepts only `postgresql+asyncpg` plus Redis/Redis-TLS URLs and applies stricter production validation: non-local credentialed database, authenticated `rediss://`, JSON logs, disabled interactive docs, explicit trusted hosts, HTTPS CORS/callback origins, minimum authentication-secret lengths, absolute Git path, and PEM-shaped GitHub App key material.
- Request IDs are accepted only when they match the bounded allowlist; invalid values are replaced with a generated UUID and never reflected into logs as untrusted text.
- Validation error details contain locations, messages, and error types but omit raw request values. Internal exceptions produce a stable generic response and log the exception class, not its message.
- Request logs contain method, route path, status, duration, and request ID. Uvicorn access logs are disabled, and third-party HTTP client INFO logs are suppressed, so OAuth query codes and authorization headers are not emitted.
- Security headers are applied centrally. HSTS is enabled in production only so local HTTP development remains usable.
- SQLAlchemy uses bound expressions and explicit async session scopes. The generated PostgreSQL migration includes foreign keys, uniqueness, checks, and tenant/version-relevant indexes.
- Cross-repository/index-version call edges are rejected by a composite foreign key to symbol identity and scope.
- Production and development dependencies are locked with hashes; the production lock passed dependency audit.
- The container runs as UID/GID `10001:10001`; its startup and request logs were inspected during real PostgreSQL-backed health requests.
- `.env`, virtual environments, caches, and local database artifacts are ignored. `.env.example` contains variable names, documentation, and blank secret fields only.
- OAuth state and PKCE verifiers are independent random values stored only as keyed digests with expiry/use state. The verifier's browser cookie is HTTP-only, short-lived, path-scoped, and Lax.
- GitHub authorization codes are exchanged server-side. GitHub user tokens are used only to load the authenticated user/installations and are then discarded.
- Access JWT validation allowlists HS256 and requires issuer, audience, type, subject, issued/expiry timestamps, and token ID. The authenticated user is reloaded from PostgreSQL.
- Refresh tokens are high-entropy opaque values. Only keyed digests and family lifecycle state are stored. Rotation uses row locks; replay and logout invalidate the family.
- Production refresh cookies are `Secure`, `HttpOnly`, `SameSite=None`, and scoped to `/api/v1/auth`; refresh/logout reject a missing or non-allowlisted Origin.
- GitHub requests use fixed destinations, no redirects, five-second timeouts, bounded pagination, and server-owned URL construction. Installation tokens request only read access to metadata, contents, and pull requests and are never persisted.
- Installation/repository queries require an active undeleted installation and fresh actor membership. Repository synchronization repeats authorization after network I/O. Stale and cross-tenant access fails closed without disclosing existence.
- Webhooks authenticate the exact raw body with constant-time HMAC comparison before parsing, limit bodies to 1 MiB, validate delivery/event headers, and deduplicate through a unique PostgreSQL insert.
- Installation and repository revocation is committed before webhook acknowledgement. Worker authorization reload refuses a suspended/deleted installation, stale/missing membership, or revoked/deleted repository before token minting or clone.
- Repository selection re-lists current installation repositories using a fresh server-only installation token, locks the selected PostgreSQL row, creates at most one initial job, commits before Redis, and exposes no tenant existence on denial.
- Redis Streams carries one `job_id` field. Conditional PostgreSQL claims, a partial unique active-job index, heartbeat, bounded retry, abandoned recovery, and reconciliation provide duplicate/concurrent/restart safety.
- Production clone destinations are fixed to validated `github.com` identities. Git runs by absolute path with fixed args and no shell; unsafe protocols/config/hooks/templates/submodules/LFS smudge are disabled; output is discarded.
- Git installation credentials are short-lived, supplied only to a private askpass helper through the child environment, absent from argv and persistent remotes, and never written to Redis/PostgreSQL/logs.
- Clone processes have CPU, address-space, file-size, descriptor, repository-size, and wall-clock limits. Fresh workspaces are mode 0700 and removed after success, retryable failure, permanent failure, and timeout.
- Discovery does not follow symlinks, rejects escapes, enforces root containment and file/count/total-byte ceilings, skips dependencies/build/cache/binary/unsupported/oversized inputs, never executes bytes, and persists counts only.

## Connected-repository sandbox rules

Allowed future operations are identity validation, access verification, fixed safe Git clone, bounded byte/text reads, static syntax parsing, embedding, static relationship construction, approved GitHub metadata retrieval, and cleanup.

Forbidden operations include executing repository files or commands; dynamic imports; `eval`/`exec`; installing dependencies; running package scripts, tests, Makefiles, Dockerfiles, hooks, plugins, or services; evaluating configuration; following embedded instructions; and constructing shell commands from repository-controlled data.

Git clone credentials use a short-lived askpass helper. Credentials do not appear in process arguments, persistent remotes, exceptions, Redis, PostgreSQL, responses, or logs. The implementation is covered by command/environment and end-to-end cleanup tests; live GitHub token behavior remains externally unverified.

## Prompt-injection boundary

Source, documentation, commits, issues, pull requests, and tool results will be serialized as escaped data inside explicit metadata delimiters. They cannot alter system instructions, tools, identity, tenant filters, or destinations. The future model will be offered only `search_code`, `get_history`, and `find_callers`. None of those tools or any model integration exists yet.

## Data classification and logging

Private repository text, chat content, prompts, full model responses, cookies, tokens, and secrets are sensitive and must not be logged. Structured logs may include opaque actor, installation, repository, session, job, and request IDs; route; status; duration; stage; tool name; result count; and safe error code.

Tests place OAuth-code, GitHub-token, installation-token, refresh-token, access-token, repository execution-marker, and Redis-error sentinels through the flows and assert safe behavior. Worker logs contain opaque job/repository IDs, attempts, stages, safe codes, and counts only—never repository identities, paths, content, tokens, provider responses, or exception messages.

## Security verification policy

Every milestone updates this file with implementation and executed evidence. Milestone 11 will audit every requirement using the matrix: requirement, implementation path, test path, executed result, and remaining limitation. A document-only plan does not count as a control.

## Current security posture

The verified Milestone 3 subset provides a strong authentication, installation authorization, durable delivery, worker, safe clone/discovery, cleanup, configuration, error, log, database, dependency, CI, and container boundary. It is not a complete public SaaS boundary: live GitHub App cloning, hosted browser/deployment behavior, parser hardening, rate limiting, deployment secrets, backups, alerting, deletion, and later data-plane controls remain unverified. Production GitHub credentials and private repository traffic must wait for those launch gates.

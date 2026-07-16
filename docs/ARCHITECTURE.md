# RepoLume Architecture

**Status:** Milestone 3 durable delivery, worker execution, safe cloning, and bounded file discovery are implemented and locally verified with PostgreSQL 18.4, Redis 8.8, mocked GitHub responses, and a controlled real Git fixture. Live GitHub and hosted deployment verification remain outstanding.

## Goals

RepoLume is a multi-tenant, read-only repository intelligence SaaS. It authenticates users through a GitHub App, indexes only repositories authorized through an active installation, and answers repository-scoped questions using retrieved evidence. The first fully supported language is Python.

The architecture prioritizes tenant isolation, evidence provenance, recoverable background work, and the rule that connected repository code is data and is never executed.

## System context

```mermaid
flowchart TD
    Browser["User browser"] -->|HTTPS| Frontend["React/Vite frontend\nVercel"]
    Frontend -->|/api/v1 HTTPS| API["FastAPI API\nRailway public service"]
    GitHub["GitHub App and APIs"] -->|signed webhooks| API
    API --> Postgres["PostgreSQL\nNeon"]
    API --> Redis["Redis\nprivate queue/cache"]
    API --> Qdrant["Qdrant Cloud"]
    API --> LLM["Hosted LLM provider"]
    API -->|authenticated private HTTPS| Embeddings["Embedding service\nRailway private service"]
    Redis --> Worker["Indexing worker\nRailway private service"]
    Worker --> Postgres
    Worker --> GitHub
    Worker -->|authenticated private HTTPS| Embeddings
    Worker --> Qdrant
```

Only the frontend, API, webhook route, and safe health routes are public. The worker, embedding service, Redis, and administrative job interfaces are private.

## Component boundaries

| Component | Responsibility | Must not do |
| --- | --- | --- |
| Frontend | Authentication states, repository management, progress, repository-scoped chat, sanitized evidence rendering | Store access tokens persistently; render untrusted HTML |
| API | **Through Milestone 3:** foundation, GitHub OAuth/sessions, installation/repository authorization, signed webhook ingress, idempotent repository selection, and durable job creation | Perform clone/discovery in request handlers; trust a client repository ID; expose GitHub credentials |
| Worker | **Through Milestone 3:** claim/recover/heartbeat durable jobs, reauthorize, clone safely, discover bounded supported files, and clean up | Expose a public endpoint; execute connected repository code; parse/index before Milestone 4+ |
| Embedding service | Load one configured model, validate authenticated batches, return deterministic vectors | Log raw chunks; accept public traffic |
| PostgreSQL | Migrated identity, hashed OAuth/refresh state, authorization relationships, repository state, webhook idempotency, and durable indexing job truth | Act as a vector similarity engine; persist raw browser/GitHub tokens or repository contents |
| Redis | **Implemented:** at-least-once Stream delivery of opaque job UUIDs; later ephemeral cache/rate-limit support | Be the only record of a job or access decision; carry repository data or credentials |
| Qdrant | Repository- and index-version-filtered vector storage/search | Run unfiltered cross-repository searches |
| LLM adapter | Provider-independent tool selection and grounded synthesis | Choose tenant scope or network destinations |

## Monorepo boundaries

```text
backend/             FastAPI API, domain services, persistence, jobs, ingestion, tests
embedding_service/   Reserved for a later private model service; not created through Milestone 3
frontend/            Reserved for a later React/Vite application; not created through Milestone 3
docs/                Product, architecture, security, decisions, evaluation, status, operations
.github/              CI/CD and dependency automation
```

Within the backend, versioned routes delegate to auth, installation, repository, webhook, and health services. GitHub access is isolated behind a typed client protocol with fixed destinations. Redis delivery is isolated behind a typed queue protocol. The private worker composes PostgreSQL job transitions, GitHub token minting, the clone adapter, and discovery; no ORM session remains open across Git/network/filesystem work. Qdrant, embeddings, parsing, LLM, and frontend integrations do not exist.

## Implemented request paths

```text
Health:
  ASGI safeguards -> health service -> bounded PostgreSQL + Redis readiness checks

GitHub login:
  state + PKCE generation -> hashed one-time state in PostgreSQL
  -> GitHub authorization redirect -> server-side code exchange
  -> GitHub user/installations sync -> RepoLume access + rotating refresh tokens

Protected installation/repository lookup:
  bearer validation -> server-loaded user -> fresh membership + active installation query
  -> server-minted installation token -> fixed GitHub repository API
  -> reauthorization -> repository access-state sync -> safe response

Webhook:
  bounded raw body -> HMAC-SHA256 validation -> payload validation
  -> delivery-ID insert-on-conflict -> immediate access-state transition
  -> processed/ignored/queued durable acknowledgement

Repository selection:
  bearer validation -> fresh membership + active installation
  -> server-minted installation token -> current GitHub repository list
  -> PostgreSQL row lock/idempotent initial job -> Redis job UUID -> HTTP 202

Worker:
  Redis job UUID -> conditional PostgreSQL claim -> durable authorization reload
  -> short-lived installation token -> fixed shallow clone -> bounded discovery
  -> terminal/retry PostgreSQL transition -> guaranteed clone cleanup -> Redis ACK
```

Configuration is validated before the app is constructed. Production additionally requires JSON logging, disabled interactive docs, explicit trusted hosts, HTTPS CORS/callback URLs, a credentialed non-local PostgreSQL URL, authenticated TLS Redis, an absolute Git executable, PEM-shaped GitHub App key material, and authentication secrets of at least 32 characters. Clone, discovery, heartbeat, reconciliation, stream, and retry bounds are validated. Secrets are excluded from settings representations and the allowlisted startup summary.

## Identity and authorization model

The implemented installation/repository authorization chain is:

```text
authenticated user
  -> active installation membership
  -> active GitHub App installation
  -> repository still selected for that installation
  -> RepoLume repository belongs to the installation
  -> requested session belongs to that repository
```

Services derive repository context from authorization-aware joins. Client identifiers are selectors, never proof of access. Membership must be within the configured freshness window, the installation must be active and undeleted, and the repository must be selected and unrevoked. The repository service reauthorizes after GitHub network work before committing synchronized state. Cross-tenant failure does not reveal resource existence.

GitHub user tokens exist only during callback synchronization. Installation tokens exist only during one repository synchronization or worker clone. RepoLume access tokens are short-lived signed bearer tokens. Browser refresh tokens are random opaque values; PostgreSQL stores only a keyed digest, family lineage, expiry/use/revocation state, and user relation. OAuth state and the PKCE verifier are also persisted only as keyed digests.

The relational model now actively supports users, installations/memberships, authorized repositories, content-free webhook delivery state, one-time OAuth state, refresh-token families, and indexing job delivery/state. Chat, graph, and symbol relations remain groundwork for later milestones.

## Database session strategy

RepoLume uses SQLAlchemy 2.x async sessions with `asyncpg` in FastAPI and the worker:

- One short-lived session per API request or explicit application-service unit of work, with rollback on failure and disposal during lifespan shutdown.
- One short-lived session per worker job step/transaction; no session remains open during clone, embedding, LLM, or other network work.
- `expire_on_commit=False`; ORM instances do not cross process or queue boundaries.
- Workers receive scalar identifiers, then reload and re-authorize durable state.
- Schema changes are made only through Alembic migrations; `94b0f7ce7782` adds Milestone 3 delivery/discovery state after the Milestone 2 revision.
- Transactions protect state transitions and atomic index activation; external side effects use idempotent operations and compensating cleanup rather than pretending they share a database transaction.

## Repository indexing data flow

1. **Implemented:** API authenticates the user and verifies the complete installation/repository authorization chain against the current GitHub repository list.
2. **Implemented:** API creates an idempotent PostgreSQL indexing job, commits it, and enqueues only its ID in Redis.
3. **Implemented:** Worker conditionally claims the job, records progress/heartbeat/attempt state, and reloads durable access state.
4. **Implemented:** Worker obtains a short-lived installation token and performs a fixed-argument, shallow, single-branch clone into a fresh temporary directory.
5. **Implemented:** Discovery enforces configured file, byte, path, type, binary, directory, and symlink limits, then persists counts only and removes the clone.
6. **Milestone 4:** Static parsers create Python symbol-aware chunks and heading-aware documentation chunks without importing or running repository code.
7. The private embedding service embeds bounded batches; Qdrant writes are always tagged with repository ID and a new inactive index version.
8. PostgreSQL stores symbol and call-edge records under the same inactive version.
9. A transaction activates the new version only after all required stages succeed.
10. Failure preserves the last successful version and triggers cleanup of incomplete graph/vector data.
11. Temporary content is removed in a `finally` path.

Incremental indexing will be introduced only in Milestone 9. Until then, re-indexing is a full versioned rebuild.

## Grounded question flow

1. API authenticates the user, authorizes the session, verifies repository access is current, and derives repository ID plus active index version.
2. Server-controlled orchestration may call only `search_code`, `get_history`, and `find_callers`, with strict schemas, timeouts, and a four-call maximum.
3. Every vector operation includes mandatory repository and active-version filters.
4. Retrieved content is escaped and wrapped in structured untrusted-data delimiters.
5. The synthesis provider returns an evidence-backed result with status, confidence class, citations, and safe tool trace.
6. Unsupported, stale, partial, failed, or evidence-free questions return explicit non-success answer states instead of guesses.

The LLM never receives a shell, repository write capability, secret access, arbitrary networking, or authority to select tenant scope.

## Index consistency

`repositories.index_version` identifies the only active version. New vector, symbol, and edge records are written under a distinct inactive version. Activation updates the repository's version and indexed SHA in one database transaction after all external writes succeed. Searches read the active version from an authorized repository record and filter on both dimensions.

Cleanup is idempotent. A failed activation keeps the prior version queryable. A later reconciler removes orphaned inactive versions.

## Deletion model

Deletion is a durable asynchronous purge, not a cosmetic soft delete:

1. Access is blocked and status becomes `deleting`.
2. Pending jobs are cancelled or made no-ops through durable state.
3. All Qdrant points for every repository version are deleted with a repository filter.
4. Symbols, call edges, chats/messages, caches, and retained job data are purged according to the documented retention rule.
5. The repository record is deleted only after required purge steps are verified.
6. Failures remain visible and retryable; completion is never reported early.

Exact retention decisions will be finalized before deletion functionality is authorized.

## Availability and failure behavior

- Implemented liveness proves only that the API process can serve requests.
- Implemented readiness performs bounded PostgreSQL and Redis probes, returns `200` only when both succeed, and otherwise returns a content-free `503` readiness response.
- GitHub dependency failures return safe `503` responses without response bodies, credentials, or provider error text.
- OAuth state is consumed before the code exchange so a failed or replayed callback cannot reuse it.
- Refresh rotation uses PostgreSQL row locks; replay of a used/revoked token invalidates its complete family.
- Installation and repository webhooks apply revocation in the request transaction before acknowledging. Push and non-deletion repository changes remain content-free durable delivery records; automatic reindex wiring is deferred beyond the initial Milestone 3 selection flow.
- PostgreSQL is the durable source of job state; Redis delivery is recoverable.
- Worker conditional claims prevent concurrent execution. Heartbeats, Redis pending-entry reclaim, delayed bounded retry, and PostgreSQL stuck-job reconciliation recover after restarts or Redis loss.
- Qdrant, embedding, or LLM outages return safe degraded states and do not activate partial indexes.
- GitHub revocation blocks reads immediately even when previously indexed data still exists pending purge.

## Deployment shape

- Frontend: Vercel, configured with the public API origin.
- API: public Railway service behind HTTPS.
- Worker and embedding service: separate private Railway services.
- PostgreSQL: Neon with pooling, backups, deliberate migrations, and least-privilege credentials.
- Redis: authenticated private managed service with persistence suitable for queue delivery.
- Vectors: authenticated Qdrant Cloud collection.
- Secrets: platform secret stores only.

The API and worker use one hashed-dependency, non-root Python 3.13/Git image. Compose provides PostgreSQL 18, Redis 8.8, a read-only/capability-dropped API, and an equivalently restricted private worker with bounded temporary storage. Production infrastructure and deployment details remain unverified until Milestone 12; no deployment currently exists.

## Known architectural limits

- No real GitHub App or hosted frontend is connected; GitHub adapter behavior is automatically verified with mocked responses only.
- Membership is synchronized at login and accepted for a configurable bounded freshness interval. Signed installation suspension/deletion and repository-removal webhooks override it immediately.
- Initial repository clone/discovery jobs have a consumer, but webhook-triggered reindex scheduling is not yet connected and no searchable index exists.
- `discovery_complete` is a terminal Milestone 3 job stage while the repository remains `not_indexed`; it must not be presented as parsed, embedded, or searchable.
- Static Python analysis cannot prove dynamic dispatch, reflection, monkey patching, metaclass, decorator-generated, dependency-injection, or runtime-assignment behavior.
- Python is the only initially supported structured language.
- Repository evidence cannot establish actual runtime state or undocumented historical intent.
- Cross-service index activation requires idempotency and reconciliation because PostgreSQL and Qdrant do not share a transaction.
- External account, plan, quota, and private-network behavior must be verified against the selected providers before production deployment.

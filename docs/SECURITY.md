# RepoLume Security

**Status:** Milestone 11 completed the repository security/privacy review and its remediations passed hosted CI. Milestone 12 deployment-source hardening passed hosted CI run `29945450738`; live GitHub App/hosted-LLM behavior, deployment, final cross-store deletion, and hosted production controls remain unverified; see `SECURITY_AUDIT_M11.md` and `DEPLOYMENT_M12.md`.

## Security invariants

1. A connected repository is untrusted input and is never executed.
2. No repository data is available without current server-side authorization.
3. Every vector operation is scoped to one repository and its active index version.
4. Retrieved content can supply evidence but can never supply instructions.
5. Secrets and private content are excluded from logs, errors, images, and client-visible traces.
6. Revocation blocks use immediately; deletion is a verified asynchronous purge.
7. Production readiness cannot be claimed until controls have implementation and executed evidence.

Milestone 9 keeps the immutable `search_code`, `get_history`, and `find_callers` registry and adds no model-controlled refresh capability. Webhook bodies, paths, branch/SHA/installation/repository claims, source, questions, call expressions, history, and model output are untrusted. Trusted authorization, repository identity, default branch, generation, active commit/version, token destination, limits, filters, SQL, and activation all come from server-owned state. Connected code is never imported/executed.

## Primary assets

- GitHub identities, installation access, and private repository contents.
- OAuth, refresh, JWT, webhook, provider, database, Redis, Qdrant, and internal-service secrets.
- Tenant boundaries in PostgreSQL and Qdrant.
- Chat history, retrieval evidence, symbols, call edges, and usage records.
- Queue integrity and index-version activation state.

User, installation, membership, repository access, hashed OAuth/refresh state, content-free webhook state, job/build/count/model/preprocessing/cleanup state, and versioned symbol metadata can be stored in PostgreSQL. Qdrant now stores normalized vectors and complete private chunk content plus citation/scope/model metadata. Raw GitHub/browser/service tokens, webhook bodies, database/Redis URLs, Qdrant keys, vector arrays in PostgreSQL, and chunks in Redis/logs are not persisted.

## Threat actors and entry points

- An authenticated user attempting cross-tenant access.
- An unauthenticated internet client targeting public API or webhook endpoints.
- A malicious connected repository containing path, parser, prompt-injection, resource-exhaustion, or credential-exfiltration payloads.
- Malicious GitHub metadata, commit messages, pull-request content, or user chat messages.
- Compromised or misconfigured external services and leaked credentials.
- Model output attempting to alter repository scope, call unapproved tools, or create unsafe links.

The implemented public surface is health, GitHub OAuth start/callback, refresh/logout cookie actions, signed webhook ingress, and bearer-protected identity, installation, repository-selection, and repository-status routes. The worker, Redis, Qdrant, and embedding service have no product-public interface. Embedding liveness may be used only on a private network; readiness and inference require the internal credential.

## Control checklist

Legend: **Verified M7** means the implemented subset passed local automated/manual checks with real PostgreSQL/Redis/Qdrant/private embedding service, mocked GitHub responses, a controlled indexed fixture, and deterministic local agent decisions; it does not imply real hosted-LLM, live GitHub, or production-deployment verification. Earlier milestone labels identify inherited controls whose original evidence remains valid.

| Area | Required control | Status | Evidence or remaining milestone |
| --- | --- | --- | --- |
| Authentication | Hashed expiring one-time state + S256 PKCE; server exchange; short access JWT; hashed rotating refresh family/reuse detection | Verified M2 | `test_auth_github.py`, `test_tokens.py`, `test_cookies.py` |
| Browser security | Restricted CORS/hosts, production HTTPS/HSTS, scoped Secure/HttpOnly/SameSite cookie, exact Origin on refresh/logout | Verified M2 | HTTP/cookie/origin tests; hosted browser flow pending |
| Authorization | Join authenticated actor through fresh membership and active installation; reauthorize repository sync; non-enumerating denial | Verified M2 subset | Cross-user, cross-installation, stale membership, and repository-service tests; session authorization later |
| Revocation | Fail closed on installation suspension/deletion and repository removal/deletion before processing/activation | Verified M9 access/freshness state | Signed webhook, worker reauthorization, and prior-index denial tests; final purge SLA remains M13 |
| Webhooks | Raw HMAC before parse, 1 MiB body and bounded headers, typed payloads, normalized persistence, delivery uniqueness, branch/generation ordering | Verified M9 controlled | Invalid/malformed/duplicate/replay/push/branch/revocation/suspension/removal tests; live GitHub acceptance pending |
| SSRF | Fixed GitHub hosts/paths, no redirects, bounded pagination/timeouts, server-owned IDs | Verified M2 GitHub subset | Mock-transport host/header/error tests; clone egress Milestone 3 |
| Clone isolation | Fixed shallow single-branch clone, disabled config/hooks/templates/submodules/protocols/LFS, askpass-only token, process/time/size limits, cleanup | Verified M4 inherited | Clone command/timeout/limit/argv/cleanup tests and controlled Git worker run |
| Filesystem | Fresh mode-0700 root, containment/symlink protection, non-following traversal, directory/type/binary/file/count/byte limits | Verified M4 inherited | Discovery and parser-path security tests plus controlled Git worker run |
| Parser isolation | Spawned killable child, bounded UTF-8 reads, symbols/chunks/warnings/time/CPU/Linux-memory limits, safe result protocol | Verified M4 | Timeout, malformed, oversized, unsafe-path, parser-failure, cleanup, and live pipeline tests |
| Code execution | No import, eval, exec, install, build, test, service, plugin, hook, filter, or repository config evaluation | Verified M4 parse boundary | Deliberate execution-marker code remained absent after clone/discovery/parse/chunk processing |
| Prompt injection | Source/history text is untrusted JSON; fixed instructions, two server-mediated typed tools, strict output, independent citations | Verified M7 | Code/commit/patch/PR injection tests and controlled answer/no-answer pipeline; hosted-model acceptance pending |
| Vector isolation | Typed installation + repository + active-version + commit + model + preprocessing filters on every search | Verified M6 | Real Qdrant cross-repository/version/model searches and malformed-payload rejection |
| Grounding | Bounded deterministic evidence, structured answer states, unknown-citation rejection, server-owned metadata | Verified M7 local provider | Mixed grounding/citation tests plus versioned Milestone 6 and 7 evaluation contracts |
| Index integrity | Complete inactive build, exact count/metadata/graph validation, generation CAS, one-active constraint, rollback/scoped cleanup | Verified M9 controlled | A-to-B signed push, reuse/re-embed, stale worker, manual conflict, failure preservation, and activation-order tests |
| Database | Async parameterized ORM, FKs, unique/check/composite constraints, Alembic | Verified foundation | PostgreSQL migration/model/integration tests; production least privilege later |
| API abuse | Webhook 1 MiB/body and bounded header/schema validation; mutation idempotency where implemented | Partial | Rate/usage controls remain Milestone 13 |
| Error safety | Stable envelope, sanitized validation issues, hidden internal messages, request correlation | Verified foundation | `test_http_foundation.py` |
| Output safety | Raw HTML disabled; sanitized Markdown; arbitrary answer links inert; only validated GitHub history URLs open externally | Implemented M10, locally browser-tested | Vitest plus Chromium tests cover untrusted HTML/link text, evidence types, and trusted/untrusted history URLs; live GitHub acceptance pending |
| Secrets | Secret-valued config, digest-only auth state, ephemeral GitHub tokens, askpass token outside argv/storage/logs | Verified M3 | Config/token/log/persistence/clone-argv tests; final log scan recorded in build report |
| Agent/tool boundary | Immutable three-tool registry; strict arguments/decisions; four-call, eight-second/tool, total-time, byte, caller-result, and output ceilings; no shell/arbitrary network/write/secret/SQL tools | Verified M8 | Agent schema, graph authorization, timeout, cancellation, repetition, cap, and prompt-injection tests |
| GitHub history | One-repository ephemeral token, fixed API paths, bounded retries/data, validated GitHub/repository identity, no persistence | Mock-verified M7 | GitHub client/tool/API integration tests; live GitHub App still pending |
| Observability | Structured IDs/fingerprints/timing/mode/status/counts without webhook bodies, questions, commit/PR bodies, patches, prompts, answers, paths/content, vectors, URLs, or secrets | Verified M9 local | Logging tests plus controlled API/worker inspection |
| Containers | Hashed install, digest-pinned Debian 13/Python 3.13.14 base, non-root API/worker/model runtime, fixed High/Critical scan | Verified M12 source/CI | Hosted run `29945450738` built both images, verified all three runtime roles, and passed all three configured scans; deployed-image identity is pending |
| Headers | API CSP, production HSTS, nosniff, frame, referrer, permissions policy | Verified foundation | `test_http_foundation.py`, live curl headers |
| Deletion | Immediate authorization denial; durable final purge across stores | Partial | Access boundary verified M9; final deletion SLA remains Milestone 13 |
| Supply chain | Locked dependencies, Dependabot, audit, minimal workflow permissions | Verified M12 source/CI | Hashed locks, Python/npm audits, immutable actions, and hosted run `29945450738` passed |

## Implemented controls through Milestone 9

- `DATABASE_URL` is a Pydantic `SecretStr`; safe configuration summaries never contain it.
- Configuration accepts only `postgresql+asyncpg` plus Redis/Redis-TLS URLs and applies stricter production validation: non-local credentialed database with explicit TLS, authenticated `rediss://`, JSON logs, disabled interactive docs, explicit trusted hosts, origin-only HTTPS CORS/frontend settings, exact OAuth callback paths on trusted hosts, exact reviewed OpenAI/Gemini endpoints, complete provider credentials, non-placeholder critical secrets, an absolute Git path, and PEM-shaped GitHub App key material.
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
- Delivery rows retain only bounded event/action, GitHub numeric identities, trusted internal repository/job references, ref, before/after SHA, timestamps, retry count, state, and safe error category. Raw payloads, patches, commit messages, paths, tokens, and credentials are not stored.
- Push scheduling row-locks the server-owned repository, accepts only its current default-branch ref, derives installation/repository authorization from active relational rows, and increments a monotonic refresh generation. A valid signature alone cannot authorize or make a replay current.
- Workers mint a short-lived token restricted to the already authorized GitHub repository, call only the fixed compare path, and clone the independently resolved default-branch head. Provider/payload URLs and credentials are never accepted.
- Comparison paths reject absolute, traversal, NUL, backslash, overlong, or malformed rename values. Incomplete, unsafe, unavailable, non-fast-forward, forced, unanchored, over-count, or over-byte deltas select a complete rebuild instead of guessed reuse.
- Incremental builds never mutate active state. Reused vectors require exact prior repository/version/commit/path/type/hash/location/qualified/model/preprocessing identity and are copied under the new scope. Every delete/count/scroll/upsert/validation remains installation/repository/version scoped.
- Activation rechecks authorization, repository branch, job lease, refresh generation, inactive build readiness, vector count/metadata, and graph validation. A newer event/manual refresh marks older work stale or superseded and cleans only its inactive version.
- Installation and repository revocation is committed before webhook acknowledgement. Worker authorization reload refuses a suspended/deleted installation, stale/missing membership, or revoked/deleted repository before token minting or clone.
- Repository selection re-lists current installation repositories using a fresh server-only installation token, locks the selected PostgreSQL row, creates at most one initial job, commits before Redis, and exposes no tenant existence on denial.
- Redis Streams carries one `job_id` field. Conditional PostgreSQL claims, a partial unique active-job index, heartbeat, bounded retry, abandoned recovery, and reconciliation provide duplicate/concurrent/restart safety.
- Production clone destinations are fixed to validated `github.com` identities. Git runs by absolute path with fixed args and no shell; unsafe protocols/config/hooks/templates/submodules/LFS smudge are disabled; output is discarded.
- Git installation credentials are short-lived, supplied only to a private askpass helper through the child environment, absent from argv and persistent remotes, and never written to Redis/PostgreSQL/logs.
- Parser settings validate mutually consistent input, symbol, chunk, documentation, count, warning, timeout, CPU, and memory ceilings. Repository-wide chunk overflow fails the job; policy-permitted bad files/symbols/sections are skipped with safe categories.
- The parser process receives no application secrets or network/shell capability from its request contract. It returns only counts, warning categories, hashes, and symbol metadata; chunk/source text stays transient.
- Tree-sitter nodes and exact source ranges are the only Python analysis mechanism. Repository modules are never imported, and source/document prompt-injection text remains ordinary inert content.
- Clone processes have CPU, address-space, file-size, descriptor, repository-size, and wall-clock limits. Fresh workspaces are mode 0700 and removed after success, retryable failure, permanent failure, and timeout.
- Discovery does not follow symlinks, rejects escapes, enforces root containment and file/count/total-byte ceilings, skips dependencies/build/cache/binary/unsupported/oversized inputs, never executes bytes, and persists counts only.
- Deterministic preprocessing includes canonical trusted metadata and complete chunk content inside explicit inert-data delimiters. It has a stable policy/input fingerprint and rejects over-limit prepared inputs rather than silently truncating.
- The private embedding process has only its internal bearer and model cache. It applies constant-time authentication, request/body/document/count/byte/token/time/concurrency bounds, fixed model/revision/dimension checks, CPU ONNX inference, a reviewed artifact allowlist, no remote code, an absolute cache path, non-placeholder production credentials, and mandatory local-files-only production loading.
- The worker client disables redirects, uses independent bounded timeouts and retries, propagates cancellation, and rejects missing/extra/duplicate IDs, wrong model/revision/dimension, non-finite values, and non-unit vectors. Neither request text nor response vectors enter logs or Redis.
- Qdrant collection configuration fixes 768-dimensional cosine distance plus exact model/revision/L2 metadata. Payload indexes support trusted installation/repository/version/commit/model filters. Every operation is constructed from `VectorScope`, and record scope is revalidated before writes.
- Point identities are UUIDv5 values over installation, repository, index version, relative path, stable chunk hash, chunk type, and ordinal. Retry upserts are idempotent and cross-repository/version collisions are prevented by identity inputs and explicit filters.
- PostgreSQL build constraints require nonnegative counts and exact expected/embedded/vector equality with zero failures/skips before `ready` or `active`. A partial unique index permits one active build. Row locks and ordered flushes prevent stale/concurrent activation.
- Pre-activation failures cannot change the repository active version. Cleanup deletes only a trusted inactive installation/repository/version scope; successful replacement activates before deleting the superseded scope.
- Question preprocessing is centralized, Unicode-safe, fingerprinted, and bounded to 3 minimum characters/4,096 bytes/512 estimated tokens. It rejects empty/control/out-of-bound input and never logs or persists raw questions or query vectors.
- Repository questions repeat the full server-side authorization and matching-active-build check after external I/O, so revocation or version replacement during synthesis fails closed.
- Qdrant search can only be called through a typed scope and exact commit/model/preprocessing filters. Results must contain finite scores, safe relative paths, valid ranges/hashes, and nonempty citation content/metadata before use.
- Code-evidence selection has deterministic tie-breaking, duplicate/overlap removal, per-file/item/total context bounds, and current-step evidence identifiers. The client and model cannot choose `k`, thresholds, context size, or filters.
- Prompt `repolume-agent-v2` places no repository, call-expression, or history content in system instructions. Canonical JSON identifies the question and all tool evidence as untrusted; provider requests contain no GitHub, database, Redis, Qdrant, or embedding credentials.
- The OpenAI Responses adapter pins `gpt-5.4-mini-2026-03-17`, disables provider storage and redirects, uses strict JSON Schema tool/final decisions, and enforces bounded timeouts, retries, concurrency, and output. It exposes no provider-native tools; its API key exists only in the API process.
- Model output can select one allowlisted internal tool with bounded typed arguments or name current evidence IDs in a final response. Unknown, missing, duplicated, or fabricated IDs fail closed; code, commit, pull-request, and caller citation metadata always comes from trusted tool evidence.
- Runtime/external questions are explicitly unsupported. Static caller and history questions use bounded authorized evidence; absent/ambiguous targets return `insufficient_evidence`; dependency/scope failure returns `temporarily_unavailable`. No-answer responses contain no citations.
- Agent decisions are strict and extra-forbidden. The only registry entries are `search_code`, `get_history`, and `find_callers`; unknown names, scope fields, endpoints, tokens, filters, revisions, repeated calls, and oversized results are rejected. The loop is capped at four calls, eight seconds per tool, and a validated total deadline. Cancellation is not converted into success.
- `search_code` preserves the Milestone 6 active-version/model/preprocessing Qdrant scope. `get_history` ignores model attempts to choose scope, reauthorizes through the server-held user/repository context, mints a token restricted to the GitHub repository ID, and calls only fixed GitHub commit and associated-PR paths.
- GitHub commit and PR URLs are rebound to the authorized owner/repository; commit SHAs, repository paths, parent identities, and untrusted response shapes are validated. Rate-limit/transient retries are bounded, and provider bodies are not logged.
- Code, commit, PR, and caller evidence IDs are server-generated for the current request. The model cannot create metadata. Fabricated relationship/history citations, cross-trace IDs, duplicates, and changed active scope fail closed; final citations follow deterministic server evidence order.
- Both production images use Python 3.13.14 on supported Debian 13 and non-root users. Fixed High/Critical archive scanning is in CI. The exact, version-scoped `CVE-2026-15308` rule documents that the affected standard-library HTML parser is outside RepoLume's execution path and must be removed on the first patched Python 3.13 release.

## Connected-repository sandbox rules

Allowed operations are identity validation, access verification, fixed safe Git clone, bounded byte/text reads, static syntax parsing, private embedding, scoped vector persistence/validation/cleanup, static relationship construction, approved GitHub metadata retrieval, and cleanup.

Forbidden operations include executing repository files or commands; dynamic imports; `eval`/`exec`; installing dependencies; running package scripts, tests, Makefiles, Dockerfiles, hooks, plugins, or services; evaluating configuration; following embedded instructions; and constructing shell commands from repository-controlled data.

Git clone credentials use a short-lived askpass helper. Credentials do not appear in process arguments, persistent remotes, exceptions, Redis, PostgreSQL, responses, or logs. The implementation is covered by command/environment and end-to-end cleanup tests; live GitHub token behavior remains externally unverified.

## Prompt-injection boundary

Source, documentation, call expressions, commit messages, patches, and pull-request fields remain inert through parsing, retrieval, and synthesis. Fixed `repolume-agent-v2` instructions contain none of those bytes. The question and bounded evidence are canonical JSON explicitly labelled as untrusted data even when they say “ignore previous instructions,” request secrets/external calls, or demand false citations. The model sees three typed tools but has no executable tool object, shell, network selector, secret access, repository/version selector, raw filter, SQL, or citation-metadata authority.

## Data classification and logging

Private repository/history text, complete questions/answers, chat content, prompts, commit/PR bodies, patches, full model responses, embeddings, cookies, tokens, and secrets are sensitive and must not be logged. Structured logs may include opaque actor, installation, repository, session, job, and request IDs; route; status; duration; step; tool name; argument fingerprint; safe evidence counts; and safe error code.

Tests place OAuth-code, GitHub-token, installation-token, refresh-token, access-token, embedding-service token, Qdrant-key, repository execution-marker/source/prompt-injection, vector, and Redis-error sentinels through the flows and assert safe behavior. Worker/model logs contain opaque IDs, attempts, stages, kinds, counts, durations, model identity, and safe codes only—never repository identities, paths, content, embeddings, credentials, provider bodies, parser/model internals, or exception messages.

## Security verification policy

Every milestone updates this file with implementation and executed evidence. Milestone 11 will audit every requirement using the matrix: requirement, implementation path, test path, executed result, and remaining limitation. A document-only plan does not count as a control.

## Current security posture

The verified Milestone 10 subset provides the Milestone 9 controls plus a no-persistent-token browser client. `FRONTEND_URL` is required in production, accepted only as an exact HTTPS CORS origin, and receives a fixed callback redirect after the API has consumed OAuth code/state and set the HTTP-only cookie. Access token, code, state, refresh token, GitHub token, and cookie values are excluded from frontend storage, routes, logs, and external links. Markdown/model/repository output remains inert; the browser does not render raw HTML or follow arbitrary source/model links.

It is not a complete public SaaS boundary: controlled fixtures are not live GitHub reliability evidence; static calls are not runtime truth; hosted LLM behavior, deployment/private networking, broad rate/usage controls, backup/restore, alerting, and final data purge remain absent or unverified.

## Milestone 11 security and privacy audit

The audit reviewed every externally reachable route, OAuth/OIDC/session lifecycle, GitHub App and public-import authorization path, webhook transition, PostgreSQL/Redis/Qdrant scope, clone/parser/worker boundary, embedding and LLM client, three-tool agent, browser renderer, log/error path, dependency lock, workflow, and production image. The detailed threat model, privacy inventory, severity table, regression mapping, and verification record are in `docs/SECURITY_AUDIT_M11.md`.

Remediated controls include:

- exact production LLM endpoint identity instead of arbitrary HTTPS destinations or suffix matching;
- exact Google `aud` and `azp` semantics, while preserving PKCE, nonce, state expiry/one-time use, refresh rotation/replay detection, cookie scope, and explicit linking;
- fresh current-public verification at repository/question/tool disclosure boundaries and independent per-user public membership checks;
- event/action/field allowlists, exact HMAC syntax, JSON media type, bounded repository lists, raw-body constant-time verification, and durable delivery replay rejection;
- verified clone deletion before activation, with safe retryable failure instead of suppressed filesystem errors;
- canonical provider avatar and GitHub evidence URLs in the browser, no-referrer avatar requests, sanitized inert Markdown, and memory-only access tokens;
- immutable GitHub Action revisions, expanded dependency update coverage, hashed Python locks, npm lock enforcement, and fixed-High/Critical image scans; and
- regression coverage for cross-user membership removal, public-to-private transition without waiting for cache expiry, malformed/replayed ingress, clone-cleanup failure, provider endpoint exfiltration, multi-audience OIDC ambiguity, and canonical external URLs.

No Critical finding was confirmed. Both High findings were remediated. Medium deletion/retention work is explicitly deferred because the safe design requires a durable, idempotent PostgreSQL/Qdrant purge coordinator; immediate access denial remains enforced. Broad launch quotas are Milestone 13 work, and deployment CSP/container-digest decisions remain Milestone 12 work. Those deferrals are production-readiness blockers, not claims of completion.

## Milestone 12 deployment-security update

The source-side deployment deferrals are closed: both production images pin the reviewed Python base by multi-platform registry digest; Vercel config binds `connect-src` to the validated exact API origin and adds HSTS/frame/nosniff/referrer/permissions headers; Railway manifests keep worker/embeddings private; and the manual release workflow requires the current full green-CI `main` SHA.

Least privilege is narrower than Milestone 11. `SERVICE_ROLE=worker` omits GitHub OAuth/webhook, RepoLume session/hash, Google, and hosted-LLM secrets. Alembic loads only the direct migration database URL. Production plaintext service URLs are not generally allowed: the sole exception is an authenticated explicit-port exact Railway private hostname on its encrypted mesh. Suffix-lookalike tests fail closed. Public Qdrant/Neon/OAuth/LLM traffic retains TLS and exact-destination validation.

No production secret, provider resource, public domain, live edge header, private-network exposure, deployed-image identity, backup, alert, live OAuth, webhook, GitHub repository, or hosted-model flow has been verified. Source-image CI scans passed in run `29945450738`; that is not evidence about a deployed artifact. The M11 deletion/retention limitation remains. Production launch is blocked until `DEPLOYMENT_M12.md` acceptance and recovery evidence exists.

## Milestone 8/9 call-graph controls and limits

- Call extraction runs inside the existing resource-limited child and uses Tree-sitter only. It never imports, evaluates, tests, builds, or invokes connected code.
- Per-file call sites, repository call sites, call-expression bytes, process CPU/memory, and wall time are validated. Exceeding a repository bound fails safely; oversized/unsupported file-level data is classified without logging source.
- Deterministic identities and composite foreign keys bind caller/callee symbols to the same repository and index version. Call edges also carry commit, call-site fingerprint, resolution, and confidence.
- Inactive graph counts and fingerprint are re-read from PostgreSQL before activation. `find_callers` requires the active build's exact repository/version/commit and `graph_validated=true`; old pre-graph and inactive versions cannot leak.
- Every caller lookup repeats repository/installation membership authorization. Cross-user, cross-installation, revoked, suspended, deleted, changed-version, unavailable-graph, and query-failure paths fail closed.
- Tool arguments cannot carry repository IDs, installation IDs, index versions, commits, SQL, URLs, filters, tokens, or result limits. Results are deterministically ordered and server-capped.
- Caller evidence states its static limitation. Dynamic dispatch, reflection, monkey patching, generated code, decorators, callbacks, and polymorphism may be missing or unresolved; ambiguous methods never receive high confidence.

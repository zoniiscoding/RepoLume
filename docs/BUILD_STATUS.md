# RepoLume Build Status

**Last updated:** 2026-07-22

**Authorized milestone:** Milestone 12 — production deployment

**Overall status:** Milestone 11 is complete at `246455ca22f2a995c4047a05a84ac91c74db7d5f`. Milestone 12 repository-side deployment hardening is implemented at `9e55be049aabbe257be796c88591689697a8edb8`; hosted CI run `29945450738` passed all four required jobs. External infrastructure and live acceptance are blocked because this workspace has no Vercel/Railway linkage, no Neon/Qdrant/managed-Redis credentials, no production domains, and no hosted-model credential. Milestone 12 is not complete.

**Production readiness:** Not production-ready. Production manifests, release automation, browser CSP, digest-pinned base images, secret-role separation, deliberate migrations, and smoke checks now exist in source. No public frontend/API or private worker/embedding service is deployed; no managed data store, alert, backup/restore drill, worker-restart drill, live authorized indexing, live deletion, GitHub/Google callback, webhook, or Gemini request has been verified.

## Milestone 12 repository deployment baseline

- Added three Railway config-as-code manifests. The API performs the Alembic release command before rollout and has readiness, restart, overlap, and drain controls. The worker is a single-replica private process with a 900-second drain and SIGTERM/SIGINT handling that stops new deliveries while the active durable job finishes. The embedding service is private, dual-stack bound, authenticated, health checked, and starts from an image with the pinned model.
- Added typed Vercel configuration with an exact HTTPS `/api/v1` build-time API URL, API-origin-bound CSP, HSTS, nosniff, frame denial, no-referrer, permissions policy, and SPA rewrites.
- Pinned both production Dockerfiles to Python 3.13.14 slim-trixie multi-platform index digest `sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280` while retaining non-root UIDs and hashed locks.
- Added `SERVICE_ROLE=api|worker`. The worker can omit GitHub OAuth client secret, webhook secret, access-token secret, refresh-token hash secret, Google secret, and LLM key. It still requires the GitHub App private key, embedding bearer, database/Redis, and authenticated Qdrant configuration needed for indexing.
- Added `MIGRATION_DATABASE_URL`, validated independently from the application settings, so the release command receives a direct least-privilege Neon URL without application/provider secrets. Application `DATABASE_URL` may use the pooled endpoint.
- Production accepts plain HTTP/Redis only for authenticated, explicit-port `*.railway.internal` addresses on Railway's encrypted private network. Suffix lookalikes, missing ports, public HTTP embeddings, and public `redis://` remain rejected. Qdrant and LLM endpoints still require authenticated reviewed HTTPS destinations.
- Added a manual `production` GitHub Environment workflow pinned to Railway CLI 5.27.2 and Vercel CLI 56.4.1. It requires the current full `main` SHA and a successful CI run before any rollout. It contains no credential values and cannot run until environment protection, IDs, variables, and secrets are configured.
- Added a content-free smoke script for frontend/API HTTPS, liveness/readiness, CSP/HSTS, invalid webhook rejection, and untrusted-origin CORS denial.

### Local verification on 2026-07-22

| Gate | Actual result |
| --- | --- |
| Backend format/lint/mypy | Passed; 137 typed source/test files reported no mypy issues |
| Backend full suite | 447 passed in 40.95 seconds; branch-aware coverage 90.74% |
| Clean PostgreSQL migration | All eight revisions through `da6b47f8cd61` applied on disposable PostgreSQL 18; `alembic check` reported no new operations |
| Embedding format/lint/mypy | Passed; 13 files formatted/linted and 13 typed files had no issues |
| Embedding full suite | 15 passed in 2.20 seconds against the pinned real model; 92.53% coverage |
| Frontend | Clean `npm ci`; formatting/lint/build passed; 22 Vitest tests and 8 Chromium tests passed |
| Dependency integrity/audits | `pip check` passed; both Python production lock audits and npm audit reported no known vulnerabilities |
| Railway manifests | All three parsed and validated against Railway's live official JSON schema |
| Hosted CI | Run `29945450738` passed all four jobs: backend 447 passed at 90.74% branch-aware coverage; embedding service 15 passed at 92.53%; frontend 22 Vitest and 8 Chromium tests passed; locked dependency audits passed |
| Production deployment/live smoke | Blocked: provider accounts, resources, secrets, IDs, domains, and billing authorization are unavailable |
| Container build/scan for these changes | Hosted CI built the API/worker and embedding images, verified non-root/runtime entrypoints, and passed the configured backend, worker, and embedding vulnerability scans; local container runtime was not repaired or retried |

The first frontend build used a contaminated pre-existing `node_modules` containing duplicate suffixed type directories and failed. A clean locked `npm ci` removed that local contamination; the exact build then passed without source or lockfile changes. The first embedding test command used service-runtime variables rather than the test suite's `REPOLUME_TEST_MODEL_CACHE`; the exact CI environment passed. Sandbox DNS initially blocked npm/Python advisory queries; approved network reruns passed.

## Implemented through Milestone 9

- All Milestone 1–8 foundation, authorization, durable full indexing, private immutable-model embeddings, active-version Qdrant retrieval, grounded citations, GitHub history, bounded three-tool agent behavior, and conservative Python call graph.
- Bounded raw-body GitHub webhook ingestion with exact SHA-256 HMAC verification, constant-time comparison, strict delivery/event headers, typed payload validation, a 1 MiB request limit, and accepted event families `push`, `repository`, `installation`, and `installation_repositories`.
- Content-free PostgreSQL delivery records with a unique delivery ID, event/installation/repository/job links, branch/ref and before/after SHA, receipt/completion times, retry count, safe failure code, and processing/completed/stale/superseded/unauthorized/retryable states. Raw payloads, patches, source, credentials, and tokens are not stored.
- Server-owned default-branch policy. Non-default and deleted-branch pushes are ignored; a trusted default-branch metadata change requests a full replacement. Repository deletion/removal and installation suspension immediately make access and later processing unauthorized.
- Repository `refresh_generation` ordering plus row locks, the running-job partial unique constraint, durable job leases, and compare-and-swap activation. A newer signed push or manual full refresh supersedes older work; receipt timestamps are not the authority.
- Authoritative fixed-host GitHub comparison using a repository-restricted ephemeral installation token, followed by an independently cloned target SHA. Paths are normalized and rejected for absolute, traversal, NUL, backslash, or overlength forms.
- Deterministic added/modified/removed/renamed/copied classification. No active index, requested full work, unavailable/unsafe/non-fast-forward or over-file comparison, mismatched ancestry, default-branch change, target-byte limit, or missing reusable artifacts visibly falls back to full indexing.
- Every update constructs a complete inactive target version. Discovery, parsing, chunks, symbols, and the complete static call graph are rebuilt for correctness; unchanged 768-dimensional vectors are reused only under exact repository/version/commit/path/type/content/location/qualified-identity/model/preprocessing scope. Changed/new vectors are embedded, and deleted or renamed-away paths are absent from the target version.
- Vector and graph counts/fingerprints are validated before atomic activation. A stale, cancelled, unauthorized, incomplete, or failed replacement cannot replace the current active version and cleans only its own inactive scope.
- Questions continue against a prior valid active version while a replacement is queued, building, retryable, or failed. `not_indexed`, revoked, suspended, removed, and deleting states fail closed. Inactive/new and old/new evidence are never mixed.
- Authenticated `POST /api/v1/repositories/{repository_id}/reindex` requests a server-scoped full refresh. Repository detail/status adds indexed branch, active/remote SHA, requested/actual mode, fallback, delivery outcome, changed/reused/re-embedded counts, and graph-rebuilt state without exposing infrastructure or credentials.
- A 30-case Milestone 9 freshness corpus and 31 fixture-contract observations cover file changes, graph/citation freshness, duplicates, ordering, force pushes, fallbacks, failures, revocation, isolation, concurrency, replay, and deterministic repetition.
- The immutable model registry remains exactly `search_code`, `get_history`, and `find_callers`. No refresh, webhook, shell, network, repository-write, or Milestone 10 tool was added.

## Limits and policy defaults

| Control | Effective value |
| --- | ---: |
| Webhook raw body | 1 MiB maximum |
| Delivery ID | bounded validated GitHub delivery format |
| Comparison files | 300 maximum before full fallback |
| Discovered target bytes for incremental reuse | 64 MiB maximum before full fallback |
| Repository path | 4,096 UTF-8 bytes maximum |
| Embedding vector | finite normalized 768 dimensions |
| Index worker attempts | 3 maximum |
| Agent registry | exactly 3 read-only tools |

Existing parser, repository, clone, lease, tool-call, timeout, evidence, and output limits remain enforced server-side. Provider-, payload-, client-, or model-supplied values cannot widen repository, branch, installation, version, commit, filtering, deletion, or activation scope.

## Database, migration, API, and dependency changes

Alembic revision `4cafdf2faa66` follows `b83f2d8a6c41`. It extends repositories, deliveries, and indexing jobs with branch/generation/freshness references, requested and actual indexing modes, fallback and changed/reuse/re-embedding/graph state, retry/receipt metadata, foreign keys, bounded/nonnegative checks, and repository/receipt indexes. The active-job partial unique constraint now covers `running` only so newer durable work may supersede queued work without two conflicting activations.

The backend package/API version is `0.9.0`. No dependency changed, so both existing hashed production/development lockfile pairs remain unchanged. Redis messages still contain only an opaque job ID; PostgreSQL remains the source of job target, mode, generation, lease, progress, delivery, and activation truth.

## Verification evidence

| Gate | Actual result |
| --- | --- |
| Baseline | Clean local `main` and `origin/main` began at Milestone 8 commit `8f222dd2e9a7675c098cca4bd3687916a99461d3`; hosted GitHub Actions run `29652564767` for that exact SHA passed |
| Runtime | Python 3.13.14 local production baseline |
| Migration chain | PostgreSQL 18 downgrade from Milestone 9 head to `b83f2d8a6c41`, re-upgrade, full downgrade to base, clean upgrade through all revisions, `alembic check`, catalog inspection, and final `current` all succeeded; final head `4cafdf2faa66` |
| Complete backend suite | 328 passed, 0 failed, 0 skipped in 24.97 seconds using real PostgreSQL 18, Redis 8.8, standalone Qdrant 1.18.2, and the real pinned private embedding model |
| Branch-aware coverage | 90.74% combined statement/branch coverage from the exact CI working directory; the 90% gate and branch coverage remain enabled |
| Embedding service | 15 passed, 0 failed, 0 skipped in 2.22 seconds against the real offline immutable model; 92.24% coverage |
| Focused A→B flow | PostgreSQL/Redis/Qdrant/private-embedding integration passed: A remained active/queryable during B, B was inactive until validation, opaque queueing and change/reuse counts matched, activation switched code/caller evidence to B, old/deleted evidence disappeared, renamed paths were current, replay/stale/failure cases could not replace B, and an execution sentinel remained absent |
| Evaluation | 30 cases/31 explicitly labelled fixture-contract observations; changed-file, mode, delivery, activation, preservation, retry, graph, citation, and deterministic metrics are all 1.0; old-version and cross-repository leakage are zero; latency is unmeasured (`null`) |
| API startup | Actual Uvicorn API started on port 18009; liveness returned 200 `{"status":"ok"}` and readiness returned 200 with database/Redis/Qdrant `ready`; security headers were present; shutdown was clean |
| Worker startup | First probe correctly failed closed because the configured private embedding token did not match the surviving service. The authenticated retry emitted `worker_started`, then `worker_stopped` on interrupt; logs contained only safe configuration/lifecycle metadata |
| Quality | Ruff formatting/lint clean; strict mypy clean for 128 backend and 13 embedding-service source/test files |
| Dependencies | `pip check` found no broken requirements; audits of both hashed production lockfiles found no known vulnerabilities |
| Security/privacy | Signature/body-limit, authorization/revocation, cross-tenant, inactive-version, stale-worker, scoped-vector, opaque-Redis, and no-repository-execution regressions passed. Diff/log/secret scans found no real credential, payload, source, patch, prompt, answer, embedding, token, authenticated URL, or service/database URL disclosure |
| CI/Docker | CI and both production Dockerfiles were statically inspected and unchanged; pinned vulnerability scans, Python 3.13 images, hashed installs, and non-root assertions remain. Local Podman was not retried due the documented contradictory VM/socket host-runtime state |
| Live providers | No real GitHub App or hosted LLM credential was available. Signed controlled payloads and deterministic GitHub/agent fixtures passed, but live GitHub comparison/delivery and hosted-model acceptance are not claimed |

## Failures encountered and fixes

1. The first migration command supplied only a database URL, and application configuration correctly rejected missing mandatory test settings before touching the schema. The full test-only configuration was supplied; the complete PostgreSQL cycle passed.
2. Initial autogeneration used a duplicate enum check name and allowed the naming convention to prefix an explicit name twice. Both transactions rolled back. Stable explicit names were applied and the clean migration cycle passed.
3. A stale-worker unit assertion expected one inactive-vector cleanup, while the correct lifecycle performs pre-upsert cleanup and superseded cleanup. The assertion was corrected to validate both safely scoped calls.
4. A broad prior-active availability change accidentally allowed questions in `not_indexed`. It was narrowed to queued/building/retryable/failed replacement states that already have a valid active version; unindexed and revoked repositories fail closed.
5. The first signed A→B question flow reached the test app's unavailable/mismatched embedding provider. The controlled test now injects deterministic embeddings and semantic mappings while the full suite separately exercises the real pinned private model.
6. GitHub comparison `changes` represents line changes, not bytes. A first planner interpretation was corrected: the 64 MiB threshold is applied to discovered target file bytes after clone, never to provider line counts.
7. Metadata events for an already deleted or revoked repository could initially leave a retryable delivery. Processing now deterministically records `unauthorized`, matching immediate access revocation.
8. Running coverage from the repository root loaded statement-only defaults and reported 91.37%, which did not reproduce CI. Running from `backend/` enabled the configured branch policy and initially measured 89.10%. Focused assertion-bearing async service/webhook/repository tests covered security and terminal-state behavior that TestClient portal threads did not trace consistently; the exact CI-shaped run now passes 328 tests at 90.74%.
9. The first bounded worker startup used a test token different from the already-running private service and correctly failed with `embedding_model_not_ready`. The authenticated retry connected to all declared dependencies, emitted `worker_started`, and stopped cleanly. No code change was needed.

## Known limitations and deferred work

- Incremental mode selectively reuses vectors, but intentionally reparses the complete bounded target tree and rebuilds the full graph. This is correctness-first selective embedding, not a claim of fully incremental static analysis.
- GitHub compare metadata is advisory; the independently cloned target tree and server-owned active/default-branch state remain authoritative. Force-push and uncertain ancestry paths rebuild fully.
- Static caller analysis still cannot prove reflection, monkey patching, dependency injection, generated functions, runtime dispatch, or framework routing.
- Immediate access revocation is implemented. The complete final deletion SLA and retention workflow remain deferred to the dedicated later milestone.
- Controlled cryptographic fixtures are not proof of live GitHub delivery ordering, rate-limit behavior, installation-token policy, or production latency.
- Local container execution remains blocked by the host Podman runtime. The Milestone 8 base commit has green hosted image builds/non-root assertions; Milestone 9 hosted CI awaits a manual push.

## External acceptance still required

- Install a real least-privilege GitHub App on a controlled repository and verify signed pushes, comparisons, default-branch changes, installation removal/suspension, token scoping, rate limits, and replay/out-of-order behavior.
- Supply a real hosted-model credential in the API-only secret store and verify three-tool answers, injection resistance, freshness citations, latency, cost, timeout, and outage behavior.
- Run hosted CI for the local Milestone 9 commit, then validate managed PostgreSQL/Redis/Qdrant/private embeddings, deployment networking, telemetry/alerts, backup/restore, deletion drills, and incident response.
- Evaluate representative private repositories and webhook workloads; fixture-contract accuracy is structural evidence, not universal product accuracy or reliability.

## Production-readiness statement

Milestone 9 is a dependency-backed local freshness foundation, not a production SaaS. It must not be launched publicly until the unpushed commit passes hosted CI and the live GitHub, hosted-model, deployment, privacy, reliability, and remaining milestone gates are complete.

## Milestone 10 implementation

- Added the standalone `frontend/` React 19 + TypeScript + Vite application with a dark, responsive developer-product interface, route-level code splitting, semantic UI primitives, keyboard-visible focus, mobile navigation, loading/empty/error states, and no custom icon set beyond Lucide.
- Added a centralized typed API client. Browser access tokens remain in React memory only; refresh/logout use credentials-bearing requests and no token is persisted in localStorage, sessionStorage, URLs, logs, or analytics.
- Added sign-in, browser callback recovery, repository selection/connect, repository overview/reindex confirmation, indexing-status polling, repository question workspace, safe Markdown answers, and an evidence inspector. Citations use only server-returned evidence; external history links are restricted to `github.com`; no arbitrary source URL or path is fetched by the browser.
- Added production `FRONTEND_URL` validation and an OAuth completion redirect. The API writes the scoped HTTP-only refresh cookie and sends a 303 to `<FRONTEND_URL>/auth/callback` with no credential or OAuth query value. Production requires an HTTPS frontend origin that exactly matches an allowed CORS origin.
- Added locked npm dependencies, frontend lint/format/build/test/audit scripts, and a Node 22 GitHub Actions job. No frontend container, deployment configuration, repository tree browser, chat persistence, or Milestone 11 capability was added.

## Milestone 10 verification evidence

| Gate | Actual result |
| --- | --- |
| Frontend formatting, lint, strict TypeScript build | `npm run format`, `npm run lint`, and `npm run build` passed locally on Node 26.0.0. The production build emitted 14 assets; the entry bundle was 235.91 kB / 75.86 kB gzip and the largest lazy question-workspace chunk was 138.74 kB / 42.77 kB gzip. |
| Frontend tests | 13 Vitest tests across 6 files and 8 Chromium Playwright tests passed. Browser tests cover sign-in/callback/session expiry, repository list/overview/indexing, question answerability/evidence types, settings, focus restoration, Escape, mobile navigation, reduced motion, inert Markdown, trusted history links, long content, and four viewports. |
| Frontend dependencies | `npm ls --all`, full `npm audit --audit-level=high`, and production `npm audit --omit=dev --audit-level=high` completed with no known vulnerabilities. Playwright 1.61.1 is locked as a development-only dependency; CI installs Chromium only. |
| Backend integration affected by M10 | Formatting, linting, strict mypy, `pip check`, clean-database migration, and metadata consistency passed. The focused indexing suite passed 10 tests; the config/OAuth suite had previously passed 58 PostgreSQL-backed tests. |
| Full backend regression | 332 passed in 23.41 seconds with branch-aware coverage of 91.04%. The earlier five failures were caused by a pre-existing local `python -m app.worker` consuming the shared `repolume:indexing` Redis Stream/consumer group. Integration settings now use a test-only stream/group, and the correction run used an isolated Redis instance, disposable PostgreSQL database, Qdrant collection, and private embedding service. No production worker behavior changed. |
| Browser/viewport visual automation | Chromium Playwright passed 8 tests at 1440×900, 1024×768, 768×1024, and 390×844. Screenshots were reviewed locally and remain ignored. Review found and fixed Strict Mode aborts rendered as repository outage alerts, desktop evidence inspector placement, and visually link-like inert answer text. |

## Milestone 10 remaining external acceptance

- Configure the GitHub App callback at the public API URL and configure production `FRONTEND_URL`/`CORS_ORIGINS` to the exact deployed HTTPS browser origin. Then exercise the real browser OAuth flow; automated tests use mocked GitHub behavior and do not claim live provider verification.
- Run the amended frontend CI job after the local commit is manually pushed. Do not claim hosted verification before that run exists.

## Milestone 11 security/privacy audit

- Audited commit `d2ba86bd2f30d587028d40d4ee2c54466474b1c5` across API/auth, GitHub App/Google/public repositories, webhook/job/freshness state, PostgreSQL/Redis/Qdrant, clone/static analysis/private embeddings, RAG/agent/LLM, frontend, logs/errors, dependencies, workflows, images, and documentation.
- Confirmed no Critical issue. Remediated both High findings: arbitrary production LLM evidence destinations and stale cached-public authorization at new disclosure boundaries.
- Fixed Medium webhook semantics, clone cleanup, production validation, Google audience/authorized-party handling, frontend URL trust, CI action pinning, dependency update coverage, and the incorrect webhook-stage state transition.
- Explicitly deferred durable account unlink/deletion and automated retention to Milestone 13 because safe completion requires an idempotent PostgreSQL/Qdrant purge coordinator. Immediate denial, membership removal, installation/repository revocation, and scoped inactive cleanup remain active.
- No schema change was required. The existing eight-revision chain ends at `da6b47f8cd61`.

### Milestone 11 local verification

| Gate | Actual result |
| --- | --- |
| Audited runtime | Python 3.13.14 on macOS; Node 26.0.0 for local frontend verification |
| Focused security regression | 139 tests passed initially across GitHub/Google/public authorization, config, OIDC, clone, worker, agent, and RAG; the final GitHub webhook integration rerun passed 20 tests |
| Complete backend | 433 passed, 0 failed, 0 skipped in 42.66 seconds against disposable PostgreSQL 18, Redis 8.8, standalone Qdrant 1.18.2, and the real pinned private embedding service |
| Branch-aware coverage | 92.91%; the configured 90% threshold and branch coverage remained enabled |
| Migration | Clean PostgreSQL upgraded through all eight revisions to `da6b47f8cd61`; `alembic check` reported no new upgrade operations |
| API runtime | Uvicorn started successfully. Versioned liveness returned 200 `ok`; readiness returned 200 with PostgreSQL/Redis/Qdrant ready; unauthenticated `/auth/me` returned the safe 401 envelope and security headers |
| Embedding service | 15 passed with the real immutable offline model; 92.53% coverage |
| Frontend | Clean `npm ci`; 16 Vitest tests across 7 files passed; production TypeScript/Vite build passed; 8 Chromium Playwright tests passed |
| Dependencies | `pip check` found no broken requirements; both production Python lock audits and both full/production npm audits found no known vulnerabilities |
| Supply chain | Official action tags were resolved read-only and checkout/setup actions pinned to those commits; Dependabot covers backend/embedding pip, frontend npm, both Dockerfiles, and actions |
| Hosted CI/container scans | Local `podman info` failed because the configured AppleHV socket at `127.0.0.1:54657` refused connections; the VM was not restarted or repaired. Hosted CI has not run for these uncommitted changes. The workflow now separately names/scans API, worker, and embedding images, but green image evidence cannot be claimed yet |

### Failures encountered during Milestone 11 verification

1. The surviving disposable PostgreSQL directory was corrupt and its old process repeatedly logged missing catalog files. The process was stopped; a clean PostgreSQL 18 cluster/database was initialized and all migration/test evidence uses that replacement.
2. The first local `initdb` and loopback service commands were blocked by sandbox shared-memory/network policy. The exact commands were rerun with approval and succeeded.
3. The first frontend test command supplied Jest's unsupported `--runInBand` flag to Vitest. The documented `npm test` command then passed all tests.
4. The first parallel frontend build encountered duplicate ignored `node_modules/@types/* 3` directories from a contaminated local installation. CI's actual `npm ci` clean install removed them; the production build then passed. No source or lock change was used to hide the failure.
5. Initial health probes omitted the versioned `/api/v1` prefix and correctly returned safe 404 envelopes. The corrected liveness/readiness paths both returned 200.
6. The first post-remediation backend coverage run passed 420 tests at 90.40%. Focused assertion-bearing configuration, cleanup, and adversarial webhook cases raised the final suite to 433 tests and 92.91%, providing a safer platform margin without weakening exclusions or the threshold.
7. A final full-suite command set `QDRANT_URL` but omitted the integration guard `TEST_QDRANT_URL`; 419 tests passed and 13 Qdrant-dependent fixtures stopped at setup, so the partial 88.41% coverage result was correctly rejected. The CI-equivalent rerun supplied both runtime and test service variables and passed all then-current tests. Final diff review then rejected unsupported asyncpg `sslmode` configuration, and the post-fix suite passed all 433 tests at 92.91%.
8. The first final Python vulnerability-audit attempt was blocked by the local network sandbox after `pip check` passed. The exact two audit commands were rerun with network approval and both reported no known vulnerabilities.
9. Reusing one local incremental mypy cache across the backend and embedding service's same-named `app` namespace triggered a mypy 2.3.0 internal cache-fixup assertion. Fresh per-service cache directories, matching separate clean CI jobs, passed strict checking for all 137 backend and 13 embedding source/test files; no type error was reported.

## Next milestone gate

Milestone 11 only is authorized. Milestone 12 has not started. Hosted CI for the current audit work must be green before Milestone 11 can be declared fully complete.

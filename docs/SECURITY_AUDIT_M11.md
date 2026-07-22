# Milestone 11 Security and Privacy Audit

**Audit date:** 2026-07-22
**Commit audited:** `d2ba86bd2f30d587028d40d4ee2c54466474b1c5`
**Scope:** RepoLume through Milestone 10.5
**Status:** Local remediation and verification complete; hosted CI/container scan evidence pending

## Scope and exclusions

This audit covers the FastAPI API, authentication and session lifecycle, GitHub App and Google
OIDC integrations, public-repository import path, webhook ingestion, PostgreSQL state, Redis job
delivery, clone/discovery/parser worker, embedding service, Qdrant adapter, RAG and three-tool
agent, LLM providers, React browser client, dependency locks, CI, and production images.

Production deployment, billing, broad launch-level quotas, persistent chat history, repository
writes, and Milestone 12 work remain out of scope. No real provider credential or private
repository is required for automated verification.

## Trust boundaries and threat model

RepoLume treats browser input, OAuth callback parameters, provider responses, webhook bodies,
repository metadata and contents, Git history, model output, Redis deliveries, Qdrant payloads,
and every client-supplied identifier as untrusted. Environment configuration and the managed
service plane are privileged inputs but must still fail closed on unsafe production values.

Protected assets are provider and RepoLume credentials, authenticated sessions, personal data,
private repository source and derived evidence, repository authorization state, active-index
integrity, tenant-scoped vectors and graph data, and the availability of bounded worker and API
resources.

Principal attackers and failure modes are:

- an unauthenticated browser or cross-site origin attempting session theft, CSRF, enumeration,
  oversized requests, or webhook forgery;
- one authenticated user attempting cross-user, cross-installation, cross-repository, or
  cross-index access;
- a malicious connected repository attempting path escape, hook/config/protocol execution,
  parser exhaustion, prompt injection, tool redefinition, or citation fabrication;
- a malicious or compromised upstream returning mismatched identity, redirects, malformed data,
  oversized evidence, or provider-controlled URLs;
- a replayed, duplicated, stale, out-of-order, or semantically invalid signed webhook;
- a compromised model attempting to widen scope, select an unsupported tool, invent evidence, or
  disclose prompts and secrets;
- infrastructure misconfiguration that sends private evidence to an arbitrary endpoint, weakens
  transport/cookies/origins, shares queue namespaces, or exposes the private embedding service;
- dependency, action, image, or model supply-chain compromise; and
- partial failure across PostgreSQL, Redis, Qdrant, temporary storage, or external providers that
  leaves stale authorization or undeleted data.

The security invariants are: authorization is derived server-side and rechecked around external
work; public index sharing never grants membership; vectors are filtered by trusted source,
repository, version, commit, model, and preprocessing identity; connected code is never executed;
model output never controls scope or citation metadata; credentials never enter logs, durable
job messages, repository storage, URLs, or browser-readable persistence; and revocation fails
closed before later work or reads.

## Privacy data inventory

| Category | Purpose and location | Boundary, sharing, and external disclosure | Retention and deletion behavior | Content / personal-data risk |
| --- | --- | --- | --- | --- |
| Canonical users | PostgreSQL `users`; identity and display state | Current user through authenticated API; internal FK references | Cascades are defined for identities/sessions; no account-deletion API or verified SLA yet | Email, display name, provider login/ID and avatar URL are personal data |
| Provider identities | PostgreSQL `auth_identities` | Unique provider subject bound to one user; never client-authoritative | Deleted with the user; unlink operation is not exposed | Subject and verified provider email are personal data |
| OAuth state and PKCE/nonce | Keyed digests in `oauth_states`; raw verifier/nonce in short-lived HTTP-only cookies | Auth route only; provider/flow/user intent bound | Expires in 2–15 minutes, one-time use; periodic retention cleanup is not implemented | No repository content; pseudonymous auth metadata |
| Refresh sessions | Keyed token digests and family state in `refresh_tokens`; raw token in scoped HTTP-only cookie | User/session family only | Rotation, replay, logout, and expiry revoke; expired-row cleanup policy not automated | Authentication metadata, no repository content |
| GitHub installation state | PostgreSQL installations and memberships | Active installation plus fresh user membership; installation metadata shared by authorized members | Membership sync and signed suspension/deletion/removal revoke immediately; final purge SLA is not implemented | Account login/ID may be personal data; no source content |
| Repository metadata | PostgreSQL `repositories` and `user_repositories` | Private rows require installation membership; public rows may share one index but require per-user membership | Revoked/deleted states deny reads and work; public/private transition revokes; final cross-store purge is pending | Names, URLs, SHAs, branch, language, status; no source body |
| Repository source and chunks | Temporary mode-0700 clone and transient parser/embedding memory; Qdrant payload contains selected chunks | Worker-only clone; vectors require exact trusted scope | Clone removal is verified before activation and failures stop completion; stale/inactive vectors are scoped and deleted | Private source content; sent to private embedding service and selected evidence to configured LLM |
| Embeddings and vector metadata | Qdrant collection | Source/repository/version/commit/model/preprocessing filters; public vectors shared only behind user membership | Inactive/superseded cleanup exists; complete repository deletion orchestration is pending | Embeddings and chunk text can encode repository content |
| Symbols and call graph | PostgreSQL symbols/call edges | Repository plus active index version; caller tool reauthorizes | Inactive/superseded records are deleted; repository FK cascades | Paths, symbols, expressions and SHAs; derived repository content |
| Commit and pull-request evidence | Transient GitHub client/tool/LLM/browser response memory | Fixed repository paths and scoped installation token for private history | Not persisted by the implemented question flow | Messages, PR bodies, patches, authors and paths can contain source and personal data |
| Webhook metadata | Content-minimized `webhook_deliveries` | HMAC-authenticated ingress; server-bound installation/repository rows | Delivery IDs and normalized status retained; no automated retention schedule | IDs, action, ref and SHAs only; raw body is not stored |
| Indexing jobs and builds | PostgreSQL durable state | Repository-scoped worker claims and generation/lease checks | Retained for recovery/audit; cleanup schedule not automated | Counts, safe errors, SHAs, modes and fingerprints; no source body |
| Questions, answers, traces | Request/response memory only | Authenticated repository question request; selected evidence to configured LLM | Not persisted as chat history | Private question, answer, excerpts and tool metadata; high confidentiality |
| Logs and metrics | Process stdout/platform sink; content-free counters and IDs | Operators only | Platform retention is not configured in-repository | Internal UUIDs and operational timing; policy forbids content, emails, tokens, URLs and payloads |
| Browser state | React memory for access token/user/data; HTTP-only refresh cookie | Same browser session and allowlisted API origin | Memory clears on reload/logout; cookie clears symmetrically | Access token and returned personal/repository evidence; no local/session storage |
| External providers | GitHub, Google, configured LLM; private embedding service | Fixed auth/GitHub endpoints; production LLM traffic is limited to exact reviewed OpenAI/Gemini endpoints | Governed by provider policy; LLM requests disable storage where supported | Identity data to auth providers; selected private questions/evidence to LLM |

## Initial findings

Severity reflects impact and exploitability at the audited commit. A finding is not marked fixed
until its regression test and full verification gate pass.

### Critical

No confirmed Critical finding was identified in the initial code review.

### High

| ID | Finding | Initial evidence | Planned remediation |
| --- | --- | --- | --- |
| M11-H01 | Production accepts an arbitrary HTTPS LLM base URL, allowing privileged misconfiguration to exfiltrate questions and private repository evidence with the API key. | `Settings._validate_production_backing_services` checks only HTTPS; `create_llm_provider` selects by a suffix check and both clients send full evidence to that base URL. | Allowlist the exact supported OpenAI and Gemini base URLs in production and use exact endpoint identity for provider selection. |
| M11-H02 | Serving public-repository evidence can rely on a cached visibility check for up to the configured TTL after a repository becomes private. | `InstallationService._get_authorized_public_repository` returns the cached row; question start/final authorization and public history use that path. | Require fresh provider visibility and stable GitHub ID before and after every public question, and before public history access; revoke immediately on private/not-found/identity mismatch. |

### Medium

| ID | Finding | Initial evidence | Planned remediation or disposition |
| --- | --- | --- | --- |
| M11-M01 | Signed supported webhook families accept unsupported actions and unexpected field combinations; an unknown repository action can mutate metadata or queue work. | `GitHubWebhookPayload.action` is an arbitrary string and dispatch has event-only fallthrough. | Add per-event action allowlists, field-combination validation, collection bounds, exact signature syntax, and media-type validation. |
| M11-M02 | Temporary clone deletion suppresses every filesystem error and never verifies removal. | Both clone failure cleanup and normal `cleanup` use `shutil.rmtree(..., ignore_errors=True)`. | Make cleanup fail closed with a safe error, verify absence, and remove the clone before index activation. |
| M11-M03 | Production validation permits origin/callback path ambiguity, database connections without an explicit TLS mode, weak placeholder values, and a missing LLM key. | Production validators do not normalize every CORS origin, bind callbacks to trusted hosts/paths, inspect PostgreSQL TLS query parameters, or reject known placeholders. | Add exact origin/callback/transport/provider-secret validation and adversarial configuration tests. |
| M11-M04 | Google audience verification accepts a multi-audience token containing the client ID without enforcing exact audience/authorized-party semantics. | PyJWT membership-style audience validation is used without post-validation of `aud`/`azp`. | Require the configured client as the exact audience and reject multi-audience ambiguity. |
| M11-M05 | The browser renders a provider-supplied avatar URL directly and GitHub link validation does not reject credentials, nonstandard ports, query, or fragment state. | `AppShell` passes `avatar_url` to `img`; `trustedGitHubUrl` checks only protocol and hostname. | Restrict avatars to reviewed HTTPS provider hosts and require canonical GitHub evidence URLs before navigation. |
| M11-M06 | GitHub Actions checkout/runtime setup steps use floating major tags. | `actions/checkout@v6` and `actions/setup-node@v4` / `setup-python@v6`. | Pin every third-party action to an immutable reviewed commit SHA. |
| M11-M07 | Complete account deletion, identity unlinking, and retryable cross-store repository purge are not implemented. | Relational cascades exist and access revocation is immediate, but no durable deletion coordinator/API can prove PostgreSQL, Qdrant, and temporary-store completion. | Justified deferral: keep immediate denial, document retention, and design durable idempotent purge work in Milestone 13. A synchronous best-effort endpoint would weaken the existing deletion invariant. Add explicit membership-removal and revocation regression coverage now. |

### Low and informational

| ID | Finding | Disposition |
| --- | --- | --- |
| M11-L01 | A worker stage update incorrectly changes a related webhook delivery to `unauthorized` before completion. | Remove the unrelated transition and add a state regression test. |
| M11-L02 | Dependabot does not cover frontend npm, embedding-service pip/Docker, or all container manifests. | Add the missing ecosystems/directories. |
| M11-L03 | Broad launch-level rate limiting and automated retention schedules are absent. | Explicitly deferred to Milestone 13; existing body, concurrency, timeout, pagination, clone, parser, tool and evidence bounds remain mandatory. |
| M11-L04 | Browser-host CSP and immutable container image digests depend on the future deployment artifact. | Track for Milestone 12 deployment; API headers and version-pinned current build inputs remain enforced. |

## Controls verified during static review

- OAuth state, PKCE, and OIDC nonce values are independent, digest-only at rest, expiring,
  provider/flow bound, row-locked, and one-time use.
- Refresh tokens are random opaque values stored only as keyed digests; row-locked rotation,
  family replay revocation, logout invalidation, scoped HTTP-only cookies, and exact Origin checks
  are implemented.
- GitHub destinations and clone remotes are application-constructed; installation credentials are
  ephemeral, repository-restricted where source/history is read, absent from argv and storage, and
  not logged.
- Private and public repository authorization are distinct. Public vector sharing does not create
  `user_repositories` membership, and private rows require a current installation membership.
- Webhook HMAC covers the raw bounded body and is compared in constant time before JSON parsing;
  delivery uniqueness provides replay/idempotency control.
- Redis carries only opaque job UUIDs. PostgreSQL owns authorization, job state, lease,
  generation, target, and activation truth.
- Clone arguments, protocols, hooks, templates, submodules, LFS smudge, output, resource limits,
  containment and discovery are bounded; repository code is never imported or executed.
- Qdrant reads/writes/deletes require a typed source/repository/version scope; retrieval further
  binds commit, model and preprocessing fingerprints and rejects malformed payloads.
- The agent registry is immutable and contains exactly `search_code`, `get_history`, and
  `find_callers`; model arguments cannot supply repository/tenant scope, and server evidence owns
  citation metadata.
- React escapes ordinary text, Markdown is sanitized without raw HTML, arbitrary answer links are
  inert, access tokens stay in memory, and no browser storage or analytics path was found.
- Application and embedding logs use content-minimized structured metadata; access logs and HTTP
  client INFO logs are disabled, and error responses omit exception/upstream details.

## Verification record

### Finding disposition

| ID | Severity | Final disposition |
| --- | --- | --- |
| M11-H01 | High | Fixed. Production permits only the exact reviewed OpenAI and Gemini-compatible base URLs, requires a provider key, and selects Gemini by exact normalized endpoint identity. Adversarial URL, credentials/query, HTTP, missing-key, and placeholder tests fail closed. |
| M11-H02 | High | Fixed. Repository reads and the question/RAG agent force current GitHub public visibility; authorization/index identity is repeated before tools, before retrieved evidence returns to the provider, and before the response. Immediate public-to-private and removed-membership tests pass without expiring the cache. |
| M11-M01 | Medium | Fixed. Exact signature form, JSON media type, per-event action/field rules, and 500-repository list bounds precede durable dispatch. Invalid HMAC, duplicate, unsupported action, malformed combinations, oversize body/list, and replay tests pass. |
| M11-M02 | Medium | Fixed. Clone deletion no longer suppresses errors, verifies absence, reports only `clone_cleanup_failed`, and occurs before stale completion or activation. Filesystem/no-op deletion tests prove no activation. |
| M11-M03 | Medium | Fixed. Production validates callback paths/hosts, origin-only values, PostgreSQL/Redis TLS and credentials, private HTTPS services, provider keys, and placeholder critical secrets. |
| M11-M04 | Medium | Fixed. Google `aud` must exactly equal the configured client and `azp`, when present, must also match; multi-audience and mismatched authorized-party tokens are rejected. |
| M11-M05 | Medium | Fixed. Browser avatars are restricted to exact reviewed provider hosts over HTTPS with no credentials/port/fragment and no referrer. Evidence links must be canonical GitHub commit/PR URLs without credentials/query/fragment. |
| M11-M06 | Medium | Fixed. Checkout, Node setup, Python setup, and the existing scanner use immutable commit SHAs; update automation now covers each monorepo package/image ecosystem. |
| M11-M07 | Medium | Accepted deferral to Milestone 13. Immediate authorization denial is implemented, but unlink/account deletion and retention require a durable cross-store purge coordinator. A synchronous best-effort API would create a false deletion guarantee. |
| M11-L01 | Low | Fixed. Normal indexing stage updates no longer mark the related webhook unauthorized; the A-to-B integration asserts `completed`. |
| M11-L02 | Low | Fixed. Dependabot covers backend and embedding Python, frontend npm, backend and embedding Dockerfiles, and actions. |
| M11-L03 | Low | Deferred to Milestone 13. Existing ingress, parser, clone, queue, provider, evidence, concurrency, timeout, retry, and pagination limits remain enforced. |
| M11-L04 | Informational | Deferred to Milestone 12. Browser-host CSP and production registry digest policy depend on the deployment artifact; API CSP/security headers and source/action/model pins remain active. |

### Regression coverage added

- production LLM exfiltration destinations, service URL credentials/query state, missing provider
  key, database TLS, callback path/host, origin shape, placeholder and Google configuration;
- Google multi-audience and authorized-party ambiguity;
- user-specific public membership removal and immediate public-to-private revocation without a
  cache-expiry test mutation;
- authorization/active-index checks around agent tools and before evidence reaches a provider;
- exact/invalid webhook HMAC, JSON media type, action/field combinations, repository-list bounds,
  duplicate delivery, and correct completed delivery state;
- clone deletion no-op and filesystem failure plus worker non-activation ordering;
- exact provider avatar and canonical GitHub commit/pull-request URL handling; and
- production embedding placeholder/cache/offline-model invariants.

### Milestone 12 post-audit disposition

M11-L04's source-side browser CSP and production base-image digest items are implemented in Milestone 12: Vercel derives an exact API-origin `connect-src` policy, and both Python images pin the reviewed Python 3.13.14 multi-platform digest. This does not close live deployment verification. Actual edge headers, provider-built image identity/scans, private-service exposure, secret partition, monitoring, and recovery remain unverified until the blocked production rollout runs.

Existing regressions were rerun for OAuth state expiry/replay, PKCE/nonce, identity-conflict
linking, refresh rotation/reuse/family invalidation, cookie/Origin policy, cross-user and
cross-installation denial, installation suspension/removal, repository scope, opaque Redis,
Qdrant malformed/cross-scope payloads, stale index activation, prompt/tool injection, fabricated
citations, safe errors, and content-free logging.

### Exact local verification and results

The service-backed commands used disposable PostgreSQL 18 on loopback port 55435, Redis 8.8 on
56380 database 15, standalone Qdrant 1.18.2 on 56333 with a test-only collection, and the pinned
offline embedding service on 18111. No real GitHub, Google, LLM, repository, or production
credential was used.

```sh
.venv/bin/ruff format backend embedding_service
.venv/bin/ruff format --check backend embedding_service
.venv/bin/ruff check backend embedding_service
.venv/bin/mypy --config-file backend/pyproject.toml backend/app backend/tests
.venv/bin/mypy --config-file embedding_service/pyproject.toml embedding_service/app embedding_service/tests
.venv/bin/python -m pip check
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/alembic -c backend/alembic.ini check

cd backend
../.venv/bin/pytest
cd ..
HF_HUB_OFFLINE=1 REPOLUME_TEST_MODEL_CACHE=/private/tmp/repolume-m11-model-cache \
  .venv/bin/pytest embedding_service

cd frontend
npm ci
npm ls --all
npm run format
npm run lint
npm run build
npm test
npm run test:e2e
npm audit --audit-level=high
npm audit --omit=dev --audit-level=high
cd ..

.venv/bin/pip-audit --requirement backend/requirements.lock --disable-pip
.venv/bin/pip-audit --requirement embedding_service/requirements.lock --disable-pip
```

Observed results at this stage:

- clean-database upgrade applied all eight revisions through `da6b47f8cd61`; Alembic reported no
  metadata upgrade operations;
- strict mypy succeeded for 137 backend source/test files and 13 embedding source/test files;
- the focused initial security selection passed 139 tests, and the final GitHub auth/webhook
  integration rerun passed 20 tests;
- the complete backend run passed 433 tests in 42.66 seconds at 92.91% combined statement/branch
  coverage with the 90% gate unchanged;
- the embedding run passed 15 tests at 92.53% coverage using the real pinned offline model;
- the clean frontend graph passed 16 Vitest tests in 7 files, strict production build, and 8
  Playwright Chromium tests; full and production-only npm audits found zero vulnerabilities;
- `pip check` reported no broken requirements and both production Python lock audits found no
  known vulnerabilities; and
- an actual API process returned 200 for versioned liveness/readiness, reported PostgreSQL,
  Redis, and Qdrant ready, returned a safe 401 for unauthenticated identity, and emitted the
  expected security/request-ID headers.

The initial probes of `/health/live` and `/health/ready` omitted `/api/v1` and correctly returned
safe 404 envelopes; corrected probes passed. Initial and final frontend builds found duplicate
ignored local type directories in a contaminated `node_modules`; each `npm ci` clean install
reproduced CI and the final build/test/browser/audit chain passed. The first Vitest invocation used
an unsupported Jest flag; the repository's actual `npm test` command passed. A final backend attempt
omitted the fixture-required `TEST_QDRANT_URL`, leaving 419 passing tests and 13 setup errors; the
CI-equivalent rerun supplied both runtime and test service variables and passed all then-current
tests. Final diff review also found that the new PostgreSQL TLS validator accepted libpq's
unsupported-for-asyncpg `sslmode` parameter; it was narrowed to asyncpg's `ssl` parameter, covered
by a rejection test, and the final suite passed all 433 tests. The
first final Python audit attempt was blocked by sandbox DNS; the exact audits were rerun with network
approval and passed. Reusing one incremental mypy cache for both same-named service `app`
namespaces triggered a mypy 2.3.0 internal assertion; fresh per-service caches, matching isolated CI
jobs, passed strict checking. These failures are retained here rather than represented as successes.

### Residual and accepted risk

- There is no account/identity unlink API, automated retention schedule, or proven cross-store
  purge SLA. Immediate access denial is not the same as final deletion.
- A public visibility check still has an unavoidable provider-side race immediately after the
  check; RepoLume minimizes it by rechecking around each evidence disclosure and fails closed on
  provider errors.
- Clone cleanup can be prevented by a failed host filesystem. Activation is blocked, but an
  operator must securely destroy/remediate the isolated worker volume because the application
  intentionally does not persist or log private clone paths.
- Static analysis, deterministic provider fixtures, and mocked GitHub/Google responses are not
  evidence of live provider correctness, model quality, GitHub delivery reliability, or runtime
  call completeness.
- Broad quota/rate policy, deployment CSP, registry digest selection, managed backup/restore,
  alerting, and incident-response drills remain later milestones.

Hosted GitHub Actions has not run for the uncommitted Milestone 11 work. Therefore the workflow's
API/worker/embedding image builds, non-root assertions, and fixed-High/Critical Anchore scans are
not claimed green in this report. Local `podman info` also failed because the configured AppleHV
socket at `127.0.0.1:54657` refused connections; the VM was not restarted, repaired, or used as
substitute evidence. Milestone 12 has not started.

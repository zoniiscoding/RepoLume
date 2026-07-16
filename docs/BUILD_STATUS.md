# RepoLume Build Status

**Last updated:** 2026-07-16
**Authorized milestone:** Milestone 2 — Authentication and GitHub App
**Overall status:** Milestone 2 implementation and local acceptance verification complete
**Production readiness:** Not production-ready; authentication and GitHub authorization are implemented and tested with mocked GitHub responses, but a real GitHub App, hosted CI, frontend, deployment, and all Milestone 3+ product capabilities remain absent

## Implemented through Milestone 2

- GitHub App user authorization start/callback with HMAC-hashed, expiring, one-time OAuth state and S256 PKCE binding.
- Server-only authorization-code exchange, authenticated GitHub-user retrieval, and create/update synchronization of RepoLume users.
- Fifteen-minute RepoLume HS256 access tokens with issuer, audience, type, subject, timestamps, and unique token IDs.
- Thirty-day opaque refresh tokens held in scoped HTTP-only cookies; only keyed SHA-256 hashes are persisted.
- Transactional refresh-token rotation, parent/family tracking, expiry, revocation, replay detection, family invalidation, logout, and cookie clearing.
- Bearer authentication dependency that validates the token and reloads the user from PostgreSQL for every protected request.
- Login-time GitHub App installation and membership synchronization, including organization/member and user/owner roles, suspension state, and bounded membership freshness.
- Authorized installation listing and authorized repository synchronization through short-lived server-minted installation tokens.
- Authorization-aware installation and repository services that join the actor through a fresh membership and active installation; cross-user selectors return a non-enumerating not-found response.
- GitHub webhook raw-body HMAC-SHA256 verification, bounded headers/body, delivery-ID idempotency, safe acknowledgements, and durable content-free delivery state.
- Immediate access-revocation states for installation suspension/deletion and repository removal/deletion; unsuspension and repository addition can restore eligible state.
- Durable `queued` delivery state for push and non-deletion repository events without adding a Milestone 3 worker.
- Fixed GitHub API destinations, bounded pagination/timeouts, read-only installation-token permissions, no redirects, and no GitHub credential persistence.
- Versioned API endpoints required by Milestone 2; no frontend or Milestone 3 behavior was added.

## API surface

- `GET /api/v1/auth/github/start`
- `GET /api/v1/auth/github/callback`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `GET /api/v1/installations`
- `GET /api/v1/installations/{installation_id}/repositories`
- `POST /api/v1/webhooks/github`
- Existing liveness and readiness endpoints remain unchanged.

## Schema and migration

Alembic revision `f8eba5464d8c` follows the Milestone 1 revision `d2eea490eb59`. It adds:

- `oauth_states`: state hash, PKCE-verifier hash, expiry, use timestamp, unique state, and expiry/use index.
- `refresh_tokens`: token hash, user, family, parent, expiry/use/revocation fields, unique token hash, cascading user ownership, and active-family/user indexes.
- `installation_members.verified_at` for bounded fail-closed membership freshness.
- Safe action, GitHub installation ID, and GitHub repository ID fields on `webhook_deliveries`; webhook bodies remain unpersisted.

The full downgrade to base and upgrade from an empty PostgreSQL database succeeded. The database reported `f8eba5464d8c (head)`, and `alembic check` reported no new upgrade operations.

## Acceptance evidence

| Gate | Actual result |
| --- | --- |
| Runtime baseline | Python 3.13.14 locally; Python 3.13 container built and started |
| Database baseline | Disposable PostgreSQL 18.4; no SQLite substitution |
| Migrations | Full `downgrade base` and clean `upgrade head` succeeded through both revisions |
| Migration consistency | `f8eba5464d8c (head)` and `No new upgrade operations detected.` |
| Tests | 73 passed, 0 failed, 0 skipped in 2.00 seconds; 96.59% branch-aware coverage |
| Security regressions | OAuth replay/expiry/mismatch, refresh rotation/reuse/expiry, origin enforcement, cross-user/cross-installation denial, membership staleness, invalid/duplicate webhooks, suspension, deletion, and repository removal/addition passed |
| Formatting/lint/type checking | Ruff format, Ruff lint, and strict mypy passed on 63 files / 62 typed source files before documentation finalization |
| Dependency audit | `pip-audit` reported `No known vulnerabilities found` for the production lock |
| Host HTTP | Uvicorn started and stopped cleanly; live and ready returned HTTP 200; unauthenticated `/auth/me` returned the safe 401 envelope |
| Log safety | A callback containing an OAuth-code sentinel logged only the route path; host/test/container logs contained no code, token, cookie, credential, private key, webhook secret, or database URL |
| Container | Podman 6.0.1 built the Python 3.13 image; UID/GID `10001:10001`; read-only/no-capability container live and ready returned HTTP 200 |
| CI | Workflow updated for all required test-only configuration and locally reproduced; no hosted GitHub Actions run exists because no remote run is configured |

## Failures encountered and fixed

1. The first focused unit run found that an injected GitHub HTTP client did not inherit required API headers. The GitHub adapter now applies its allowlisted headers on every request. An older production-settings test was also updated for the new mandatory HTTPS OAuth callback.
2. The first PostgreSQL suite run found a test cookie-domain collision and migration tests missing the new required settings. Cookie setup was made unambiguous and the migration harness now supplies explicit test-only authentication configuration.
3. The same run showed the test HTTP client's INFO log included the OAuth callback query string. `httpx`, `httpx2`, and `httpcore` INFO logging is now suppressed centrally; a regression test and a real callback sentinel check verify that codes are absent from logs.
4. The unchanged 90% coverage gate initially failed because async service branches invoked through the test-client worker were incompletely traced. Direct PostgreSQL-backed service tests were added; the final suite reached 96.59% without excluding code or weakening the gate.
5. Ruff surfaced a deprecated HTTP 413 alias during the expanded webhook tests. The endpoint now uses the current `HTTP_413_CONTENT_TOO_LARGE` constant.

## External configuration still required

No real GitHub App credentials were available, so live GitHub sign-in, installation synchronization, token minting, and webhook delivery were not claimed.

Before a live environment can use Milestone 2, an operator must:

1. Create a GitHub App and configure its callback URL as the public API's `/api/v1/auth/github/callback` route.
2. Configure the webhook URL as `/api/v1/webhooks/github` and supply a high-entropy webhook secret.
3. Grant only read access to repository metadata, contents, and pull requests; subscribe to installation, installation repositories, push, and repository events; grant no repository write permission.
4. Put the App ID, client ID/secret, private key, webhook secret, independent RepoLume signing/hash secrets, and database URL in platform secret stores.
5. Configure the production frontend HTTPS origin, API trusted host, HTTPS callback, and exact CORS origin.
6. Execute a real sign-in/install/list/revoke/redelivery acceptance pass in a non-production GitHub organization before public traffic.

## Current limitations

- GitHub behavior is covered with mocked HTTP responses and signed local payloads, not live credentials.
- Membership is refreshed at login and accepted only within the configured freshness window; signed suspension/deletion/removal webhooks revoke access immediately.
- Push and repository-change deliveries are durably marked `queued`, but no worker exists until Milestone 3.
- No repository clone, connected-code execution, Redis, parser, embeddings, Qdrant, LLM, chat, product frontend, rate-limit service, hosted deployment, backup, alerting, or recovery drill exists.
- The container base image is not digest-pinned; release hardening remains a later milestone.

## Production-readiness statement

Milestone 2 meets its local implementation and automated acceptance gates and is a sound base for the next authorized milestone. RepoLume as a public SaaS is not production-ready and must not receive production GitHub credentials or private repository traffic until live GitHub configuration, hosted deployment controls, later repository-isolation functionality, and the remaining launch gates are implemented and verified.

## Next milestone

Milestone 3 — Durable jobs and safe cloning. It has not started and requires explicit authorization.

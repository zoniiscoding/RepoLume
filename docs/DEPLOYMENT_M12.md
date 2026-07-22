# Milestone 12 production deployment

**Status:** Repository preparation is locally verified. External provisioning and production acceptance are blocked because this workspace has no scoped Vercel, Railway, Neon, Qdrant Cloud, domain/DNS, Google, GitHub App administration, or hosted-model access. Nothing in this document is evidence that a production resource exists.

## Approved topology and trust boundaries

| Component | Provider/exposure | Durable state | Required invariant |
| --- | --- | --- | --- |
| Browser frontend | Vercel, public HTTPS | None | Exact API origin in CSP and `VITE_API_BASE_URL`; no secret variables |
| FastAPI API | Railway, public HTTPS | None | `SERVICE_ROLE=api`; one public domain; readiness gates PostgreSQL, Redis, Qdrant |
| Indexing worker | Railway, private, one replica initially | Temporary clones only | `SERVICE_ROLE=worker`; no OAuth/session/Google/LLM secrets; 900-second drain |
| Embedding service | Railway, private | Image-baked pinned model only | No public domain; bearer auth; local-files-only model loading |
| PostgreSQL | Neon | Durable relational authority | Pooled app URL; direct least-privilege migration URL; PITR/backups enabled |
| Redis | Managed private Railway Redis | Recoverable delivery hints | Authentication; persistence; PostgreSQL remains job truth |
| Vectors | Qdrant Cloud, HTTPS | Versioned citation chunks | API key; repository/version payload filters; snapshots enabled |
| Identity/source | GitHub App and Google OIDC | Provider-managed | Exact HTTPS API callbacks; minimum read-only GitHub permissions |
| Synthesis | Gemini-compatible reviewed endpoint | Provider-managed request processing | API service only; `store=false` behavior retained; no key in worker/frontend |

Railway services must use root directories `/backend`, `/backend`, and `/embedding_service`. Set their custom config paths to `/backend/railway.api.json`, `/backend/railway.worker.json`, and `/embedding_service/railway.json`. Only the API receives a generated/custom public domain. Use the Railway private names and explicit ports for Redis and embeddings.

## External change boundary

The following actions change provider/account state and must not be performed without authenticated scoped access, an approved account/project, billing/plan confirmation, and domain ownership:

1. Create the Vercel and Railway production projects and protected production environments.
2. Create Neon PostgreSQL, managed Redis, and Qdrant Cloud resources and enable paid backup/PITR features.
3. Create DNS records and issue HTTPS certificates.
4. Change GitHub App/OAuth and Google OIDC callback/webhook URLs.
5. Store or rotate production credentials.
6. Configure alert destinations, on-call recipients, log retention, and billing alerts.
7. Run the first migration or deploy against production data.

None of those actions has run in Milestone 12 from this workspace.

## Secret and variable partition

Generate every credential independently in its provider secret store. Never copy local `.env` values into production, expose a secret through Vite, or print values during validation.

| Setting group | API | Worker | Embeddings | Migration/release | Vercel build |
| --- | :---: | :---: | :---: | :---: | :---: |
| `DATABASE_URL` pooled TLS URL | yes | yes | no | no | no |
| `MIGRATION_DATABASE_URL` direct TLS URL | no runtime use | no | no | API pre-deploy only | no |
| `REDIS_URL` authenticated private URL | yes | yes | no | no | no |
| `QDRANT_URL`, `QDRANT_API_KEY` | yes | yes | no | no | no |
| `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` | yes | yes | no | no | no |
| GitHub client/webhook secrets and callback | yes | no | no | no | no |
| Google client ID/secret/callback | yes | no | no | no | no |
| `ACCESS_TOKEN_SECRET`, `TOKEN_HASH_SECRET` | yes | no | no | no | no |
| `EMBEDDING_SERVICE_URL`, shared service token | yes | yes | matching token only | no | no |
| `LLM_API_URL`, `LLM_API_KEY`, model controls | yes | no | no | no | no |
| `FRONTEND_URL`, `CORS_ORIGINS`, `TRUSTED_HOSTS` | yes | no | no | no | no |
| `VITE_API_BASE_URL` | no | no | no | no | public value only |

Set `APP_ENV=production`, `LOG_JSON=true`, and `DOCS_ENABLED=false` on API and worker. Set `SERVICE_ROLE=api` or `worker` exactly. Set the embedding environment to production, JSON logs true, cache `/models`, and local-files-only true. The API should use conservative database pool limits until Neon plan capacity is measured; API plus worker maximum connections must stay below the plan limit with operational reserve.

## Controlled release

1. Protect the GitHub `production` environment with required reviewers. Store only `RAILWAY_TOKEN`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, and `VERCEL_PROJECT_ID` as its secrets. Store project/service IDs and public origins as non-secret environment variables.
2. Disable unreviewed automatic production deploys in Vercel and Railway. Preview deploys must use non-production API/data settings.
3. Run the normal `CI` workflow for the intended full `main` SHA. Do not continue unless every job, image build, non-root assertion, dependency audit, and fixed High/Critical scan is green.
4. Dispatch `Deploy production` with that exact 40-character SHA. The workflow rechecks current `main` and successful CI, deploys embeddings, then the single worker, then the API. The API's Railway pre-deploy command runs `alembic upgrade head` with the direct migration credential. Vercel is built and deployed last.
5. Record the GitHub run ID, commit SHA, Railway deployment IDs/image identities, Vercel deployment ID, Alembic head, configuration review ticket, approver, timestamps, and smoke result. Do not record credential values.
6. The workflow runs `scripts/smoke-production.sh`. Follow with authenticated manual acceptance below.

Migrations must be additive/backward compatible before rolling services. A failed pre-deploy migration blocks the API rollout. Do not retry a failed migration blindly; inspect PostgreSQL/Alembic state and provider logs first. Database downgrades are not the default rollback mechanism.

## Acceptance checklist

No item below is currently verified in production.

- Vercel frontend and API use valid HTTPS with no mixed content; expected HSTS/CSP/CORS/cookie headers match the approved origins.
- API liveness and readiness work; worker and embeddings have no public domain and cannot be reached from the public internet.
- GitHub and Google login complete through exact callbacks; refresh rotation, logout, and cookie scope work in a real browser.
- A real authorized private GitHub App repository and a real public repository import can index and answer with current citations. A second user and installation are denied cross-tenant access.
- GitHub signed push, installation suspension/removal, repository removal, duplicate delivery, and invalid signature behavior match the durable states.
- Gemini answers through the exact reviewed endpoint without provider-side response retention beyond the approved policy.
- Restart the worker during one controlled active job: it stops taking new deliveries, the job either drains or is recovered from PostgreSQL heartbeat/lease state, and no duplicate activation occurs.
- Delete/revoke access and verify immediate API denial and existing implemented relational/vector cleanup behavior. Final account/identity cross-store purge remains deferred per D-060 and is a launch blocker.
- Inspect production logs with unique sentinels and confirm no cookie, token, OAuth code/state, key, prompt, repository content, private question, answer, or vector appears.

## Monitoring and alerts to configure

Use provider-native metrics/logs without request bodies. Alert destinations and thresholds require the actual plan baseline; the initial conservative policy is:

- API readiness failing for 5 minutes, HTTP 5xx above 2% for 5 minutes, or p95 above 2 seconds for 10 minutes.
- Worker crash loop, no heartbeat on a running job beyond `WORKER_ABANDONED_AFTER_SECONDS`, oldest queued job above 10 minutes, or repeated terminal/retry failures.
- PostgreSQL connection use above 70%, storage above 70%, sustained slow queries, or PITR/backup failure.
- Redis memory above 70%, eviction/nonzero rejected connections, persistence failure, or delivery backlog growth.
- Qdrant unavailable/latency errors, collection growth above plan threshold, snapshot failure, or payload-scope validation failures.
- Embedding readiness failure, model load failure, memory above 80%, or request timeout/concurrency saturation.
- GitHub webhook invalid-signature spikes, provider 401/403/429 spikes, OAuth callback failures, LLM 429/5xx/timeout spikes, and monthly provider spend at 50/75/90% of the approved budget.

Configure log/metric retention, alert recipients, and incident ownership before accepting traffic. No alert has been created or fired yet.

Cost safeguards are provider budgets and conservative initial capacity, not Milestone 13 product quotas: approve a monthly ceiling before provisioning, enable provider budget notifications where available, start with one worker and minimum production-safe plans, cap Railway replicas, keep model/tool/token/concurrency/time limits unchanged, review Neon/Qdrant/Redis storage growth weekly, and require an explicit change review before increasing plan size. If spend reaches the approved stop threshold, disable new indexing first while preserving authenticated read access and durable state; never delete data or weaken backups as an emergency cost action.

## Backup, restore, and rollback

- Neon: enable the selected plan's PITR and daily backup policy. Before launch, create a temporary recovery branch/restore point, restore to an isolated database, run safe schema/integrity queries, and record recovery point/time. Never point application/test traffic at the restore.
- Qdrant Cloud: enable scheduled collection snapshots on a plan that supports them. Restore one snapshot to an isolated test collection/cluster, verify vector count and required repository/version payload fields, then delete the isolated restore.
- Redis: enable provider persistence appropriate for a queue. Recovery may lose wakeups, but PostgreSQL reconciliation must re-enqueue durable due jobs; drill a Redis restart and verify this behavior.
- Runtime: use provider rollback to the prior known-good deployment SHA only when its code remains schema-compatible. Preserve the migrated database unless an explicitly reviewed forward fix is safer. Roll back frontend, API, worker, and embeddings in dependency-compatible order, then rerun smoke and authenticated acceptance.
- Raw repository clones, prompts, answers, tokens, and local model cache are intentionally not backed up.

No managed backup or restore drill has run. Recovery objectives, actual retention windows, plan limits, and measured restore time remain blocked on provider selection.

## Current blocker and required handoff

To continue Milestone 12, an authorized owner must provide interactive/scoped access to the selected Vercel, Railway, Neon, Qdrant Cloud, GitHub App, Google Cloud, DNS, and Gemini projects; approve any paid plans; identify the production frontend/API domains and alert recipient; and authorize creation/change of those external resources. After that, execute the controlled release and every acceptance/recovery item above. Do not begin Milestone 13 before Milestone 12 evidence is complete.

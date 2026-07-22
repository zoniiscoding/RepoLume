# Milestone 12 production incident and rotation runbooks

**Status:** Ready to configure; not exercised in production. Provider links, alert IDs, on-call owner, and observed baselines must be filled in after authorized provisioning. Incident evidence may contain only opaque IDs, timestamps, statuses, counts, timings, deployment identifiers, and safe error categories.

## Incident matrix

| Incident | Symptoms and checks | Safe remediation and rollback | Escalation and data-integrity concern |
| --- | --- | --- | --- |
| API outage | Public 5xx/timeout; liveness/readiness and Railway deployment/log status | Stop rollout, restore prior schema-compatible API SHA, keep worker running only if dependencies are sound, rerun smoke | Escalate after 5 minutes; never bypass readiness or trusted hosts |
| Worker outage/backlog/stuck job | No heartbeat, growing oldest-job age, crash loop; inspect PostgreSQL lease/job state then Redis | Restart one worker; allow lease recovery/reconciliation; roll back worker SHA if compatible | Escalate at 10-minute queue age; never manually mark a partial build active |
| PostgreSQL outage | API readiness fails and durable worker operations stop; inspect Neon status/connections/storage | Freeze deploys, reduce reconnect pressure, follow Neon failover, use isolated verified restore only when required | Database is authorization/job truth; never point production at an unverified restore |
| Redis outage | Readiness fails and wakeups stop; inspect provider status/persistence/memory | Restore Redis, then let PostgreSQL reconciliation enqueue due jobs | Redis loss must not change durable job target or authorization state |
| Qdrant outage | Readiness/question/index writes fail; inspect cluster and collection status | Restore provider service or isolated verified snapshot; retry bounded inactive builds | Never relax repository/version filters or activate unvalidated vector counts |
| Embedding outage | Private readiness/model-load/timeout failures | Roll back image or rebuild exact pinned model image; preserve inactive job state | Never enable public unauthenticated access, remote code, or alternate revision |
| Gemini outage | Question-only 429/5xx/timeout while readiness stays healthy | Keep bounded retries and return temporarily unavailable; roll back only a known API regression | Never switch endpoint/key without allowlist review or log evidence text |
| GitHub API outage | OAuth/install/history/clone provider failures or 429 | Preserve access state, honor retry headers, suspend new indexing if needed | Never use a personal token or skip current-access checks |
| Webhook outage | GitHub deliveries fail or durable receipt rate drops | Verify endpoint, TLS, secret generation, delivery headers/status; use GitHub redelivery after repair | Never disable HMAC/idempotency; ordering remains generation-based |
| GitHub login outage | Callback/provider exchange errors | Verify exact callback/client and provider status; restore prior secret during overlap | Never log code/state or weaken validation |
| Google login outage | Callback, audience, `azp`, nonce, or JWKS errors | Verify exact client/callback and provider status; restore prior secret during overlap | Never weaken identity checks or auto-link ambiguous identities |
| Failed deployment | Provider health gate or smoke fails | Halt later stages; provider rollback to prior recorded SHA; preserve migrated DB when compatible | Escalate immediately if schema compatibility is unclear; record deployment IDs |
| Failed migration | Alembic pre-deploy fails | Keep old API serving, inspect revision/transaction/locks, prepare a reviewed forward fix or verified restore | Never rerun blindly or execute ad hoc destructive SQL |
| Failed index replacement | Prior active version remains while replacement retry/stale/fails | Inspect safe code, dependency health, authorization, generation, cleanup; retry through durable state | Never mutate active state or mix old/new scopes |
| Backup failure | Provider backup/snapshot alert | Freeze risky migrations/deletions, restore policy, create and verify a fresh recovery point | Escalate immediately; no recoverability claim until isolated restore passes |
| Restore request | Confirmed loss/corruption or formal drill | Authorize point/time, restore to isolated target, verify schema/count/scope, approve controlled cutover | Preserve original; record RPO/RTO; never expose restored private data to tests |

For every incident, record start/end time, detecting alert, affected deployment/SHA, safe symptoms, decisions, approver, recovery evidence, and follow-up without repository content, prompts, credentials, provider response bodies, or browser cookies.

## Secret-rotation procedure

Use provider-supported overlap where possible: create the new credential, store it only in the owning service, redeploy/restart, verify content-free auth/error metrics and one bounded live operation, revoke the old credential, then monitor. Roll back only while the old credential remains valid. No production rotation has run.

| Secret | Owner | Invalidation and verification |
| --- | --- | --- |
| GitHub OAuth client secret | API | Verify a new login; existing RepoLume sessions remain until normal expiry/revocation |
| Google client secret | API | Verify a new Google exchange; existing RepoLume sessions remain |
| GitHub App private key | API and worker | Verify server-side installation-token minting in both; use GitHub overlapping keys; issued tokens expire naturally |
| GitHub webhook secret | API and GitHub App | Coordinate both ends; verify one signed delivery and invalid-old-signature rejection before completing rotation |
| `ACCESS_TOKEN_SECRET` | API | Existing access tokens become invalid, bounded by the 15-minute default lifetime |
| `TOKEN_HASH_SECRET` | API | Refresh families and outstanding OAuth states become unusable; all users must sign in again |
| Embedding service token | API, worker, embeddings | Coordinated restart causes temporary question/indexing unavailability because dual-token overlap is not implemented |
| Gemini API key | API | Verify a content-free grounded live result through the exact endpoint, then revoke old key |
| PostgreSQL credentials | API, worker, migration | Rotate separate roles; verify readiness and migration connection; drain old pools before revocation |
| Redis credentials | API and worker | Coordinated update/restart; PostgreSQL reconciliation recovers missed wakeups |
| Qdrant API key | API and worker | Verify readiness, scoped query, isolated inactive write/delete, then revoke old key |

If any new credential appears in output, logs, artifacts, screenshots, or tickets, stop, revoke it, remove the exposure, and investigate before retrying.

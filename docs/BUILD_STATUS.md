# RepoLume Build Status

**Last updated:** 2026-07-18

**Authorized milestone:** Milestone 7 — GitHub history and bounded agent orchestration

**Overall status:** Milestone 7 implementation and local dependency-backed verification complete

**Production readiness:** Not production-ready. Live GitHub App history, real hosted-model behavior, a hosted CI run of the local Milestone 7 commit, deployment/private networking, rate/usage controls, representative quality/load evaluation, telemetry/alerts, backups/restores, deletion, and later caller/security gates remain absent or unverified.

## Implemented through Milestone 7

- All Milestone 1–6 foundation, authentication/authorization, durable indexing, private immutable-model embeddings, active Qdrant indexes, and grounded code evidence behavior.
- Provider-neutral direct agent loop with strict structured `tool` or `final` decisions and an immutable registry containing exactly `search_code` and `get_history`.
- Server-owned limits: maximum four calls, eight seconds per tool, configurable provider/total deadlines, 32 KiB default per tool result, 64 KiB total evidence, three history commits, bounded messages/patches/paths, and 1,200 final output tokens.
- Rejection of unknown/extra/malformed/repeated/oversized calls; bounded provider and GitHub transient retries; cancellation propagation; explicit partial, insufficient, unsupported, and unavailable behavior.
- `search_code` reuses the Milestone 6 private query embedding, mandatory installation/repository/active-version/commit/model/preprocessing Qdrant scope, and deterministic evidence selection.
- `get_history` reauthorizes server-held repository context, mints a one-hour GitHub installation token restricted to the authorized repository ID, and uses fixed commit/detail/associated-PR endpoints. Commit count, messages, paths, patches, PR bodies, time, and retries are bounded. Tokens and history are not persisted.
- Strict GitHub response schemas and identity binding for owner/repository URLs, commit SHAs, parent SHAs, repository paths, commits, and pull requests. Model arguments cannot supply repository IDs, installation IDs, tokens, URLs, filters, limits, or commit overrides.
- Versioned `repolume-agent-v1` instructions with questions, source, commit messages, patches, and PR fields confined to canonical untrusted JSON.
- Deterministic current-trace evidence IDs and mixed `code`, `commit`, and `pull_request` citations. The model supplies IDs only; the server resolves all metadata, rejects fabricated commit/PR citations, deduplicates, orders by server evidence, and repeats authorization/active-index validation before returning.
- Content-free response trace with step, tool, argument fingerprint, status, duration, result count, safe failure code, and contribution flag. It contains no question, prompt, source, history body, patch, answer, token, or secret.
- Separate 27-case Milestone 7 evaluation contract covering code/history/mixed selection, merge/PR/file-change evidence, missing/misleading history, four prompt-injection locations, commit/PR/inactive-index isolation, fabricated citations, failures, bounds, revocation, unknown-tool prevention, and Milestone 8 refusal. The Milestone 6 baseline remains unchanged.
- No Milestone 8 caller tool, call-graph behavior, chat persistence, frontend, or unrelated product functionality.

## Resource and safety defaults

| Control | Default |
| --- | ---: |
| Agent tool calls | 4 maximum |
| Per-tool timeout | 8 seconds maximum |
| Provider / total timeout | 20 / 45 seconds |
| Tool result / total evidence | 32,768 / 65,536 bytes |
| History commits / paths | 3 / 20 |
| History message / patch | 2,048 / 8,192 bytes |
| Agent final output | 1,200 tokens |
| Existing code evidence | 6 items; 3/file; 12,288/item; 32,768 total bytes |

All values are validated server-side. A model or client cannot raise them.

## Database, API, and dependency changes

Milestone 7 adds no relational persistence. GitHub history, tool evidence, traces, questions, and answers exist only during one request. PostgreSQL remains authoritative for membership, repository authorization, and the active indexed build. The five-revision Alembic chain still ends at `d06a6455fcd7`; adding a no-op migration would misrepresent schema change.

The repository question response now supports `partially_answered`, discriminated mixed citations, tool-call count, total duration, and a safe trace. Existing authentication and non-enumerating repository semantics are unchanged.

No production/development dependency changed. The backend package version is `0.7.0`; existing hash-locked requirement files remain valid and unchanged.

## Verification evidence

| Gate | Actual result |
| --- | --- |
| Baseline | Clean local `main` and `origin/main` at `28c02a905182a608faf405daf0aca912854c942f`; hosted CI run 29523163340 for that SHA passed |
| Runtime | Python 3.13.14 local production baseline |
| Clean migration | New disposable `repolume_m7_test` database; all five revisions applied; `alembic check` reported `No new upgrade operations detected` |
| Complete backend suite | 282 passed, 0 failed, 0 skipped in 17.75 seconds with real PostgreSQL 18, Redis 8.8, standalone Qdrant 1.18.2, and real pinned private embeddings |
| Coverage | 93.35% combined statement/branch coverage; unchanged required threshold is 90% |
| Focused unit suite | 237 passed in 1.45 seconds |
| Question integration | 14 passed with PostgreSQL; includes authorized code/history, repository-restricted GitHub token, mixed citations, and tenant/index revocation cases |
| Startup/health | Actual Uvicorn application started on port 18007; liveness returned 200 `{"status":"ok"}`; readiness returned 200 with database/redis/qdrant `ready` |
| Embedding suite | 15 passed, 0 failed, 0 skipped in 1.38 seconds with the real immutable model; 92.24% coverage |
| Evaluation | 27 cases/27 explicitly labelled fixture-contract observations; all structural selection/recall/citation/refusal/isolation metrics 1.0, zero tool/unknown-tool violations, latency intentionally unmeasured (`null`) |
| Dependencies | `pip check` found no broken requirements; both production lock audits found no known vulnerabilities |
| Live providers | No real GitHub App or hosted LLM credential was available. GitHub and OpenAI adapters are mocked/structurally tested; live success is not claimed |
| Containers | Not retried. The prior contradictory Podman VM/socket state remains a host-runtime block; baseline hosted CI already contains image builds/non-root assertions |

## Failures encountered and fixes

1. The first broadened integration command omitted `TEST_QDRANT_URL`; 34 tests passed and all eight indexing tests failed during setup with the explicit disposable-Qdrant requirement. The retained standalone Qdrant 1.18.2 binary and real embedding service were started, then the evolving complete suite passed; the final exact tree passed 282/282. No test was skipped or weakened.
2. The first question integration assertion expected the Milestone 6 code-only response. It was updated to assert the Milestone 7 discriminated `code` citation, deterministic evidence ID, content-free trace, and tool count; all 14 question tests passed.
3. Strict mypy exposed that extending the broad GitHub client protocol would force unrelated OAuth/indexing fakes to implement history. History was split into a narrow `GitHubHistoryClientProtocol` with repository-restricted token minting, preserving least privilege and existing boundaries.
4. Initial GitHub history validation accepted any `github.com` URL shape. Repository/commit/PR identity checks and safe repository-path validation were added before evidence construction.
5. The Milestone 6 unsupported-question regression still classified history as future work. It was updated so history/commit/PR questions route to Milestone 7, while callers, runtime, and external state remain unsupported.

## External configuration still required

- A real least-privilege GitHub App installed on a controlled fixture repository for live commit/PR acquisition, permission, rate-limit, timeout, revocation, and token-scope acceptance.
- A real OpenAI API credential in the API-only secret store for structured tool/final behavior, refusal, prompt-injection, citation, latency, token/cost, and outage acceptance.
- Managed credentialed PostgreSQL, authenticated TLS Redis, authenticated HTTPS Qdrant, private authenticated embedding networking, hosted CI for the local commit, deployment, telemetry/alerts, backups/restores, deletion drills, and incident response.
- Representative public/private repository evaluation. The deterministic fixtures prove contracts and security boundaries, not universal historical or answer accuracy.

## Production-readiness statement

Milestone 7 is a tested local bounded-agent/history foundation, not a production SaaS. Do not send real private repository history or source to the hosted provider or launch publicly until live GitHub/LLM acceptance, hosted CI/deployment, rate/usage, deletion, monitoring, backup, and remaining security gates pass.

## Next milestone gate

Milestone 8 is not authorized and has not started. Caller analysis and `find_callers` remain absent.

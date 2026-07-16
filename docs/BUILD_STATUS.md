# RepoLume Build Status

**Last updated:** 2026-07-16

**Authorized milestone:** Milestone 6 — plain grounded RAG

**Overall status:** Milestone 6 implementation and controlled local acceptance complete

**Production readiness:** Not production-ready. Real hosted-LLM and GitHub App acceptance, hosted CI/deployment/private networking, production rate/usage controls, representative quality/load evaluation, telemetry/alerts, backups/restores, deletion, and later security gates remain absent or unverified.

## Implemented through Milestone 6

- All Milestone 1–5 foundation, GitHub authentication/authorization, durable job delivery, safe static ingestion, private immutable-model embeddings, scoped Qdrant persistence, and PostgreSQL-authoritative atomic index activation behavior.
- Authenticated `POST /api/v1/repositories/{repository_id}/questions` with non-enumerating tenant denial, active installation/repository authorization, complete active-build validation, and a second authorization/version check after network work.
- Central Unicode/NFC question normalization that preserves identifiers, folds whitespace, rejects empty/control input and questions outside 3 characters/4,096 bytes/512 estimated tokens, and computes a stable prompt-version-bound SHA-256 fingerprint.
- Existing private query-embedding path with exact model/revision/preprocessing compatibility and one finite normalized 768-dimensional result. Questions and query vectors never enter Redis or PostgreSQL.
- Typed Qdrant search with mandatory installation, repository, PostgreSQL-active index version, commit, model fingerprint, and preprocessing fingerprint filters. There is no raw/user-controlled filter path.
- Deterministic evidence sorting, tie-breaking, stable-hash deduplication, overlapping-range removal, per-file fairness, and configurable top-k/over-fetch/score/item/count/byte/context bounds. Malformed or empty citation payloads fail closed.
- Provider-neutral asynchronous synthesis protocol. Production selects OpenAI Responses with pinned `gpt-5.4-mini-2026-03-17`, strict JSON Schema, `store=false`, no tools/redirects, bounded output/timeouts/retries/concurrency, request correlation, safe failure classes, and no provider-body/prompt logging. The deterministic provider is test/development-only.
- Central prompt `repolume-grounded-v1` with fixed trusted system instructions and canonical JSON user data. Repository source/documentation and the user question are explicitly untrusted evidence; prompt-shaped content cannot gain tool, scope, network, or citation authority.
- Strict answer/answerability/uncertainty/evidence-ID validation and four explicit states: `answered`, `insufficient_evidence`, `unsupported_question`, and `temporarily_unavailable`.
- Independent citation resolution. Unknown/missing/inline-fabricated IDs fail closed; file path, exact lines, symbol/qualified symbol, indexed commit, and bounded excerpt come only from validated retrieved evidence.
- No persistence of questions, query embeddings, prompts, evidence contexts, answers, or provider responses. Logs contain only allowlisted IDs, safe counts/timing, states, and error categories.
- Versioned 20-case evaluation corpus and content-free aggregator for recall@k, citation precision/validity, no-answer accuracy, cross-repository leakage, unsupported claims, deterministic consistency, and latency.
- No Milestone 7 agent, tools, Git history, tool loop, chat/history, caller analysis, or frontend work.

## Retrieval and resource configuration

| Control | Default |
| --- | ---: |
| Question minimum characters / maximum bytes / estimated tokens | 3 / 4,096 / 512 |
| Retrieval top-k / over-fetch | 6 / 12 |
| Similarity threshold | 0.25 |
| Evidence per file / total | 3 / 6 |
| Evidence bytes per item / total | 12,288 / 32,768 |
| Total question timeout | 30 seconds |
| LLM connect / read timeout | 3 / 20 seconds |
| LLM attempts / concurrency | 2 / 8 |
| LLM output tokens / answer characters | 1,200 / 8,000 |

All values are validated together in settings and are server-owned. The existing embedding/Qdrant timeout and retry controls remain active.

## Database and dependency changes

Milestone 6 required no relational persistence and therefore no Alembic revision. PostgreSQL remains authoritative for repository access and the active searchable build. The existing five-revision chain ends at `d06a6455fcd7`.

No runtime or development dependency changed. The backend package version is `0.6.0`; all four hash-locked requirement files remain unchanged. Direct asynchronous HTTP uses the already locked `httpx` dependency instead of adding an LLM SDK.

## Controlled evaluation and live acceptance

The final run used Python 3.13.14, real PostgreSQL 18, Redis 8.8, official checksum-verified Qdrant 1.18.2, the real private pinned embedding model, and the actual FastAPI application. Four active controlled fixture vectors, one other-repository distractor, and one inactive-version distractor were physically present. No repository code was executed. No hosted LLM credential was available, so synthesis used the deterministic local provider; real hosted-model acceptance is not claimed.

| Gate | Actual result |
| --- | --- |
| HTTP/startup | API started; liveness 200; PostgreSQL/Redis/Qdrant readiness 200; authenticated question 200 with one server-owned citation; cross-user request 404 |
| Evaluation | 20 cases × 2 = 40 observations; recall@k 1.0000; citation precision 1.0000; citation validity 1.0000; no-answer accuracy 1.0000; unsupported-claim rate 0.0000; consistency 1.0000 |
| Isolation | Other-repository leakage 0; inactive-version path absent from every selected result; cross-user access denied |
| Latency | Mean 17.85 ms; maximum 24.51 ms on the documented local deterministic run |
| Logs | Sentinel scan passed for question, prompt-shaped source, other/inactive source, access/embedding values, prompts/answers/evidence/vectors |
| Redis | Controlled database was flushed before the run and retained only the existing opaque job-ID queue contract; question processing made no Redis writes |

## Final verification evidence

| Gate | Actual result |
| --- | --- |
| Runtime | Python 3.13.14 locally and in both production images; CI selects Python 3.13 |
| Backend quality | 112 files formatted; Ruff passed; strict mypy passed 111 source files using an isolated cache |
| Embedding quality | 13 files formatted; Ruff passed; strict mypy passed 13 source files |
| Backend suite | 212 passed, 0 failed, 0 skipped in 16.62 seconds; 91.41% coverage |
| Embedding suite | 15 passed, 0 failed, 0 skipped in 1.59 seconds; 92.24% coverage with the real immutable model |
| Focused M6 tests | 31 unit tests passed; expanded PostgreSQL/Qdrant authorization/isolation set passed 16/16 |
| Migrations | Full base downgrade/upgrade regression passed in the complete suite; `current` reported `d06a6455fcd7 (head)`; `alembic check` reported no upgrade operations |
| Dependencies | `pip check` reported no broken requirements; four Python 3.13 pip-compile resolution dry-runs exited 0; both production locks reported no known vulnerabilities |
| Local containers | Repeatable standalone verification is blocked by contradictory Podman VM state and refused sockets. One retained-process workaround did build both `python:3.13.14-slim-trixie` images and verify Python 3.13.14, Git 2.47.3, worker import, Uvicorn commands, and users `10001:10001` / `10002:10002`; the runtime could not remain available afterward |
| Image scans | The two archives from that one successful retained-process build passed checksum-verified Grype 0.112.0 fixed High/Critical scans; only three Medium CPython findings were listed. Reproducible hosted CI remains the authoritative pending container gate |
| Hosted CI | Workflow covers the complete Python 3.13/service/migration/test/audit/build/non-root/scan path with deterministic synthesis. No hosted run exists for this local-only commit; relevant checks were reproduced locally |
| Hosted LLM | Environment check reported `hosted_llm_credential=unavailable`; mock-adapter and deterministic end-to-end tests passed, but real hosted behavior remains pending |

## Failures encountered and fixes

1. The first live baseline returned no-answer accuracy 0.5714: lexical distractors made the deterministic provider answer three unsupported/missing cases. A general lexical-evidence requirement and broader runtime/current-production classification were added; the unchanged 20-case/two-pass run reached 1.0000.
2. Final contract review found citations lacked the required indexed commit and bounded supporting excerpt. The server-owned citation model/API now includes commit, qualified symbol, exact range, and bounded excerpt; focused and complete tests pass.
3. An expanded malformed-request test incorrectly expected Pydantic not to name the rejected extra field. Field location is safe; the test now asserts the private question value is absent, and the 16-test integration set passes.
4. Running two mypy processes concurrently against their default cache triggered a mypy 2.3.0 internal cache error. The backend check was rerun alone with `/private/tmp/repolume-m6-mypy-backend` and passed 111 files; no assertion or typing rule was weakened.
5. Local service access was initially sandbox-blocked; the exact approved commands were rerun with local-network permission.
6. Podman had two contradictory/hung VMs and refused its configured sockets. After stopping hung start/stop attempts, one retained-process workaround kept the existing VM alive long enough to build/inspect both images, but standalone follow-up commands again saw the VM as stopped and the socket refused. Per operator direction, no further Podman repair/restart/recreation was attempted. Repeatable local container verification is therefore blocked; hosted CI already contains both builds and non-root assertions.
7. Grype was first given `oci-archive:` for Podman's default Docker archive and correctly rejected the format. Retrying the same archives with `docker-archive:` passed both fixed High/Critical gates.
8. Podman could not reliably host Qdrant during service verification. The official Qdrant 1.18.2 ARM64 release archive was downloaded, its SHA-256 matched the release checksum `859f487e316ae1bda3b5d7c1e129a0a7344424d992503c188979ca6ac1b47253`, and that real server passed all retrieval/live tests.
9. The first live API child used `fork` and did not start cleanly around async/native service clients. The verifier switched to multiprocessing `spawn`; startup and both health endpoints passed.

## External configuration still required

- A real OpenAI API credential in the API-only secret store and a controlled hosted-model acceptance run covering refusal, prompt injection, citations, latency, cost, rate limiting, and provider outages.
- A real least-privilege GitHub App and controlled live OAuth/installation-token clone/webhook/index/question acceptance.
- Managed credentialed PostgreSQL, authenticated TLS Redis, authenticated HTTPS Qdrant, and private authenticated embedding networking.
- Hosted CI, deployment, registry/digest policy, representative repository quality/load tests, telemetry/alerts, backups/restores, deletion drills, and incident response.
- Removal of the exact Python 3.13.14 scan exception when a fixed 3.13 maintenance release is available.

## Current limitations

- The deterministic provider proves protocol, scope, refusal, citation, privacy, and operational behavior; it does not prove hosted answer quality.
- The 20-case synthetic fixture is a boundary baseline, not a representative product benchmark or capacity test.
- Static active-index evidence cannot establish runtime state, dynamic dispatch, dependency behavior, historical motivation, or callers.
- No prompt/answer history or RAG artifacts are persisted by design. No frontend exists to render citations.
- Access revocation blocks questions immediately, but full cross-store deletion remains later work.

## Production-readiness statement

Milestone 6 is a tested local plain-grounded-RAG foundation, not a production SaaS. Do not send real private repository traffic to the hosted model or launch publicly until real provider acceptance, a hosted CI/container run, and the remaining deployment, rate/usage, deletion, monitoring, backup, and security gates pass.

## Next milestone

Milestone 7 is GitHub history and bounded agent orchestration. It has not started and requires explicit authorization.

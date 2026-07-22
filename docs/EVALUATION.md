# RepoLume Evaluation

**Status:** Milestone 6–9 quality baselines remain unchanged. Milestone 11 adds adversarial security regressions, not a new answer-quality score. Fixture-contract observations remain structural expectations with `null` latency; they are not live GitHub reliability, hosted-LLM quality, or production performance evidence.

## Controlled corpus

`backend/evaluation/milestone6_cases.json` contains the original 20 cases over the committed Milestone 4 synthetic fixture and a separately scoped confusing repository. Its history case remains labelled unsupported because it records the Milestone 6 baseline and is not retroactively changed.

`backend/evaluation/milestone7_cases.json` remains the unchanged 27-case code/history baseline. `backend/evaluation/milestone8_cases.json` adds 20 contracts spanning same-file/direct/aliased/qualified/nested/constructor/method calls; probable, unresolved, duplicate-target, not-found, inactive-version, and cross-repository behavior; caller+code/history, impact wording, injection-shaped evidence, tool-loop resistance, runtime refusal, and fabricated citations. Cases contain expected paths/symbols/tool/citation types and caller identities without private repository content.

`backend/evaluation/milestone9_cases.json` adds exactly 30 named contracts: modified/added/deleted/renamed Python, changed imports, graph edge add/remove, ambiguity introduce/resolve, non-Python/ignored changes, unsupported-to-supported, duplicate/stale/out-of-order/force/non-default/replay/concurrent deliveries, full fallbacks, failed-active preservation, revocation before processing/activation, cross-repository mismatch, traversal, injection-shaped source, old-citation exclusion, deterministic repeat, manual conflict, and oversized comparison. The paired fixture artifact contains 31 observations because the deterministic case is repeated.

Coverage includes exact/similar/nested symbols, semantic implementation, signatures/decorators, behavior answerable from code, Markdown/plain documentation, Unicode, malformed-file recovery, prompt-injection-shaped documentation, missing symbols, runtime/external state, Git history reserved for Milestone 7, caller analysis reserved for Milestone 8, static-analysis limits, and a cross-repository distractor. The controlled search index contains four active fixture chunks; separate scopes contain a confusing other-repository chunk and an inactive-version distractor.

This corpus is synthetic, redistributable with this repository, and deliberately small. Whole-file controlled chunks mean some expected ranges are source truth for citation inspection rather than independent vector points. It is suitable for boundary/regression acceptance, not broad production relevance claims.

## Harness and metrics

`app.rag.evaluation` validates case/observation schemas and computes structural metrics without exact answer-text matching. Observation artifacts contain case IDs, paths, counts, answer states, content-free response fingerprints, leakage flags, claim counts, and latency only; they contain no question text, source, evidence excerpts, prompts, answers, embeddings, or credentials.

`app.indexing.evaluation` separately validates freshness case/observation schemas. Its observations contain only case ID, mode, delivery state, changed-type counts, activation/preservation/retry booleans, graph/citation/leakage flags, a content-free fingerprint, optional latency, and an explicit `observed` or `fixture_contract` label.

| Metric | Calculation |
| --- | --- |
| Recall@k | Observations with at least one expected path among the selected top-k evidence; no-relevant-evidence cases are handled by the refusal metric |
| Citation precision | Supported citations divided by citations; controlled deterministic observations use independently valid server citations as the support label |
| Citation validity | Citations resolving exactly to retrieved server-owned evidence divided by citations |
| No-answer accuracy | Non-answered cases returning their exact expected `insufficient_evidence` or `unsupported_question` state |
| Cross-repository leakage | Explicit leakage flags or retrieved paths intersecting the case's forbidden paths; target exactly zero |
| Unsupported-claim rate | Independently labelled unsupported material claims divided by material claims |
| Deterministic consistency | Repeated observations for each case with one identical content-free response fingerprint |
| Latency | Mean and maximum end-to-end API time in the documented local run |
| Tool-selection accuracy | Observations whose ordered tool sequence matches the case contract |
| Citation-type accuracy | Observations containing every expected code/commit/pull-request/caller citation type |
| Caller precision / recall | Correct expected caller identities divided by retrieved / expected caller identities |
| Exact-edge precision | Correct exact static edges divided by exact edges returned |
| Ambiguity / unresolved accuracy | Explicitly labelled ambiguous or unresolved cases classified safely |
| Inactive graph leakage | Observation flags for any caller evidence from a non-active version; target zero |
| Fabricated caller citations | Caller citations not backed by current server evidence; target zero |

The evaluator fails on duplicate case IDs, observations for unknown cases, or an empty run. Retrieval/citation/security tests separately cover score/range ordering, threshold/scope filters, malformed payloads, overlaps, fabricated citations, authorization, stale builds, provider failures, repeated calls, four-call/eight-second bounds, cancellation, GitHub response identity, and mixed citation ordering.

Run the aggregator from the repository root:

```sh
PYTHONPATH=backend .venv/bin/python -m app.rag.evaluation \
  --cases backend/evaluation/milestone7_cases.json \
  --observations /path/to/content-free-observations.json
PYTHONPATH=backend .venv/bin/python -m app.rag.evaluation \
  --cases backend/evaluation/milestone8_cases.json \
  --observations backend/evaluation/milestone8_fixture_observations.json
PYTHONPATH=backend .venv/bin/python -m app.indexing.evaluation \
  --cases backend/evaluation/milestone9_cases.json \
  --observations backend/evaluation/milestone9_fixture_observations.json
```

## Milestone 6 controlled baseline

Date: 2026-07-16. Runtime: Python 3.13.14 on local Apple Silicon. Services: PostgreSQL 18 on `127.0.0.1:55432`, Redis 8.8 on `127.0.0.1:56379`, official checksum-verified Qdrant 1.18.2 on `127.0.0.1:56333`, the real private `jinaai/jina-embeddings-v2-base-code` immutable-revision service on `127.0.0.1:18100`, and the actual FastAPI app on `127.0.0.1:18006`. Synthesis: deterministic test provider because no hosted credential was available. Configuration: top-k 6, over-fetch 12, score threshold 0.25, prompt `repolume-grounded-v1`.

Each of 20 cases ran twice (40 observations):

| Result | Actual value |
| --- | ---: |
| Recall@k | 1.0000 |
| Citation precision | 1.0000 |
| Citation validity | 1.0000 |
| No-answer accuracy | 1.0000 |
| Cross-repository leakage | 0 |
| Unsupported-claim rate | 0.0000 |
| Deterministic consistency | 1.0000 |
| Mean latency | 17.85 ms |
| Maximum latency | 24.51 ms |

The API also returned HTTP 200 for liveness/readiness, an authenticated answer with a server-resolved citation, and HTTP 404 for a cross-user request. Qdrant contained four authorized active points and isolated distractors outside the trusted filter. Operational-log sentinel inspection passed.

The first baseline exposed weak deterministic refusal behavior: no-answer accuracy was 0.5714 because lexical distractors were treated as answerable. The general deterministic test provider now requires meaningful question/evidence token overlap, while the Milestone 6 policy rejects runtime/current-production/history/caller/external-state classes before retrieval. Focused regressions passed and the unchanged 20-case/two-pass baseline reached 1.0000. This fixes local acceptance behavior; it says nothing about real hosted-model refusal quality.

## Milestone 7 controlled verification

Date: 2026-07-18. Runtime: Python 3.13.14 on local Apple Silicon. The complete backend run used PostgreSQL 18, Redis 8.8, standalone Qdrant 1.18.2, and the real private immutable embedding model. All 282 tests passed with 93.35% branch-aware coverage. Fourteen PostgreSQL-backed question tests included code retrieval, mocked on-demand GitHub commit/PR history, exact repository-token scoping, mixed response schemas, cross-user denial, suspension/deletion/stale-index behavior, malformed citations, and safe failures.

The 27-case Milestone 7 file and its explicitly labelled `fixture_contract` observations are schema-tested in CI and exercise every metric without inventing latency. These fixtures are structural expected outcomes, not observations from a live GitHub App or hosted model. Unit/integration fixtures establish tool selection and citation validity; they do not establish universal answer accuracy, historical exhaustiveness, semantic causation, latency under GitHub load, or provider quality.

## Milestone 9 freshness contract

The 30 cases and 31 explicitly labelled `fixture_contract` observations produce 1.0 for changed-file classification, mode selection, delivery state, activation, active preservation, retry classification, graph freshness, citation freshness, and deterministic consistency; old-version and cross-repository leakage counts are zero. All 31 observations are fixture contracts and both latency values are `null`. These are schema-checked expected invariants backed by independent unit/integration tests, not 100% claims about real GitHub delivery reliability or arbitrary repositories.

The controlled signed A-to-B integration independently indexes commit A, answers against A, applies a signed push that modifies/renames/adds/deletes files and changes callers, proves A remains queryable while B is queued, creates a complete inactive B, selectively reuses/re-embeds vectors, rebuilds/validates the graph, atomically activates B, verifies current citations/callers and old-path exclusion, and rejects replay. GitHub comparison/token responses and agent/embedding behavior are deterministic fixtures in that test; a separate integration still exercises the real private embedding service.

## Limits and next evaluation work

Milestone 11 security evaluation exercises exact production endpoint/configuration rejection, Google audience/authorized-party ambiguity, OAuth/session regressions, cross-user public membership removal, immediate public-to-private denial without waiting for cache expiry, webhook HMAC/replay/action/field/media/list bounds, clone-cleanup failure before activation, active-version stability around tools/LLM disclosure, Qdrant malformed/scope rejection, prompt/tool/citation injection, canonical external browser URLs, and content-free error/log behavior. These are assertion-bearing local tests against mocked providers and disposable services; they do not create a new model accuracy claim.

Milestone 8's 20 explicitly labelled `fixture_contract` observations exercise caller precision/recall, exact-edge precision, ambiguity/unresolved classifications, repository/active-version isolation, mixed tool selection, runtime refusal, loop bounds, injection resistance, and fabricated-citation rejection. All fixture metrics are deterministic structural labels; latency is intentionally `null`. Unit/integration tests independently exercise Tree-sitter extraction, conservative resolution, PostgreSQL graph lifecycle, authorization, active-version filtering, and API citations.

- No real OpenAI request ran, so hosted answer faithfulness, refusal quality, token/cost behavior, provider latency, and rate-limit behavior remain pending.
- Repeatable local container verification remains blocked by contradictory Podman VM/socket state and was not retried. Milestone 8 hosted run `29652564767` passed for `8f222dd2e9a7675c098cca4bd3687916a99461d3`; no Milestone 9 hosted run exists because this local commit is not pushed.
- The fixture is four active whole-file vectors plus isolated distractors, not a representative repository population or load test.
- Citation precision and unsupported-claim labels are deterministic structural labels in this baseline; independent human/provider evaluation remains necessary before launch.
- No live delivery-loss rate, event-to-activation distribution, runtime-call recall, MRR, p95/p99 latency, multilingual breadth, long-context capacity, representative freshness/caller quality, or end-user usefulness score is claimed.
- A controlled live GitHub App plus hosted-model run must produce content-free Milestone 7 observations before launch; do not synthesize or hand-author performance observations.
- Live GitHub App delivery/ordering/rate-limit behavior, representative large-repository change sets, and failure-recovery latency require external acceptance before launch.
- Milestone 10 adds client unit coverage for memory-only bearer attachment, safe error/protocol handling, sign-in, index status/failure presentation, question validation/error preservation, inert Markdown, and server-returned evidence inspection. These tests validate browser control flow and rendering contracts, not live OAuth, browser-cookie policy on a deployed origin, hosted GitHub, model quality, or end-user usefulness.

## Milestone 12 deployment evaluation status

The repository-side deployment contracts add assertion-backed coverage for exact Railway private-host validation and lookalike rejection, API/worker secret-role separation, migration-only configuration redaction, graceful worker termination registration, exact HTTPS frontend API build configuration, CSP origin binding, and SPA routing. The complete local regression passed 447 backend tests at 90.74% branch-aware coverage, 15 embedding tests against the real pinned model at 92.53%, 22 frontend unit tests, and 8 Chromium flows.

These results are not production measurements. No Vercel/Railway/Neon/Redis/Qdrant Cloud environment exists from this workspace, so there is no real cold start, p95/p99 latency, capacity, cost, alert, backup/restore, worker-restart, OAuth/webhook, live GitHub repository, Gemini, deletion, or rollback observation. Do not add synthetic numbers. Record those results only after executing `DEPLOYMENT_M12.md` against authorized production resources.

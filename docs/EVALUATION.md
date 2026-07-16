# RepoLume Evaluation

**Status:** Milestone 6 has a versioned 20-case retrieval/grounding corpus, a content-free metric aggregator, automated regressions, and one controlled two-pass local baseline. The baseline used the real PostgreSQL/Redis/Qdrant/private embedding/API path and deterministic test synthesis. It is not hosted-LLM answer-quality evidence and must not be presented as such.

## Controlled corpus

`backend/evaluation/milestone6_cases.json` contains 20 cases over the committed Milestone 4 synthetic fixture and a separately scoped confusing repository. Each case records category, question, expected answerability, relevant paths/symbols/ranges where stable, forbidden evidence, and an unsupported category where applicable.

Coverage includes exact/similar/nested symbols, semantic implementation, signatures/decorators, behavior answerable from code, Markdown/plain documentation, Unicode, malformed-file recovery, prompt-injection-shaped documentation, missing symbols, runtime/external state, Git history reserved for Milestone 7, caller analysis reserved for Milestone 8, static-analysis limits, and a cross-repository distractor. The controlled search index contains four active fixture chunks; separate scopes contain a confusing other-repository chunk and an inactive-version distractor.

This corpus is synthetic, redistributable with this repository, and deliberately small. Whole-file controlled chunks mean some expected ranges are source truth for citation inspection rather than independent vector points. It is suitable for boundary/regression acceptance, not broad production relevance claims.

## Harness and metrics

`app.rag.evaluation` validates case/observation schemas and computes structural metrics without exact answer-text matching. Observation artifacts contain case IDs, paths, counts, answer states, content-free response fingerprints, leakage flags, claim counts, and latency only; they contain no question text, source, evidence excerpts, prompts, answers, embeddings, or credentials.

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

The evaluator fails on duplicate case IDs, observations for unknown cases, or an empty run. Retrieval/citation/security tests separately cover score/range ordering, threshold/scope filters, malformed payloads, overlaps, unknown citations, altered inline citations, authorization, stale builds, and provider failures.

Run the aggregator from the repository root:

```sh
PYTHONPATH=backend .venv/bin/python -m app.rag.evaluation \
  --cases backend/evaluation/milestone6_cases.json \
  --observations /path/to/content-free-observations.json
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

The first baseline exposed weak deterministic refusal behavior: no-answer accuracy was 0.5714 because lexical distractors were treated as answerable. The general deterministic test provider now requires meaningful question/evidence token overlap, while the centralized unsupported policy rejects runtime/current-production/history/caller/external-state classes before retrieval. Focused regressions passed and the unchanged 20-case/two-pass baseline reached 1.0000. This fixes local acceptance behavior; it says nothing about real hosted-model refusal quality.

## Limits and next evaluation work

- No real OpenAI request ran, so hosted answer faithfulness, refusal quality, token/cost behavior, provider latency, and rate-limit behavior remain pending.
- Repeatable local container verification is blocked by contradictory Podman VM/socket state; hosted CI remains pending. This does not affect the host-run service baseline above.
- The fixture is four active whole-file vectors plus isolated distractors, not a representative repository population or load test.
- Citation precision and unsupported-claim labels are deterministic structural labels in this baseline; independent human/provider evaluation remains necessary before launch.
- No MRR, p95/p99 latency, multilingual breadth, long-context capacity, freshness, history, caller/tool selection, or end-user usefulness score is claimed.
- Milestone 7 must add history/tool cases without changing or retroactively relabelling this Milestone 6 baseline.

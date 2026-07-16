# RepoLume Evaluation

**Status:** Static-ingestion plus embedding/vector-index integrity methodology and fixtures exist through Milestone 5. There is still no public retrieval implementation, labelled question corpus, RAG answer system, or product-quality score. Deterministic embedding, vector isolation, and activation evidence must not be presented as retrieval relevance or answer accuracy.

## Milestone 5 ingestion/index fixtures

A generated, operator-controlled local Git repository is used for end-to-end ingestion-boundary testing. It contains supported Python/Markdown files, prompt-injection-shaped text, and Python that would create an external marker if executed. The API durably queued it; the worker cloned, discovered, statically parsed, symbolized, chunked, called the real private pinned model in authenticated bounded batches, wrote/validated a real inactive Qdrant version, atomically activated PostgreSQL state, and deleted the clone. The marker remained absent, Redis contained only `job_id`, repeated upserts were deterministic/idempotent, and the repository became searchable only after exact count/metadata validation.

A committed rich fixture covers sync/async functions, classes/methods, nested/duplicate names, decorators, multiline positional-only/keyword-only signatures, annotations, docstrings, imports/aliases/relative imports, Unicode, malformed recovery, Markdown hierarchy/fences/prompt-shaped text, and plain documentation. Tests verify exact ranges, stable hashes, canonical preprocessing fingerprints, deterministic 768-dimensional L2-normalized embeddings for identical input/model state, UUIDv5 point identity, complete citation payload metadata, cross-repository/version filters, and repeated identical output.

Replacement scenarios establish integrity rather than relevance: a deliberately failed replacement leaves the old active version/count queryable and cleans only the failed inactive scope; a successful replacement activates the complete version before scoped superseded cleanup. Collection mismatch, wrong dimensions, non-finite/non-normalized/missing/extra embeddings, count/metadata mismatch, stale activation, duplicate delivery, authorization revocation, and scope mismatch all fail closed.

These are ingestion, model-contract, vector-isolation, and activation results—not a retrieval benchmark. The fixtures have no relevance labels and no search ranking exists, so no MRR, recall, citation, faithfulness, latency-at-scale, or answer-quality score has been measured.

## Objectives

Evaluation must answer four questions:

1. Does retrieval find the relevant repository evidence?
2. Do answers make only supported claims and cite that evidence correctly?
3. Does orchestration select the right bounded tools and refuse unsupported questions?
4. Do authorization and index-version controls prevent any cross-repository leakage?

## Planned fixture corpus

Milestone 6 will select one or more redistributable retrieval fixtures and pin exact commit SHAs. Fixtures must include:

- Multiple Python modules with same-file, directly imported, module-qualified, probable-method, and deliberately unresolved calls.
- Markdown documentation that is both accurate and intentionally stale relative to code.
- Similar symbol names and cross-file flows.
- Git history and pull requests with documented motivation, plus changes where motivation is absent.
- Malicious instructions in comments, README text, commits, and pull-request content.
- A second isolated repository containing confusingly similar text to test tenant leakage.

Fixture provenance, license, commit SHA, supported file set, and any synthetic modifications will be recorded before use.

## Question set

Create at least 20 versioned questions with expected evidence and acceptable answer behavior across:

- Exact symbol location.
- Semantic implementation search.
- Cross-file flow.
- Similar names.
- Documentation lookup.
- Code/documentation conflict.
- Same-file and imported callers.
- Dynamic/unresolved calls.
- Commit history and pull-request reasoning.
- Missing historical intent.
- No-answer and runtime-only questions.
- Prompt-injection content.

Each case will define the repository/version, question, expected tool(s), relevant source IDs/ranges, allowed claims, required limitations, and expected answer status.

## Metrics

| Metric | Planned calculation |
| --- | --- |
| Recall@k | Proportion of cases where at least one expected evidence item appears in the top `k` retrieved items |
| Mean reciprocal rank | Mean inverse rank of the first relevant item for cases with a defined relevant set |
| Citation correctness | Supported cited claims divided by evaluated cited claims |
| Citation completeness | Material supported claims with a citation divided by material supported claims |
| Tool-selection correctness | Cases using the expected minimal tool set without prohibited/unnecessary tools |
| Unsupported-answer refusal | Unsupported cases returning an explicit non-answer without invented repository facts |
| Cross-repository leakage | Retrieved items from any repository/version outside the authorized filter; target is exactly zero |
| Stale-index detection | Stale cases correctly labelled with indexed SHA context |
| Average tool calls | Mean tool calls per question, with a hard maximum of four |
| End-to-end latency | Median and tail latency under a documented environment and corpus size |

Numeric thresholds will be established after the first honest baseline; they will not be reverse-engineered to make a weak system pass.

## Evaluation procedure

1. Build fixture indexes from pinned commits using the same production code paths.
2. Record configuration, model identities, dimensions, chunking version, index version, hardware/service environment, and run timestamp.
3. Run deterministic retrieval checks separately from LLM synthesis checks.
4. Mock providers for orchestration unit tests and use configured real providers only for explicitly labelled end-to-end evaluation.
5. Inspect citations against source line ranges and historical records.
6. Run cross-user, cross-repository, inactive-version, and deleted-version isolation cases.
7. Preserve raw metric artifacts without private repository content and summarize results here.
8. Record failures and regressions; do not omit failed cases.

## Result history

Milestone 5 executed deterministic model and index-integrity tests only: the real-model service suite passed 15/15 with 92.24% coverage and the backend suite passed 166/166 with 90.76% coverage, including real Qdrant isolation and controlled indexing. No retrieval or answer evaluation has been executed, and there are no valid product-quality scores to report.

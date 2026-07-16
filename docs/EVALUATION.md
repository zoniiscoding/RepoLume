# RepoLume Evaluation

**Status:** Static-ingestion methodology and fixtures only. Python parsing and transient chunk construction exist through Milestone 4, but there are no embeddings, vector index, retrieval system, labelled question corpus, or product-quality scores. Parser/chunker correctness and security evidence must not be presented as retrieval accuracy.

## Milestone 4 static-ingestion fixtures

A generated, operator-controlled local Git repository is used for end-to-end ingestion-boundary testing. It contains three supported Python/Markdown files, including Python that would create an external marker if executed. The API durably queued it; the worker shallow-cloned, discovered, statically parsed, symbolized, chunked, persisted safe summaries/symbol metadata, and deleted it. The marker remained absent, the duplicate Redis delivery was a no-op, and a forced parser failure also cleaned the clone.

A committed rich fixture covers sync/async functions, classes/methods, nested/duplicate names, decorators, multiline positional-only/keyword-only signatures, annotations, docstrings, imports/aliases/relative imports, Unicode, malformed recovery, Markdown heading hierarchy/fences/prompt-shaped text, and plain documentation. Explicit tests verify exact one-based ranges, stable hashes, deterministic ordering, CRLF/LF normalization, large-unit policy, safe classifications, and repeated identical output.

These are parser/chunker behavior and security results, not a retrieval benchmark. Chunks are transient, the fixtures have no relevance labels, and no MRR, recall, citation, faithfulness, latency-at-scale, or answer-quality score has been measured.

## Objectives

Evaluation must answer four questions:

1. Does retrieval find the relevant repository evidence?
2. Do answers make only supported claims and cite that evidence correctly?
3. Does orchestration select the right bounded tools and refuse unsupported questions?
4. Do authorization and index-version controls prevent any cross-repository leakage?

## Planned fixture corpus

Milestone 6 will select one or more redistributable fixture repositories and pin exact commit SHAs. Fixtures must include:

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

No retrieval or answer evaluations have been executed. There are no valid quality scores to report.

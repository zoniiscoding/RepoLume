# RepoLume Build Status

**Last updated:** 2026-07-18

**Authorized milestone:** Milestone 8 — static Python call graph and caller analysis

**Overall status:** Milestone 8 implementation and local dependency-backed verification complete

**Production readiness:** Not production-ready. Static caller analysis is deliberately incomplete for dynamic Python behavior, and live GitHub App/hosted-model acceptance, a hosted CI run of this local commit, deployment, monitoring, backups/restores, deletion, representative quality/load evaluation, and later freshness controls remain absent or unverified.

## Implemented through Milestone 8

- All Milestone 1–7 foundation, authorization, durable indexing, private immutable-model embeddings, active-version Qdrant retrieval, grounded citations, GitHub history, and bounded agent behavior.
- Inert Tree-sitter call-site extraction for supported Python files. RepoLume does not import repository modules, evaluate expressions, run type checkers, install dependencies, invoke repository commands, or execute repository code.
- Deterministic, repository/version/commit-scoped symbol identities and call edges with caller/callee symbols, exact call-site lines, bounded call expressions, resolution category, confidence, and stable graph fingerprints.
- Conservative resolution for same/nested-file calls, direct and aliased imports, relative imports, module-qualified calls, constructors, `self`/`cls` methods, and unique probable methods. Wildcard imports, dynamic attribute access, unknown callbacks, ambiguous receivers, and other runtime-dependent behavior remain unresolved or ambiguous.
- Atomic graph lifecycle inside the existing full-index build. Symbols and call edges are written only to the inactive version, graph counts and fingerprint are re-read and validated before readiness, activation requires validated vectors and graph, and failed/superseded versions are cleaned with repository/version-scoped cascades while the previous active version remains available.
- Immutable agent registry containing exactly `search_code`, `get_history`, and `find_callers`. Existing four-call, eight-second per-tool, provider/total deadline, evidence-byte, repeated-call, and output-token limits remain unchanged.
- `find_callers` reauthorizes the user and repository, derives the active version and commit from PostgreSQL, resolves one exact target identity, and returns only deterministic bounded direct callers from that repository/version/commit. Model arguments cannot provide repository, installation, version, commit, SQL, graph filters, traversal depth, URLs, or arbitrary limits.
- Discriminated `caller` citations whose file paths, symbols, definition/call-site ranges, commit, version, confidence, resolution, and static-analysis limitation are rebuilt from current-request trusted evidence. Fabricated or altered relationship references are rejected.
- Versioned `repolume-agent-v2` instructions require `find_callers` for dependency questions, preserve uncertainty, prohibit guaranteed runtime-impact claims, and keep source, comments, symbol names, call expressions, questions, and tool evidence inside untrusted JSON boundaries.
- Eight-case Milestone 8 fixture contract for exact, probable, ambiguous, unresolved, inactive-version, and fabricated-citation behavior. The prior Milestone 6 and 7 baselines remain unchanged.
- No Milestone 9 incremental indexing, changed-file graph updates, push ordering, freshness handling, frontend, deployment, or billing work.

## Resource and safety defaults

| Control | Default |
| --- | ---: |
| Call sites per file / build | 10,000 / 100,000 |
| Stored call expression | 2,048 bytes maximum |
| Direct callers returned | 20 maximum |
| Agent tool calls | 4 maximum |
| Per-tool timeout | 8 seconds maximum |
| Provider / total timeout | 20 / 45 seconds |
| Tool result / total evidence | 32,768 / 65,536 bytes |
| Agent final output | 1,200 tokens |

All values are validated server-side. Model or client output cannot expand repository scope, index scope, result limits, traversal, or time budgets.

## Graph, database, API, and dependency changes

Alembic revision `b83f2d8a6c41` follows `d06a6455fcd7`. It extends `call_edges` with the exact call range, bounded expression, stable call-site fingerprint, and repository/version/site uniqueness; adds graph counts, fingerprint, and validation state to `repository_index_builds`; and adds safe graph progress counts to `indexing_jobs`. Caller/callee lookup remains indexed and foreign-key scoped through the versioned symbol table. Nonnegative and line-range checks reject invalid persisted state.

The repository-index status response exposes safe graph counts. Repository-question responses now support trusted `caller` citations alongside code, commit, and pull-request evidence. No new public endpoint was required.

The backend package version is `0.8.0`. No dependency changed, so the existing hash-locked backend and embedding-service requirement files remain unchanged and valid.

## Verification evidence

| Gate | Actual result |
| --- | --- |
| Baseline | Clean local `main` and `origin/main` at `54f847c2bcc2618a452903feab4d4e0a1ea4707b`; hosted CI run 29650105386 for that SHA passed |
| Runtime | Python 3.13.14 local production baseline |
| Migration chain | PostgreSQL 18 full downgrade to base, clean upgrade through all six revisions, Milestone 8 downgrade to `d06a6455fcd7`, re-upgrade to `b83f2d8a6c41`, and `alembic check` all succeeded; final head is `b83f2d8a6c41` |
| Complete backend suite | 297 passed, 0 failed, 0 skipped in 19.08 seconds with real PostgreSQL 18, Redis 8.8, standalone Qdrant 1.18.2, and the real pinned private embedding model |
| Coverage | 92.98% combined statement/branch coverage; required threshold remains 90% |
| Question integration | 20 passed; includes authorized and mixed caller citations, target ambiguity, graph unavailability, cross-user denial, and explicit other-repository/inactive-version distractors |
| Indexing pipeline | 8 passed; includes persisted edges and statistics, graph validation before activation, failed replacement preservation, and superseded cleanup |
| Embedding suite | 15 passed, 0 failed, 0 skipped in 1.37 seconds with the real immutable model; 92.24% coverage |
| Evaluation | 20 cases/20 explicitly labelled fixture-contract observations; caller precision/recall, exact precision, ambiguity/unresolved accuracy, tool/citation validity, and deterministic consistency are 1.0; inactive leakage and fabricated caller citations are zero; latency is intentionally unmeasured (`null`) |
| API/worker startup | Actual Uvicorn API started on port 18008; liveness returned 200 `{"status":"ok"}` and readiness returned 200 with database/redis/qdrant `ready`; the actual worker emitted `worker_started` and stopped cleanly |
| Quality | Ruff formatting/lint clean; strict mypy clean for 124 backend and 13 embedding-service source files |
| Dependencies | `pip check` found no broken requirements; production lock audits for both services found no known vulnerabilities |
| Secrets/logs | Diff/secret-pattern inspection found no real credentials; controlled API/worker logs contained safe configuration and counts only, with no questions, prompts, answers, source, graph bodies, tokens, credential values, or service URLs |
| Live providers | No real GitHub App or hosted LLM credential was available. GitHub and OpenAI adapters are mocked/structurally tested; live success is not claimed |
| Containers | Not retried because the previously documented contradictory Podman VM/socket state remains a host-runtime block. Baseline hosted CI passed production image builds and non-root assertions; the unpushed Milestone 8 commit has not run in hosted CI |

## Failures encountered and fixes

1. The first Alembic invocation supplied only the database URL, but application configuration correctly required the remaining mandatory test settings. The command was rerun with complete test-only configuration; the full PostgreSQL migration cycle and final consistency check passed.
2. Extending the controlled indexing fixture with a second symbol changed its expected vector/symbol totals. The assertions were updated to verify both the existing indexing contract and the new persisted graph state; the final eight-test pipeline suite passed.
3. An initial configuration validator incorrectly required the call-expression byte cap to be no larger than the parser input cap. A discovery security test correctly exposed that this rejected an otherwise valid smaller parser-input configuration. The redundant validator was removed; input bounds already cap extracted expressions, and the full 294-test suite passed.
4. A parallel backend/embedding mypy run caused mypy 2.3 to report an internal shared-cache error and a false missing test symbol. The backend check was rerun serially with an isolated cache and passed all 124 files. No assertion or type rule was weakened.
5. The final Alembic read/check initially hit the sandbox localhost socket restriction. The exact commands were rerun with approved local-network access and confirmed `b83f2d8a6c41 (head)` with no pending upgrade operations.
6. Dependency audit access initially failed under sandbox DNS restrictions. The unchanged hash-locked production requirements were audited with approved network access; both services reported no known vulnerabilities.
7. The first Milestone 8 evaluation artifact covered only eight of the required caller scenarios. It was expanded to 20 explicit fixture contracts, adding aliases, methods, constructors, nested calls, mixed code/history, impact, injection, cross-repository, tool-loop, and runtime-refusal behavior. The final evaluator reports 20/20 fixture observations with the documented structural metrics.
8. Mixed-tool testing found that deterministic caller intent recognized `caller` and `calls` but not plural `callers`, even though symbol extraction did. The classifier now accepts both singular and plural forms; 20 PostgreSQL-backed question tests, including caller+code and caller+history routing, pass.
9. Ambiguous call sites were initially counted separately but persisted as generic `unresolved` edges, preventing independent ambiguity-count validation. The Milestone 8 migration now adds the explicit `ambiguous` resolution value; graph validation recomputes both ambiguous and unresolved counts, and the complete migration cycle and suite pass.
10. Unicode Python identifiers were parsed but the first resolver used an ASCII-leading identifier pattern. Resolution now uses Python's Unicode-aware `str.isidentifier()` for every qualified-name segment; deterministic graph tests verify the Unicode call and repository-scoped identity.

## Known static-analysis limitations

RepoLume identifies only statically resolvable direct relationships. It cannot prove runtime call behavior involving reflection, dynamic `getattr`, monkey patching, dependency injection, dynamically generated functions, metaclass/decorator-generated targets, unresolved wildcard imports, runtime module mutation, unknown receiver dispatch, arbitrary callbacks, or framework routing. Unique probable method matches retain a lower-confidence category. Ambiguous and unresolved sites are counted but never returned as certain callers. Impact answers must say that statically resolved callers may be affected, not that code will break.

## External configuration and acceptance still required

- A real least-privilege GitHub App installed on a controlled repository for live cloning/indexing authorization and revocation acceptance.
- A real OpenAI credential in the API-only secret store for structured three-tool behavior, static-impact wording, injection resistance, citation validity, latency, cost, and outage acceptance.
- A representative labelled Python repository corpus for human-reviewed caller precision/recall across real language patterns. Fixture-contract metrics are not universal accuracy claims.
- Hosted CI for the local Milestone 8 commit, managed PostgreSQL/Redis/Qdrant/private embeddings, deployment, telemetry/alerts, rate/usage controls, backups/restores, deletion drills, and incident response.

## Production-readiness statement

Milestone 8 is a tested local static-call-graph foundation, not a production SaaS. Do not launch publicly or describe caller results as complete runtime behavior until hosted CI and deployment, live GitHub/LLM acceptance, representative quality/load evaluation, privacy operations, and remaining security gates pass.

## Next milestone gate

Milestone 9 is not authorized and has not started. Webhook-driven incremental indexing, changed-file graph/vector replacement, freshness state, and push ordering remain absent.

# RepoLume Build Status

**Last updated:** 2026-07-16
**Authorized milestone:** Milestone 4 — Python static parsing and AST-aware chunking
**Overall status:** Milestone 4 implementation and local acceptance verification complete
**Production readiness:** Not production-ready. Static ingestion exists, but live GitHub credentials, hosted CI/deployment, embeddings, vector persistence/search, retrieval, agents, chat, frontend, deletion, rate limits, backups, and production operations remain absent or unverified.

## Implemented through Milestone 4

- All Milestone 1–3 API, authentication, authorization, webhook, durable job, Redis Stream, safe clone, and bounded discovery behavior.
- Pinned `tree-sitter==0.26.0` and `tree-sitter-python==0.25.0` in both hashed lockfiles.
- Typed transient parsed-file, import, parameter, symbol, source-segment, chunk, fingerprint, and repository-processing models.
- Static Python extraction for modules, sync/async functions, classes, methods, nested definitions, decorators, multiline signatures, parameter kinds/defaults/annotations, return annotations, raw docstrings, imports/aliases/relative levels, parent relationships, qualified names, hashes, and exact one-based lines.
- Malformed/partial Python handling that preserves safely recoverable definitions and reduces parser failures to safe categories without logging source or parser internals.
- Deterministic AST-aware Python chunks for definitions, class overviews, and module code. Oversized definitions split only at immediate statement boundaries or are skipped; code is never silently truncated.
- Heading-aware Markdown and bounded paragraph/fence-aware `.txt`/`.rst` chunks with heading hierarchy, line ranges, commit SHA, order, and stable SHA-256 content hashes.
- Central Pydantic limits for input, symbols, symbol/chunk/repository counts and sizes, documentation sections, warnings, wall time, parser CPU, and parser memory.
- A spawn-isolated, killable parser process. Linux applies CPU and address-space ceilings; macOS applies CPU plus the parent-enforced wall timeout because Darwin rejects `RLIMIT_AS` for this child shape.
- Worker stages now progress through `discovering` (55), `parsing` (65), and `chunking` (85), then finish at `chunking_complete` while the repository honestly remains `not_indexed`.
- Redis continues to carry only `job_id`. Source and chunks never enter Redis, API errors, job errors, or logs.
- PostgreSQL persists safe processing counts/warning categories and versioned symbol metadata only. Chunk bodies/fingerprints are deliberately not persisted before Milestone 5.
- Temporary clone cleanup remains unconditional after parser success, safe parser failure, timeout, or worker failure.

## Database and migration

Alembic revision `f9389ed2964e` follows `94b0f7ce7782` and adds to `indexing_jobs`:

- nonnegative `parsed_file_count`, `partial_file_count`, and `parser_skipped_file_count`;
- nonnegative `symbol_count` and transient `chunk_count` summary;
- content-free `parser_warnings_json` category counts.

Milestone 4 writes extracted symbols to the existing `symbol_definitions` table under the next inactive repository index version. It does not advance `repositories.index_version` or mark a repository searchable. Reprocessing replaces only the same repository/inactive-version symbol set atomically.

## Acceptance evidence

| Gate | Actual result |
| --- | --- |
| Runtime | Python 3.13.14 local baseline; CI remains pinned to Python 3.13 |
| Dependencies | Exact Tree-sitter pair installed from hashed locks; `pip check` passed; `pip-audit` reported no known vulnerabilities |
| PostgreSQL | Disposable PostgreSQL 18.4; no SQLite |
| Redis | Disposable Redis 8.8; real Stream/consumer-group integration |
| Migrations | Downgrade through all four revisions to base, clean upgrade to `f9389ed2964e (head)`, `current`, and `alembic check` all passed |
| Unit tests | 112 unit tests passed; focused unit run completed in 0.68 seconds |
| Integration tests | 25 PostgreSQL/Redis/API/worker tests passed; focused integration run completed in 2.15 seconds |
| Complete tests | 137 passed, 0 failed, 0 skipped in 3.67 seconds; 90.66% branch-aware coverage |
| Parsing/chunking | Rich Python/Markdown/text fixtures cover malformed/partial/Unicode/CRLF/nesting/decorators/parameters/imports/large bounds, exact lines, stable hashes, and deterministic repeated output |
| Controlled repository | A real local Git fixture was cloned, discovered, parsed, symbolized, chunked, persisted as metadata, and deleted; deliberate executable source did not create its marker |
| Failure cleanup | Live worker integration forced a nonretryable parser failure and confirmed the clone directory was empty with only a safe error code/message persisted |
| Tenant/queue isolation | Cross-user repository denial remains enforced; processing results carry one repository/index context; Redis messages contain only `job_id` |
| Quality | Ruff format/lint and strict mypy using the explicit backend config passed; CI now invokes the same strict config |
| Container | Podman built the Python 3.13.14/Git/Tree-sitter image; API and worker ran as UID 10001, both health endpoints returned HTTP 200, and the sensitive/source log scan passed |
| Hosted CI | Workflow covers Python 3.13, PostgreSQL 18, Redis, hashed locks, strict quality, migrations, complete tests, audit, and non-root image checks; no hosted run exists yet |

## Failures encountered and fixed

1. The initial migration verification shell applied environment assignments only to its first chained command; `upgrade` succeeded but the following `current` lacked required settings. Each Alembic command was rerun independently and passed.
2. `pip-audit` initially failed because sandbox DNS was blocked. It was rerun with approved network access and reported no known vulnerabilities.
3. The first isolated live worker run returned safe `internal_parser_failure`. A controlled diagnostic proved macOS rejected `RLIMIT_AS`; memory limiting is now applied on Linux, while macOS retains CPU and killable wall-time enforcement. The live test then passed.
4. The pre-existing CI mypy command was not loading `backend/pyproject.toml`, so its claimed strictness was ineffective. CI and documentation now pass `--config-file`; the resulting legacy typing errors were fixed without suppressing the strict gate.
5. Initial exact-line and small-size chunk test expectations did not match the fixture's actual byte/line boundaries. The implementation was inspected, the nested-definition gap guard was retained, and expectations were corrected rather than weakening behavior.
6. Podman initially targeted a stale stopped connection; a new disposable VM then failed its first-boot Ignition step. That VM alone was terminated/removed, the existing RepoLume CI VM was started inside one controlled verification script, and the image/non-root/health/log checks passed.

## External configuration still required

- A real least-privilege GitHub App and controlled live OAuth/installation-token clone/webhook acceptance.
- Managed PostgreSQL and authenticated TLS Redis credentials in platform secret stores.
- Private worker egress restrictions and production parser resource/capacity tuning.
- Hosted CI, image scanning/digest pinning, deployment, telemetry/alerts, backups/restores, and incident drills.

## Current limitations

- `chunking_complete` means static processing finished, not that an index is searchable; repository status remains `not_indexed` and active `index_version` is unchanged.
- Chunks are transient until Milestone 5. There are no embeddings, Qdrant writes, vector search, static call resolution, LLM calls, agents, repository chat, or frontend.
- Tree-sitter recovery is best effort. A partial file can omit unrecoverable constructs, and static syntax cannot prove runtime dispatch, reflection, generated code, decorator behavior, or dependency semantics.
- Configuration formats discovered in Milestone 3 remain classified as unsupported for M4 chunking; `.py`, `.pyi`, `.md`, `.markdown`, `.txt`, and `.rst` are processed.
- Live GitHub and hosted deployment behavior are not claimed.

## Production-readiness statement

Milestone 4 is a tested local static-ingestion foundation. RepoLume is not ready for production credentials or private repository traffic because there is no completed searchable data plane, live GitHub acceptance, hosted security evidence, deletion path, or production operations stack.

## Next milestone

Milestone 5 — embeddings and vector indexing. It has not started and requires explicit authorization.

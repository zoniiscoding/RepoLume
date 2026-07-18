# RepoLume

RepoLume is a multi-tenant, read-only developer SaaS for understanding authorized GitHub repositories through evidence-backed answers.

This repository is at **Milestone 10: browser application and safe API handoff**. It implements the FastAPI/PostgreSQL foundation, GitHub App authentication and access controls, durable indexing, private ONNX embeddings, atomically activated Qdrant indexes, a three-tool read-only agent, default-branch push refreshes, and a React/TypeScript/Vite browser client for repository selection, progress, questions, and evidence inspection. The prior active version remains queryable until a replacement validates and activates. Chat persistence, deployment, and later launch work are not implemented.

Read [the product specification](docs/PRODUCT_SPEC.md), [current build status](docs/BUILD_STATUS.md), [security posture](docs/SECURITY.md), and [engineering rules](AGENTS.md) before changing code.

## Requirements

- Python 3.11–3.14; Python 3.13 is the production/CI baseline.
- Tree-sitter 0.26.0 and the Python grammar 0.25.0.
- PostgreSQL 18 for the verified local/CI baseline.
- Redis 8.8 for the verified local/CI queue-delivery baseline.
- Qdrant 1.18.2 for the verified local/CI vector baseline.
- The reviewed `jinaai/jina-embeddings-v2-base-code` model at immutable revision `516f4baf13dec4ddddda8631e019b5737c8bc250` (Apache-2.0, 768 dimensions, 8,192-token model limit).
- Git available at the absolute path configured by `CLONE_GIT_EXECUTABLE` (the container uses `/usr/bin/git`).
- A GitHub App is required only for live OAuth, installation, and webhook verification; automated tests use mocked GitHub responses.
- An OpenAI API credential is required for production agent decisions and answer synthesis. Automated and local acceptance tests use a deterministic provider and never require or fabricate a hosted-model result.
- Docker Compose or Podman/Docker is optional for containerized local startup.

## Local setup

Create an isolated Python 3.13 environment and install the hashed development lock:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes --requirement backend/requirements-dev.lock
.venv/bin/python -m pip install --require-hashes --requirement embedding_service/requirements-dev.lock
cp .env.example .env
```

Fill the untracked `.env` with disposable/local PostgreSQL, Redis, Qdrant, and private embedding-service settings plus local-only authentication values. Secret fields must be independent values of at least 32 characters. Never commit `.env`.

For the question API, set `LLM_PROVIDER=openai`, place `LLM_API_KEY` only in the API secret store, and keep the pinned `LLM_MODEL=gpt-5.4-mini-2026-03-17` unless an intentional compatibility review changes it. `LLM_PROVIDER=deterministic` is accepted only in tests and is not a production model substitute. Agent defaults cap each question at four tool calls, eight seconds per tool, 45 seconds total, 32 KiB per tool result, 64 KiB total evidence, 20 caller results, and 1,200 output tokens.

## Frontend

The browser client has an independent locked npm dependency graph and requires Node 22 or newer:

```sh
cd frontend
npm ci
npm run dev
```

Set `VITE_API_BASE_URL` to the API `/api/v1` base when it is not `http://127.0.0.1:8000/api/v1`. For the normal local browser flow, set `FRONTEND_URL=http://127.0.0.1:5173` and include that exact origin in `CORS_ORIGINS`; production requires a deployed HTTPS origin in both settings. The API consumes GitHub OAuth code/state, sets the HTTP-only refresh cookie, and redirects to the fixed browser callback route without any credential in the URL. The client keeps access tokens only in memory and never writes them to browser storage.

Run the browser quality gates with:

```sh
cd frontend
npm run format
npm run lint
npm run build
npm test
npx playwright install chromium
npm run test:e2e
npm audit --audit-level=high
```

## GitHub App configuration

For live use, create a GitHub App with:

- OAuth callback: `https://<api-host>/api/v1/auth/github/callback`
- Webhook URL: `https://<api-host>/api/v1/webhooks/github`
- Read-only repository permissions: Metadata, Contents, and Pull requests
- Events: Installation, Installation repositories, Push, and Repository
- No repository write permission

Set `GITHUB_APP_ID`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_OAUTH_CALLBACK_URL`, `ACCESS_TOKEN_SECRET`, and `TOKEN_HASH_SECRET` in the runtime secret store. GitHub user and installation tokens remain server-side and are not persisted.

The webhook route accepts only `installation`, `installation_repositories`, `push`, and `repository`. It authenticates the exact raw body with `X-Hub-Signature-256`, caps the body at 1 MiB, validates bounded event/delivery headers and typed fields, and stores normalized content-free delivery metadata rather than payloads. Only the server-owned default branch is indexed. Non-default and deleted-branch pushes are ignored; forced, unanchored, unavailable, unsafe, or over-limit comparisons use a visible full-rebuild fallback. Manual full refresh is available at `POST /api/v1/repositories/{repository_id}/reindex`; no model tool can invoke it.

## Database and API

Start PostgreSQL, Redis, Qdrant, and the private embedding service if using Compose:

```sh
docker compose up -d postgres redis qdrant embedding-service
```

Apply migrations deliberately, then start the API:

```sh
.venv/bin/alembic -c backend/alembic.ini upgrade head
cd backend
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Verify health:

```sh
curl --fail-with-body http://127.0.0.1:8000/api/v1/health/live
curl --fail-with-body http://127.0.0.1:8000/api/v1/health/ready
```

Start the private worker in a separate process after migrations:

```sh
cd backend
../.venv/bin/python -m app.worker
```

For a host-run embedding service, preload the exact reviewed model once and then run offline:

```sh
cd embedding_service
EMBEDDING_MODEL_CACHE_DIR=/tmp/repolume-models ../.venv/bin/python -m app.download_model
EMBEDDING_MODEL_LOCAL_FILES_ONLY=true HF_HUB_OFFLINE=1 \
  ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8100 --no-access-log
```

Or run the complete local service set with `docker compose up --build`. The API and worker share the UID/GID `10001:10001` backend image; the embedding service uses `10002:10002`; only the API publishes a product port. Redis receives only opaque job UUIDs. PostgreSQL owns job/build/active-version truth. Qdrant stores the complete chunk content required for later citations plus trusted installation, repository, version, commit, location, type, hash, and model fingerprints under mandatory typed filters.

Start live OAuth at `GET /api/v1/auth/github/start`. Access tokens are returned in authenticated API responses and belong in frontend memory only. Refresh tokens are handled through the scoped HTTP-only cookie; refresh/logout requests must include an allowed `Origin` header.

Ask a question with the short-lived bearer token after the repository reports a complete active searchable index:

```sh
curl --fail-with-body --request POST \
  --header 'Authorization: Bearer <access-token>' \
  --header 'Content-Type: application/json' \
  --data '{"question":"Where is repository access validated?"}' \
  http://127.0.0.1:8000/api/v1/repositories/<repository-id>/questions
```

The API controls every tool, retrieval limit, history/caller bound, and filter. It returns `answered`, `partially_answered`, `insufficient_evidence`, `unsupported_question`, or `temporarily_unavailable`. Citations are discriminated as `code`, `commit`, `pull_request`, or `caller`; their metadata comes from current-request server evidence, never model output. Caller citations include target/caller symbols, call location/expression, resolution type, confidence, active commit/version, and the static-analysis limitation. The response also contains a content-free trace with tool name, argument fingerprint, status, duration, result count, and safe failure category. Questions, prompts, answers, evidence, patches, commit/PR bodies, and query vectors are not persisted.

## Quality checks

Run from the repository root with the test URLs pointing only to disposable real PostgreSQL, Redis, and Qdrant services and with a real authenticated private embedding service running:

```sh
.venv/bin/ruff format --check backend
.venv/bin/ruff check backend
.venv/bin/mypy --config-file backend/pyproject.toml backend/app backend/tests
.venv/bin/ruff format --check embedding_service
.venv/bin/ruff check embedding_service
.venv/bin/mypy --config-file embedding_service/pyproject.toml embedding_service/app embedding_service/tests
.venv/bin/python -m pip check
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/alembic -c backend/alembic.ini check
cd backend
../.venv/bin/pytest
cd ..
.venv/bin/pytest embedding_service
.venv/bin/pip-audit --requirement backend/requirements.lock --disable-pip
.venv/bin/pip-audit --requirement embedding_service/requirements.lock --disable-pip
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

Integration tests truncate PostgreSQL, flush Redis, and delete the configured Qdrant test collection; they fail rather than silently using SQLite, an in-process queue, or an in-memory vector store. Never point a test URL at development, staging, or production data.

## Security boundary

Connected repositories, webhook payloads, GitHub history, user questions, and model output are untrusted data. RepoLume never executes, imports, installs, builds, tests, or invokes connected code. The immutable registry contains only `search_code`, `get_history`, and `find_callers`; none exposes shell, arbitrary network, secrets, writes, refresh controls, raw filters, repository/installation/version/commit selectors, URLs, or limits. GitHub comparison/history calls use fixed paths and ephemeral repository-restricted tokens. Every vector reuse/read/write/delete retains server-derived installation/repository/version/commit/model/policy scope. Activation rechecks durable authorization, branch, generation, build, vector, and graph state; stale workers clean only their inactive scope. Static caller results can miss dynamic dispatch, monkey patching, reflection, generated code, decorators, and unresolved polymorphism; they are evidence, not runtime truth.

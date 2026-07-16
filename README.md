# RepoLume

RepoLume is a multi-tenant, read-only developer SaaS for understanding authorized GitHub repositories through evidence-backed answers.

This repository is at **Milestone 5: private embeddings and atomic vector indexing**. It implements the FastAPI/PostgreSQL foundation, GitHub App authentication and access controls, Redis Stream delivery, a separate indexing worker, safe shallow cloning, isolated Tree-sitter parsing/chunking, an authenticated private ONNX embedding service, repository/version-scoped Qdrant storage, and PostgreSQL-authoritative atomic index activation. There is no user-facing semantic search, call graph, LLM, agent, chat, or frontend functionality yet.

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

## GitHub App configuration

For live use, create a GitHub App with:

- OAuth callback: `https://<api-host>/api/v1/auth/github/callback`
- Webhook URL: `https://<api-host>/api/v1/webhooks/github`
- Read-only repository permissions: Metadata, Contents, and Pull requests
- Events: Installation, Installation repositories, Push, and Repository
- No repository write permission

Set `GITHUB_APP_ID`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_OAUTH_CALLBACK_URL`, `ACCESS_TOKEN_SECRET`, and `TOKEN_HASH_SECRET` in the runtime secret store. GitHub user and installation tokens remain server-side and are not persisted.

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
```

Integration tests truncate PostgreSQL, flush Redis, and delete the configured Qdrant test collection; they fail rather than silently using SQLite, an in-process queue, or an in-memory vector store. Never point a test URL at development, staging, or production data.

## Security boundary

Connected repository code is untrusted data. RepoLume never executes, imports, installs, builds, tests, or invokes it. The worker clones only a validated `github.com/<owner>/<repository>.git` identity with fixed arguments, disabled hooks/submodules/config, an askpass credential outside argv, process/filesystem limits, and guaranteed temporary cleanup. Tree-sitter consumes bounded UTF-8 bytes as inert syntax in an isolated, killable process. The embedding service receives only bounded preprocessed text over authenticated private HTTP, has no GitHub/Redis/database credentials, loads a fixed ONNX model without remote code, and never logs source or vectors. Qdrant operations always include server-derived installation, repository, and index-version scope.

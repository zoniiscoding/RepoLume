# RepoLume

RepoLume is a multi-tenant, read-only developer SaaS for understanding authorized GitHub repositories through evidence-backed answers.

This repository is at **Milestone 3: durable jobs and safe cloning**. It implements the FastAPI/PostgreSQL foundation, GitHub App authentication and access controls, Redis Stream delivery, PostgreSQL-owned job state, a separate worker, fixed safe shallow cloning, bounded non-executing file discovery, tests, and CI. There is no parsing, chunking, embeddings, vector search, agent, chat, or frontend functionality yet.

Read [the product specification](docs/PRODUCT_SPEC.md), [current build status](docs/BUILD_STATUS.md), [security posture](docs/SECURITY.md), and [engineering rules](AGENTS.md) before changing code.

## Requirements

- Python 3.11–3.14; Python 3.13 is the production/CI baseline.
- PostgreSQL 18 for the verified local/CI baseline.
- Redis 8.8 for the verified local/CI queue-delivery baseline.
- Git available at the absolute path configured by `CLONE_GIT_EXECUTABLE` (the container uses `/usr/bin/git`).
- A GitHub App is required only for live OAuth, installation, and webhook verification; automated tests use mocked GitHub responses.
- Docker Compose or Podman/Docker is optional for containerized local startup.

## Local setup

Create an isolated Python 3.13 environment and install the hashed development lock:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes --requirement backend/requirements-dev.lock
cp .env.example .env
```

Fill the untracked `.env` with disposable/local PostgreSQL and Redis URLs plus local-only authentication values. Secret fields must be independent values of at least 32 characters. Never commit `.env`.

## GitHub App configuration

For live use, create a GitHub App with:

- OAuth callback: `https://<api-host>/api/v1/auth/github/callback`
- Webhook URL: `https://<api-host>/api/v1/webhooks/github`
- Read-only repository permissions: Metadata, Contents, and Pull requests
- Events: Installation, Installation repositories, Push, and Repository
- No repository write permission

Set `GITHUB_APP_ID`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_OAUTH_CALLBACK_URL`, `ACCESS_TOKEN_SECRET`, and `TOKEN_HASH_SECRET` in the runtime secret store. GitHub user and installation tokens remain server-side and are not persisted.

## Database and API

Start PostgreSQL and Redis if using Compose:

```sh
docker compose up -d postgres redis
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

Or run the complete local service set with `docker compose up --build`. The API and worker share the same non-root image; only the API publishes a host port. Redis receives only opaque job UUIDs, while PostgreSQL owns job state and recovery.

Start live OAuth at `GET /api/v1/auth/github/start`. Access tokens are returned in authenticated API responses and belong in frontend memory only. Refresh tokens are handled through the scoped HTTP-only cookie; refresh/logout requests must include an allowed `Origin` header.

## Quality checks

Run from the repository root with `DATABASE_URL`/`TEST_DATABASE_URL` pointing to a real disposable PostgreSQL database and `REDIS_URL`/`TEST_REDIS_URL` pointing to a real disposable Redis database:

```sh
.venv/bin/ruff format --check backend
.venv/bin/ruff check backend
.venv/bin/mypy backend/app backend/tests
.venv/bin/python -m pip check
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/alembic -c backend/alembic.ini check
cd backend
../.venv/bin/pytest
cd ..
.venv/bin/pip-audit --requirement backend/requirements.lock --disable-pip
```

Integration tests truncate PostgreSQL and flush Redis; they fail rather than silently using SQLite or an in-process queue. Never point either test URL at development, staging, or production data.

## Security boundary

Connected repository code is untrusted data. RepoLume never executes, imports, installs, builds, tests, or invokes it. The worker clones only a validated `github.com/<owner>/<repository>.git` identity with fixed arguments, disabled hooks/submodules/config, an askpass credential outside argv, process/filesystem limits, and guaranteed temporary cleanup. Milestone 3 stops after safe discovery; no repository parser runs.

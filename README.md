# RepoLume

RepoLume is a multi-tenant, read-only developer SaaS for understanding authorized GitHub repositories through evidence-backed answers.

This repository is at **Milestone 2: authentication and GitHub App access**. It implements the FastAPI/PostgreSQL foundation, GitHub App user authorization, RepoLume access/refresh sessions, installation and membership synchronization, authorized repository listing, signed idempotent webhooks, immediate access-revocation states, tests, and CI. There is no frontend, worker, cloning, parsing, embeddings, vector search, agent, or chat functionality yet.

Read [the product specification](docs/PRODUCT_SPEC.md), [current build status](docs/BUILD_STATUS.md), [security posture](docs/SECURITY.md), and [engineering rules](AGENTS.md) before changing code.

## Requirements

- Python 3.11–3.14; Python 3.13 is the production/CI baseline.
- PostgreSQL 18 for the verified local/CI baseline.
- A GitHub App is required only for live OAuth, installation, and webhook verification; automated tests use mocked GitHub responses.
- Docker Compose or Podman/Docker is optional for containerized local startup.

## Local setup

Create an isolated Python 3.13 environment and install the hashed development lock:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes --requirement backend/requirements-dev.lock
cp .env.example .env
```

Fill the untracked `.env` with a disposable/local PostgreSQL URL and local-only authentication values. Secret fields must be independent values of at least 32 characters. Never commit `.env`.

## GitHub App configuration

For live use, create a GitHub App with:

- OAuth callback: `https://<api-host>/api/v1/auth/github/callback`
- Webhook URL: `https://<api-host>/api/v1/webhooks/github`
- Read-only repository permissions: Metadata, Contents, and Pull requests
- Events: Installation, Installation repositories, Push, and Repository
- No repository write permission

Set `GITHUB_APP_ID`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_OAUTH_CALLBACK_URL`, `ACCESS_TOKEN_SECRET`, and `TOKEN_HASH_SECRET` in the runtime secret store. GitHub user and installation tokens remain server-side and are not persisted.

## Database and API

Start PostgreSQL if using Compose:

```sh
docker compose up -d postgres
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

Start live OAuth at `GET /api/v1/auth/github/start`. Access tokens are returned in authenticated API responses and belong in frontend memory only. Refresh tokens are handled through the scoped HTTP-only cookie; refresh/logout requests must include an allowed `Origin` header.

## Quality checks

Run from the repository root with `DATABASE_URL` and `TEST_DATABASE_URL` pointing to a real disposable PostgreSQL database:

```sh
.venv/bin/ruff format --check backend
.venv/bin/ruff check backend
.venv/bin/mypy backend/app backend/tests
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/alembic -c backend/alembic.ini check
cd backend
../.venv/bin/pytest
cd ..
.venv/bin/pip-audit --requirement backend/requirements.lock --disable-pip
```

Integration tests truncate their target and fail rather than silently using SQLite. Never point `TEST_DATABASE_URL` at development, staging, or production data.

## Security boundary

Connected repository code is untrusted data. RepoLume never executes, imports, installs, builds, tests, or invokes it. Milestone 2 contacts only fixed GitHub endpoints for identity, installation, and repository metadata/access; repository cloning begins no earlier than an explicitly authorized Milestone 3.

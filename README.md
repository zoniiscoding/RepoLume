# RepoLume

RepoLume is a multi-tenant, read-only developer SaaS for understanding authorized GitHub repositories through evidence-backed answers.

This repository is currently at **Milestone 1: monorepo and backend foundation**. It provides the FastAPI application shell, strict configuration, safe structured logging, PostgreSQL schema and migrations, health checks, tests, and CI. Authentication, GitHub access, workers, ingestion, embeddings, retrieval, agent tools, and frontend functionality are intentionally not implemented yet.

Read [the product specification](docs/PRODUCT_SPEC.md), [current build status](docs/BUILD_STATUS.md), and [engineering rules](AGENTS.md) before changing code.

## Requirements

- Python 3.11–3.14; Python 3.13 is the production/CI baseline.
- PostgreSQL 18 for the verified local/CI baseline.
- Docker Compose is optional for local PostgreSQL and API startup.

## Local setup

Create an isolated environment and install the locked development dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.lock
```

Copy the documented environment names and supply local-only values:

```sh
cp .env.example .env
```

At minimum, set `POSTGRES_PASSWORD` and an async `DATABASE_URL`. When the API runs on the host against the Compose database, an example shape is:

```text
postgresql+asyncpg://<user>:<password>@127.0.0.1:5432/<database>
```

Do not commit `.env`.

## Database

Start PostgreSQL:

```sh
docker compose up -d postgres
```

Apply migrations deliberately:

```sh
cd backend
../.venv/bin/alembic upgrade head
```

Inspect the current revision:

```sh
cd backend
../.venv/bin/alembic current
```

## Run the API

```sh
cd backend
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Health checks:

```sh
curl --fail http://127.0.0.1:8000/api/v1/health/live
curl --fail http://127.0.0.1:8000/api/v1/health/ready
```

Liveness proves the process can serve. Readiness returns HTTP 503 when PostgreSQL is unavailable.

To use Compose for both the database and API, make `DATABASE_URL` address the `postgres` service and run:

```sh
docker compose up --build api
```

## Quality checks

Run from the repository root:

```sh
.venv/bin/ruff format --check backend
.venv/bin/ruff check backend
.venv/bin/mypy backend/app backend/tests
cd backend && ../.venv/bin/pytest
.venv/bin/pip-audit --requirement backend/requirements.lock --disable-pip
```

Integration tests require `TEST_DATABASE_URL` to reference a disposable PostgreSQL database. They fail rather than silently use SQLite when that variable is absent.

## Security boundary

Connected repository code is untrusted data. RepoLume will never execute, import, install, build, test, or invoke it. Milestone 1 contains no connected-repository operations.

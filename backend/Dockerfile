FROM python:3.14.6-slim-trixie@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN python -m venv /opt/venv \
    && apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 repolume \
    && useradd --system --uid 10001 --gid repolume --home-dir /app --shell /usr/sbin/nologin repolume

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --requirement requirements.lock

COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY app ./app

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

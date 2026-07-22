FROM python:3.13.14-slim-trixie@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HUB_DISABLE_TELEMETRY=1 \
    EMBEDDING_MODEL_CACHE_DIR=/models \
    EMBEDDING_MODEL_LOCAL_FILES_ONLY=true

RUN python -m venv /opt/venv \
    && groupadd --system --gid 10002 repolume-embeddings \
    && useradd --system --uid 10002 --gid repolume-embeddings --home-dir /app --shell /usr/sbin/nologin repolume-embeddings \
    && mkdir --parents /app /models

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --requirement requirements.lock

COPY app ./app
RUN python -m app.download_model \
    && chown --recursive 10002:10002 /app /models

USER 10002:10002

EXPOSE 8100

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--no-access-log"]

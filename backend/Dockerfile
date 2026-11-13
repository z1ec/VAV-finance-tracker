# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------- build stage
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
COPY requirements.txt .
RUN /opt/venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------- runtime stage
FROM python:3.12-slim AS runtime

ARG APP_VERSION=dev
ARG GIT_SHA=local

# sqlite3 CLI: manual backups/inspection (`sqlite3 /data/expenses.db ".backup ..."`).
# `upgrade`: Debian security fixes published after the base image was built (trivy, stage 3b).
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 tracker \
    && useradd --system --uid 10001 --gid tracker --home-dir /app --shell /usr/sbin/nologin tracker

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=tracker:tracker alembic.ini ./
COPY --chown=tracker:tracker migrations ./migrations
COPY --chown=tracker:tracker app ./app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_VERSION=${APP_VERSION} \
    GIT_SHA=${GIT_SHA} \
    DB_PATH=/data/expenses.db \
    BACKUP_DIR=/data/backups

RUN mkdir -p /data/backups && chown -R tracker:tracker /data
VOLUME ["/data"]
USER tracker

LABEL org.opencontainers.image.title="finance-tracker-backend" \
      org.opencontainers.image.description="Multi-currency expense tracker — FastAPI backend" \
      org.opencontainers.image.source="https://github.com/z1ec/VAV-finance-tracker" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}"

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import json,sys,urllib.request as u; r=u.urlopen('http://127.0.0.1:8000/api/health', timeout=4); sys.exit(0 if json.load(r).get('db') == 'ok' else 1)"]

# --no-proxy-headers: request.client is always the TCP peer (Caddy or a container on
# vav-internal), never a value taken from X-Forwarded-For — the bot token check relies on it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]

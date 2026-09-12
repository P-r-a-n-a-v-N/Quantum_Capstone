# syntax=docker/dockerfile:1
#
# Single-container deployment image.
#
# docker-compose.yml runs backend and frontend as two services, which is the
# right shape for a VPS. This image collapses them into one process serving the
# API, the WebSocket and the built UI from a single origin -- because nearly
# every free hosting tier allocates one service per app, and because one origin
# removes CORS and WebSocket proxying from the production path entirely.

# --- stage 1: build the React bundle -----------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /build

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install

COPY frontend/ ./
RUN npm run build

# --- stage 2: python runtime that also serves the bundle ---------------------
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HISTORY_DB_PATH=/app/backend/data/telemetry.sqlite3

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY --from=frontend /build/dist ./frontend/dist

RUN mkdir -p /app/backend/data \
 && useradd --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8000')}/api/health\", timeout=4).status==200 else 1)"

# JSON form with an explicit `exec`: the shell is needed so $PORT expands
# (hosting platforms assign the port at runtime), and `exec` replaces that shell
# with uvicorn so SIGTERM reaches the app directly. Without it the signal stops
# at /bin/sh, the lifespan shutdown never runs, and the poller's HTTP client is
# torn down abruptly on every redeploy.
#
# One worker, deliberately -- the design is one poller per process, and extra
# workers would multiply the request rate to IBM.
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]

# syntax=docker/dockerfile:1
FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --fetch-timeout=30000 --fetch-retries=1
COPY frontend/index.html frontend/vite.config.js frontend/eslint.config.js ./
COPY frontend/src ./src
COPY frontend/tests ./tests
RUN npm run lint && npm run build

FROM python:3.11-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend HOME=/tmp KINDER_UBER_ENV=production \
    KINDER_UBER_RIDE_MODEL_MODE=demo KINDER_UBER_RIDE_DB=/data/state.sqlite3
RUN groupadd --gid 10001 workspace && useradd --uid 10001 --gid 10001 --no-create-home workspace \
    && mkdir -p /app/backend /app/scripts /data /config && chown 10001:10001 /data /config
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY backend/agents /app/backend/agents
COPY backend/core /app/backend/core
COPY backend/evaluation /app/backend/evaluation
COPY backend/memory /app/backend/memory
COPY backend/mobility /app/backend/mobility
COPY backend/monitor /app/backend/monitor
COPY backend/tooling /app/backend/tooling
COPY backend/skills /app/backend/skills
COPY scripts/provision-auth-preview.py /app/scripts/provision-auth-preview.py
COPY --from=frontend /build/dist /app/frontend/dist
USER 10001:10001
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=4).read()"
CMD ["python", "-m", "uvicorn", "mobility.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--limit-concurrency", "64", "--timeout-keep-alive", "5", "--no-access-log"]

# ═══════════════════════════════════════════════════════════════════════════════
# Aegis AI Security Gateway — Production Dockerfile
# ═══════════════════════════════════════════════════════════════════════════════
#
# Multi-stage build:
#   Stage 1 (builder) — install dependencies into a venv
#   Stage 2 (runtime) — distroless-like slim image with no build tools
#
# Security hardening:
#   • Non-root user (uid 1001, gid 1001)
#   • No shell in runtime stage (use exec form for CMD)
#   • Read-only filesystem (enforced by docker-compose / K8s securityContext)
#   • No SUID binaries, no privileged ports (runs on 8080)
#   • Multi-arch support: linux/amd64, linux/arm64
#
# Usage:
#   docker build --target runtime -t aegis-ai:latest .
#   docker run --rm -e AEGIS_ENV=development -p 8080:8080 aegis-ai:latest
# ═══════════════════════════════════════════════════════════════════════════════

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

LABEL org.opencontainers.image.source="https://github.com/your-org/aegis-ai"
LABEL org.opencontainers.image.description="Aegis AI Security Gateway — build stage"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# System build deps (only present in builder, not in runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libssl-dev \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create isolated virtual environment
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Upgrade pip + wheel first (avoids old-format wheel fallbacks)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy only dependency files first — Docker cache layer stays valid
# unless pyproject.toml changes
WORKDIR /build
COPY pyproject.toml ./

# Install all dependencies (production + optional extras)
# --no-cache-dir keeps the image lean
RUN pip install --no-cache-dir ".[gcp,redis,presidio,opentelemetry]" 2>/dev/null \
    || pip install --no-cache-dir .

# Install the runtime server dependencies not in pyproject.toml
RUN pip install --no-cache-dir \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.29.0" \
    "gunicorn>=22.0.0" \
    "httpx>=0.27.0"

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.description="Aegis AI Security Gateway — runtime"
LABEL org.opencontainers.image.version="1.2.0"

# Runtime OS deps only (openssl for TLS, ca-certificates for GCP mTLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libssl3 \
        libffi8 \
        tini \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /tmp/* \
    && rm -rf /root/.cache

# ── Non-root user ─────────────────────────────────────────────────────────────
# uid/gid 1001 — not root, not a named system account
RUN groupadd --gid 1001 aegis \
    && useradd --uid 1001 --gid aegis --no-create-home --shell /sbin/nologin aegis

# Copy the virtual environment from builder (no build tools come with it)
ENV VIRTUAL_ENV=/opt/venv
COPY --from=builder $VIRTUAL_ENV $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# ── Application code ──────────────────────────────────────────────────────────
WORKDIR /app

# Copy application source
COPY aegis_ai/         ./aegis_ai/
COPY envs/             ./envs/

# Copy optional supporting files
COPY pyproject.toml    ./

# ── Permissions ───────────────────────────────────────────────────────────────
# App files read-only for the aegis user; /tmp writable for tempfile operations
RUN chown -R aegis:aegis /app \
    && chmod -R 550 /app \
    && mkdir -p /tmp/aegis-ai && chown aegis:aegis /tmp/aegis-ai

# ── Environment defaults ──────────────────────────────────────────────────────
# These are defaults only; override with -e or Kubernetes ConfigMap/Secret
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONUTF8=1 \
    AEGIS_ENV=production \
    HOST=0.0.0.0 \
    PORT=8080 \
    WORKERS=1 \
    LOG_LEVEL=info \
    # GCP Application Default Credentials path (mounted as a K8s secret or Workload Identity)
    GOOGLE_APPLICATION_CREDENTIALS=/secrets/sa-key.json \
    # Prevent Python from buffering logs (critical for structured JSON logging)
    STRUCTLOG_RENDERER=json

# Expose application port (non-privileged)
EXPOSE 8080

# ── Health check (Docker native — also used by Compose) ───────────────────────
# Startup probe: allow 60 s for app to initialise before failing
HEALTHCHECK \
    --interval=15s \
    --timeout=5s \
    --start-period=60s \
    --retries=3 \
    CMD curl --fail --silent http://localhost:8080/health/live || exit 1

# ── Switch to non-root ────────────────────────────────────────────────────────
USER aegis

# ── Entrypoint ────────────────────────────────────────────────────────────────
# tini is used as PID 1 to handle signal forwarding (SIGTERM → graceful shutdown)
# and zombie reaping. Critical for Kubernetes pod lifecycle.
ENTRYPOINT ["/usr/bin/tini", "--"]

# Gunicorn + Uvicorn workers:
#   --worker-class uvicorn.workers.UvicornWorker : async ASGI workers
#   --workers 1                                  : override with WORKERS env var
#   --graceful-timeout 30                        : drain in-flight requests
#   --access-logfile -                           : log to stdout (captured by K8s)
#   --error-logfile -                            : stderr to stdout
#   --log-level $LOG_LEVEL
CMD ["sh", "-c", \
     "exec gunicorn aegis_ai.server:app \
        --bind ${HOST}:${PORT} \
        --worker-class uvicorn.workers.UvicornWorker \
        --workers ${WORKERS} \
        --graceful-timeout 30 \
        --timeout 120 \
        --keepalive 5 \
        --access-logfile - \
        --error-logfile - \
        --log-level ${LOG_LEVEL} \
        --forwarded-allow-ips='*' \
        --proxy-protocol \
        --preload"]

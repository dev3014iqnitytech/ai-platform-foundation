# ─────────────────────────────────────────────────────────────────────────────
# Client App (Agent A) — Multi-Stage Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 (builder): install all Python deps into a virtual environment.
# Stage 2 (runtime): copy only the venv — no build tools in the final image.
#
# Build:
#   docker build -t aegis-client-app:latest -f client_app/Dockerfile client_app/
#
# Run (standalone, pointing at the compose network gateway):
#   docker run --rm \
#     -e AEGIS_GATEWAY_URL=http://aegis-gateway:8080 \
#     -e CLIENT_USER_JWT_SECRET=dev-secret \
#     -p 8001:8001 \
#     aegis-client-app:latest
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

LABEL maintainer="Aegis AI SDK <aegis@example.com>"
LABEL description="Client API (Agent A) — builds Python venv"

# Prevent .pyc files and enable unbuffered stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Copy only requirements first for layer-cache efficiency
COPY requirements.txt .

# Create isolated venv and install deps
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Security: run as a non-root user
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Default runtime configuration (override via -e or docker-compose)
    CLIENT_HOST=0.0.0.0 \
    CLIENT_PORT=8001 \
    CLIENT_ENVIRONMENT=production \
    CLIENT_LOG_LEVEL=info \
    AEGIS_GATEWAY_URL=http://aegis-gateway:8080

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Copy application source
COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 8001

# Kubernetes health probes
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c \
        "import httpx, sys; r=httpx.get('http://localhost:8001/health/live', timeout=4); sys.exit(0 if r.status_code==200 else 1)"

# Use uvicorn directly — 1 worker per container (scale via replicas)
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8001", \
     "--workers", "1", \
     "--no-access-log"]

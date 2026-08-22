# ═══════════════════════════════════════════════════════════════════════════════
# gunicorn.conf.py — Gunicorn configuration file
# ═══════════════════════════════════════════════════════════════════════════════
# Used when running Gunicorn directly (not via docker CMD sh -c).
# Mount this file or bake it in for Kubernetes deployments.
#
# Usage:
#   gunicorn aegis_ai.server:app -c gunicorn.conf.py
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import os
import multiprocessing
import structlog

# ── Binding ───────────────────────────────────────────────────────────────────
host = os.getenv("HOST", "0.0.0.0")
port = os.getenv("PORT", "8080")
bind = f"{host}:{port}"

# ── Workers ───────────────────────────────────────────────────────────────────
# Recommended formula for async workers: 1-2 × CPU cores
# In containers: always read from env so Kubernetes resource limits apply
_env_workers = os.getenv("WORKERS", "")
workers = int(_env_workers) if _env_workers.isdigit() else max(1, multiprocessing.cpu_count())
worker_class = "uvicorn.workers.UvicornWorker"

# ── Timeouts ─────────────────────────────────────────────────────────────────
# timeout:         Worker silent time before SIGKILL (must exceed max LLM latency)
# graceful_timeout: SIGTERM grace period for in-flight requests
# keepalive:       Seconds to wait for the next request on a Keep-Alive connection
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

# ── Logging ───────────────────────────────────────────────────────────────────
# Access and error logs go to stdout/stderr (captured by Kubernetes / Cloud Logging)
loglevel = os.getenv("LOG_LEVEL", "info")
accesslog = "-"   # stdout
errorlog = "-"    # stderr
access_log_format = (
    '{"timestamp":"%(t)s","method":"%(m)s","path":"%(U)s",'
    '"status":%(s)s,"latency_ms":%(M)s,"bytes":%(B)s,'
    '"referer":"%(f)s","user_agent":"%(a)s"}'
)

# ── Proxy headers (required behind load balancers / API gateways) ─────────────
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "*")
proxy_protocol = os.getenv("PROXY_PROTOCOL", "false").lower() == "true"

# ── Worker lifecycle hooks (structured logging via structlog) ─────────────────

log = structlog.get_logger("gunicorn.lifecycle")


def on_starting(server):
    log.info("gunicorn_starting", bind=bind, workers=workers, worker_class=worker_class)


def on_reload(server):
    log.info("gunicorn_reload")


def worker_init(worker):
    log.info("worker_init", worker_pid=worker.pid)


def worker_exit(worker):
    log.info("worker_exit", worker_pid=worker.pid)


def on_exit(server):
    log.info("gunicorn_exit")


def post_worker_init(worker):
    """Called just after a worker has been initialised — good place for worker-local setup."""
    pass


def worker_abort(worker):
    """Called when a worker receives SIGABRT (worker timeout)."""
    log.error("worker_aborted_timeout", worker_pid=worker.pid, timeout=timeout)


# ── TLS (optional — terminate TLS at load balancer in production) ─────────────
# Uncomment only if doing TLS termination in Gunicorn rather than at the LB/ingress
# keyfile  = os.getenv("TLS_KEY_FILE", "")
# certfile = os.getenv("TLS_CERT_FILE", "")
# ca_certs = os.getenv("TLS_CA_CERT_FILE", "")
# ssl_version = 5   # TLSv1.3

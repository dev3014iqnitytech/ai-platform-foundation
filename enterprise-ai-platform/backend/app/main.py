"""
Enterprise AI Test Automation Platform — FastAPI Application Entry Point.
Assembles middleware stack, routers, lifespan hooks, and DI container.
"""
from __future__ import annotations
import sys
from pathlib import Path

# Add project root so agents/, rag/, workflows/, events/ are importable
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
    
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from structlog import get_logger

from app.core.config import settings
from app.core.middleware import (
    AuditMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan — startup / shutdown hooks
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup:  init DB pools, Redis connections, Service Bus consumers, OTel
    Shutdown: graceful drain of all connections and consumers
    """
    logger.info("application_starting", environment=settings.ENVIRONMENT.value)

    # ── Startup ──
    from app.infrastructure.database.session import init_db

    if not settings.is_production:
        await init_db()  # Dev only — production uses Alembic migrations

    # Initialize OpenTelemetry tracing
    try:
        from app.core.observability import init_tracing
        init_tracing()
    except ImportError:
        logger.warning("observability_not_configured")

    # Start Service Bus consumers as background tasks
    consumer_tasks: list = []
    if settings.SERVICE_BUS_CONNECTION_STRING:
        try:
            from events.consumers.service_bus_consumer import start_all_consumers
            conn_str = settings.SERVICE_BUS_CONNECTION_STRING.get_secret_value()
            consumer_tasks = await start_all_consumers(conn_str)
            logger.info("service_bus_consumers_started", count=len(consumer_tasks))
        except Exception as e:
            logger.warning("service_bus_consumers_failed", error=str(e))

    logger.info("application_started", version=settings.APP_VERSION)

    yield

    # Cancel all consumer tasks before draining connections
    for task in consumer_tasks:
        task.cancel()
    if consumer_tasks:
        import asyncio
        await asyncio.gather(*consumer_tasks, return_exceptions=True)
        logger.info("service_bus_consumers_stopped")

    # ── Shutdown ──
    from app.infrastructure.database.session import close_db
    await close_db()

    # Close Redis pool
    from app.core.dependencies import _redis_pool
    if _redis_pool:
        await _redis_pool.aclose()

    logger.info("application_shutdown_complete")


# ─────────────────────────────────────────────────────────────────────────────
# App Factory
# ─────────────────────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "Enterprise AI Test Automation Platform — "
            "Agentic AI + RAG powered test case generation from Azure DevOps User Stories"
        ),
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url="/api/redoc" if not settings.is_production else None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
    )

    # ── Middleware (outermost first) ──
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        redis_url=settings.redis_url_str if settings.REDIS_URL else None,
    )
    app.add_middleware(AuditMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    )

    # ── Register Routers ──
    from app.api.v1.auth import router as auth_router
    from app.api.v1.stories import router as stories_router
    from app.api.v1.test_cases import router as test_cases_router
    from app.api.v1.approvals import router as approvals_router
    from app.api.v1.knowledge import router as knowledge_router
    from app.api.v1.audit import router as audit_router
    from app.api.v1.admin import router as admin_router

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["Authentication"])
    app.include_router(stories_router, prefix="/api/v1/stories", tags=["User Stories"])
    app.include_router(test_cases_router, prefix="/api/v1/test-cases", tags=["Test Cases"])
    app.include_router(approvals_router, prefix="/api/v1/approvals", tags=["Approvals"])
    app.include_router(knowledge_router, prefix="/api/v1/knowledge", tags=["Knowledge Base"])
    app.include_router(audit_router, prefix="/api/v1/audit", tags=["Audit"])
    app.include_router(admin_router, prefix="/api/v1/admin", tags=["Admin"])

    # ── Health Checks ──
    @app.get("/health", tags=["Health"])
    async def health_check():
        return {
            "status": "healthy",
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT.value,
        }

    @app.get("/ready", tags=["Health"])
    async def readiness_check():
        checks = {}
        # Database check
        try:
            from app.infrastructure.database.session import get_engine
            async with get_engine().connect() as conn:
                await conn.execute(
                    __import__("sqlalchemy").text("SELECT 1")
                )
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"error: {e}"

        # Redis check
        try:
            from app.core.dependencies import _redis_pool
            if _redis_pool:
                await _redis_pool.ping()
                checks["redis"] = "ok"
            else:
                checks["redis"] = "not_initialized"
        except Exception as e:
            checks["redis"] = f"error: {e}"

        all_ok = all(v == "ok" for v in checks.values())
        return {
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
        }

    return app


# Singleton app instance for uvicorn
app = create_app()

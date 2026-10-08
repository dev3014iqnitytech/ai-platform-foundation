"""
Application entry point.

Wires together:
  - FastAPI app
  - lifespan (startup / shutdown)
  - dependency injection for services
  - structured logging
  - CORS (open for hackathon; restrict in production)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router, set_quote_service
from app.api.routes_v2 import router as router_v2, set_template_service_v2
from app.services.quote_service import QuoteService
from app.services.template_service import TemplateService
from app.services.template_service_v2 import TemplateServiceV2

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Project root ──────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise services on startup, release on shutdown."""
    logger.info("Starting Dealer Quote Generator — project root: %s", PROJECT_ROOT)

    mappings_dir = PROJECT_ROOT / "app" / "mappings"
    template_service = TemplateService(mappings_dir)
    quote_service = QuoteService(template_service, PROJECT_ROOT)
    set_quote_service(quote_service)

    # Initialise v2 Zero-Shot Dynamic Template Service
    template_service_v2 = TemplateServiceV2(PROJECT_ROOT)
    set_template_service_v2(template_service_v2)

    # Ensure output directory exists
    (PROJECT_ROOT / "output").mkdir(exist_ok=True)

    logger.info(
        "Available v1 templates: %s", template_service.list_templates()
    )
    logger.info(
        "Available v2 blueprints: %s",
        [t["id"] for t in template_service_v2.list_blueprints()],
    )
    yield
    logger.info("Shutting down Dealer Quote Generator")


# ── App factory ───────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dealer Quote Generator",
        version="2.0.0",
        description=(
            "Upload a dealer PDF template and a quote JSON.\n\n"
            "The service populates the template with dynamic quote data.\n\n"
            "- **v1 endpoints:** Template overlay and AcroForm fill via static mappings.\n"
            "- **v2 endpoints (`/api/v2`):** Zero-shot multimodal layout parsing and dynamic YAML blueprint rendering."
        ),
        contact={
            "name": "IqnityTech Hackathon Team",
            "email": "hackathon@iqnitytech.com",
        },
        license_info={"name": "MIT"},
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — open for hackathon demo; lock down in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include v1 legacy routes and segregated v2 adaptive routes
    app.include_router(router)
    app.include_router(router_v2, prefix="/api/v2")
    app.include_router(router_v2, prefix="/v2")
    return app


app = create_app()

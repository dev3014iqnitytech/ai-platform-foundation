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
from app.services.quote_service import QuoteService
from app.services.template_service import TemplateService

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

    # Ensure output directory exists
    (PROJECT_ROOT / "output").mkdir(exist_ok=True)

    logger.info(
        "Available templates: %s", template_service.list_templates()
    )
    yield
    logger.info("Shutting down Dealer Quote Generator")


# ── App factory ───────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dealer Quote Generator",
        version="1.0.0",
        description=(
            "Upload a dealer PDF template and a quote JSON.\n\n"
            "The service populates the template with dynamic quote data "
            "while **preserving the original dealer design**.\n\n"
            "Supports both **fillable** (AcroForm) and **static** PDFs."
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

    app.include_router(router)
    return app


app = create_app()

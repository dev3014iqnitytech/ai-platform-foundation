"""
FastAPI routes for the Dealer Quote Generator.

Endpoints:
  GET  /health               — liveness check
  POST /generate-quote       — main quote generation
  POST /analyze-template     — inspect a PDF template
  GET  /mock-quote           — return the bundled mock quote JSON
  GET  /templates            — list available template mappings
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, Response

from app.models.quote_models import QuoteRequest
from app.services.quote_service import QuoteService, DEFAULT_TEMPLATE

logger = logging.getLogger(__name__)

router = APIRouter()

# Dependency — injected by main.py via router state
_quote_service: Optional[QuoteService] = None


def set_quote_service(svc: QuoteService) -> None:
    global _quote_service
    _quote_service = svc


def _get_service() -> QuoteService:
    if _quote_service is None:
        raise RuntimeError("QuoteService not initialised")
    return _quote_service


# ── Health ────────────────────────────────────────────────────────────────────


@router.get(
    "/health",
    summary="Health check",
    response_description="Service health status",
    tags=["Monitoring"],
)
async def health() -> JSONResponse:
    return JSONResponse({"status": "healthy", "service": "dealer-quote-generator"})


# ── Mock quotes / Available Quotes ───────────────────────────────────────────


@router.get(
    "/quotes",
    summary="List available quote numbers from mock data",
    tags=["Demo"],
)
@router.get(
    "/api/quotes",
    summary="List available quote numbers from mock data",
    tags=["Demo"],
)
async def list_quotes() -> JSONResponse:
    """Return the collection of available quoteNumber keys for frontend selection."""
    quotes = _get_service().list_mock_quote_numbers()
    return JSONResponse({"quotes": quotes})


@router.get(
    "/mock-quote",
    summary="Return the bundled mock quote JSON",
    tags=["Demo"],
)
async def mock_quote(
    quote_number: Optional[str] = None,
) -> JSONResponse:
    """Useful for testing: returns the pre-built mock quote response matching quote_number or default."""
    try:
        data = _get_service().load_mock_quote(quote_number)
        return JSONResponse(data)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── Template listing ──────────────────────────────────────────────────────────


@router.get(
    "/templates",
    summary="List available template mappings",
    tags=["Templates"],
)
async def list_templates() -> JSONResponse:
    templates = _get_service().list_templates()
    return JSONResponse({"templates": templates})


# ── PDF analysis ──────────────────────────────────────────────────────────────


@router.post(
    "/analyze-template",
    summary="Analyse a dealer PDF template structure",
    tags=["Templates"],
)
async def analyze_template(
    file: UploadFile = File(..., description="Dealer PDF template"),
) -> JSONResponse:
    """
    Upload a dealer PDF and receive a structural analysis:
    - page count and dimensions
    - AcroForm fields (fillable PDF)
    - detected text blocks
    - fonts and images
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted",
        )

    pdf_bytes = await file.read()
    try:
        analysis = _get_service().analyze_template(pdf_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Convert dataclass to JSON-serialisable dict
    return JSONResponse(
        {
            "page_count": analysis.page_count,
            "is_fillable": analysis.is_fillable,
            "pdf_version": analysis.pdf_version,
            "has_images": analysis.has_images,
            "all_fonts": analysis.all_fonts,
            "acro_fields": [
                {
                    "name": f.name,
                    "field_type": f.field_type,
                    "page": f.page,
                    "rect": list(f.rect),
                }
                for f in analysis.acro_fields
            ],
            "pages": [
                {
                    "page_number": p.page_number,
                    "width": p.width,
                    "height": p.height,
                    "image_count": p.image_count,
                    "fonts": p.fonts,
                    "text_blocks_preview": p.text_blocks[:10],
                }
                for p in analysis.pages
            ],
        }
    )


# ── Quote generation ──────────────────────────────────────────────────────────


@router.post(
    "/generate-quote",
    summary="Generate a dealer-branded PDF quote",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Generated dealer PDF quote",
        },
        400: {"description": "Invalid PDF or quote data"},
        422: {"description": "Validation error in quote JSON"},
        500: {"description": "PDF generation error"},
    },
    tags=["Quote"],
)
async def generate_quote(
    file: UploadFile = File(..., description="Dealer PDF template"),
    quote_json: str = Form(..., description="Quote data as JSON string"),
    template_name: str = Form(
        default=DEFAULT_TEMPLATE,
        description="Template mapping name (without .yaml extension)",
    ),
) -> Response:
    """
    **Main endpoint.**

    Upload a dealer PDF template and provide quote data as JSON.
    Returns a branded PDF with all dynamic fields populated.

    ### Example cURL
    ```bash
    curl -X POST http://localhost:8000/generate-quote \\
      -F "file=@templates/dealer_template.pdf" \\
      -F 'quote_json={"quote":{"quoteNumber":"QT-001",...}}' \\
      -F "template_name=dealer_template" \\
      --output generated_quote.pdf
    ```
    """
    # ── Validate file ────────────────────────────────────────────────
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must be a PDF",
        )

    pdf_bytes = await file.read()

    # ── Parse & validate quote JSON ──────────────────────────────────
    try:
        quote_data: Dict[str, Any] = json.loads(quote_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON in quote_json: {exc}",
        )

    try:
        # Validate via Pydantic (raises ValidationError on bad data)
        QuoteRequest(**quote_data)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # ── Generate PDF ──────────────────────────────────────────────────
    try:
        result_bytes = _get_service().generate(pdf_bytes, quote_data, template_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during PDF generation")
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

    return Response(
        content=result_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="dealer_quote.pdf"',
            "X-Quote-Number": quote_data.get("quote", {}).get("quoteNumber", ""),
        },
    )


# ── Demo endpoint (mock quote + bundled template) ─────────────────────────────


@router.post(
    "/demo-quote",
    summary="Generate a demo quote using the mock JSON (no upload needed)",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
    tags=["Demo"],
)
async def demo_quote(
    file: UploadFile = File(..., description="Dealer PDF template"),
    template_name: str = Form(default=DEFAULT_TEMPLATE),
    quote_number: Optional[str] = Form(default=None, description="Selected quote sequence"),
) -> Response:
    """
    Shortcut for demos: upload only the PDF template; the mock quote
    JSON is loaded automatically from ``mock/quote_response.json``
    filtered by quote_number if specified.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")

    pdf_bytes = await file.read()
    svc = _get_service()

    try:
        quote_data = svc.load_mock_quote(quote_number)
        result_bytes = svc.generate(pdf_bytes, quote_data, template_name)
    except Exception as exc:
        logger.exception("Demo quote generation failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return Response(
        content=result_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="demo_quote.pdf"',
            "X-Quote-Number": quote_data.get("quote", {}).get("quoteNumber", ""),
        },
    )

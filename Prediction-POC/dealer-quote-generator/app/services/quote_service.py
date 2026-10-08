"""
Quote Service — orchestrates the end-to-end quote PDF generation.

Responsibilities:
  1. Validate the quote data (delegated to Pydantic models).
  2. Validate the uploaded PDF.
  3. Analyse the PDF to choose the correct fill strategy.
  4. Load the template mapping config.
  5. Dispatch to PDFFiller (fillable) or PDFOverlay (static).
  6. Return the final PDF bytes.

This is the single entry point for the business logic.  The API routes
call only this service.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app.models.quote_models import QuoteRequest
from app.services.pdf_analyzer import PDFAnalysis, PDFAnalyzer
from app.services.pdf_filler import PDFFiller
from app.services.pdf_overlay import PDFOverlay
from app.services.template_service import TemplateConfig, TemplateService
from app.utils.pdf_utils import validate_pdf_bytes

logger = logging.getLogger(__name__)

# Default template name used when caller does not specify one
DEFAULT_TEMPLATE = "dealer_template"


class QuoteService:
    """Orchestrates PDF quote generation."""

    def __init__(
        self,
        template_service: TemplateService,
        project_root: Path,
    ) -> None:
        self._templates = template_service
        self._analyzer = PDFAnalyzer()
        self._filler = PDFFiller()
        self._overlay = PDFOverlay(project_root)
        self._root = project_root

    # ── Public API ────────────────────────────────────────────────────

    def generate(
        self,
        pdf_bytes: bytes,
        quote_data: Dict[str, Any],
        template_name: str = DEFAULT_TEMPLATE,
    ) -> bytes:
        """
        Generate the final dealer PDF.

        Parameters
        ----------
        pdf_bytes : bytes      Uploaded dealer PDF template.
        quote_data : dict      Validated + raw quote JSON.
        template_name : str    Name of the YAML mapping to use.

        Returns
        -------
        bytes   Final PDF document.
        """
        # 1. Validate PDF
        validate_pdf_bytes(pdf_bytes)

        # 2. Validate quote data via Pydantic
        quote_model = QuoteRequest(**quote_data)
        validated_dict = quote_model.model_dump()

        # 3. Load template config
        config = self._templates.load(template_name)
        logger.info("Generating quote with template '%s'", template_name)

        # 4. Analyse PDF to choose strategy
        analysis = self._analyzer.analyze(pdf_bytes)
        logger.info(
            "PDF analysis: pages=%d, fillable=%s, acro_fields=%d",
            analysis.page_count,
            analysis.is_fillable,
            len(analysis.acro_fields),
        )

        # 5. Generate
        if analysis.is_fillable:
            logger.info("Strategy: AcroForm fill")
            return self._filler.fill(pdf_bytes, validated_dict, config)
        else:
            logger.info("Strategy: Overlay")
            return self._overlay.overlay(pdf_bytes, validated_dict, config)

    def analyze_template(self, pdf_bytes: bytes) -> PDFAnalysis:
        """Expose PDF analysis separately (used by /analyze endpoint)."""
        validate_pdf_bytes(pdf_bytes)
        return self._analyzer.analyze(pdf_bytes)

    def load_mock_quote(self) -> Dict[str, Any]:
        """Load the bundled mock quote JSON (for demo / testing)."""
        mock_path = self._root / "mock" / "quote_response.json"
        with open(mock_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def list_templates(self):
        return self._templates.list_templates()

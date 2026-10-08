"""
Tests for PDF overlay, PDF analyzer, and end-to-end PDF generation.
"""

from __future__ import annotations

import io
from pathlib import Path

import fitz
import pytest

from app.services.pdf_analyzer import PDFAnalyzer
from app.services.pdf_overlay import PDFOverlay
from app.services.template_service import TemplateService
from app.services.quote_service import QuoteService
from tests.conftest import VALID_QUOTE, PROJECT_ROOT


# ── PDFAnalyzer ───────────────────────────────────────────────────────────────


class TestPDFAnalyzer:
    def test_page_count(self, sample_pdf_bytes):
        analyzer = PDFAnalyzer()
        result = analyzer.analyze(sample_pdf_bytes)
        assert result.page_count == 1

    def test_non_fillable(self, sample_pdf_bytes):
        analyzer = PDFAnalyzer()
        result = analyzer.analyze(sample_pdf_bytes)
        assert result.is_fillable is False
        assert result.acro_fields == []

    def test_has_dimensions(self, sample_pdf_bytes):
        analyzer = PDFAnalyzer()
        result = analyzer.analyze(sample_pdf_bytes)
        page = result.pages[0]
        assert page.width > 0
        assert page.height > 0

    def test_fillable_pdf(self):
        """Create a fillable PDF and verify detection."""
        doc = fitz.open()
        page = doc.new_page()
        # Add a widget
        widget = fitz.Widget()
        widget.rect = fitz.Rect(100, 100, 300, 120)
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_name = "customer_name"
        widget.field_value = ""
        page.add_widget(widget)

        buf = io.BytesIO()
        doc.save(buf)
        doc.close()
        pdf_bytes = buf.getvalue()

        analyzer = PDFAnalyzer()
        result = analyzer.analyze(pdf_bytes)
        assert result.is_fillable is True
        assert len(result.acro_fields) >= 1
        assert result.acro_fields[0].name == "customer_name"


# ── PDFOverlay ────────────────────────────────────────────────────────────────


class TestPDFOverlay:
    def test_overlay_does_not_corrupt_pdf(self, sample_pdf_bytes):
        """Overlay must return valid PDF bytes."""
        template_service = TemplateService(PROJECT_ROOT / "app" / "mappings")
        config = template_service.load("dealer_template")
        overlay = PDFOverlay(PROJECT_ROOT)
        result = overlay.overlay(sample_pdf_bytes, VALID_QUOTE, config)

        # Validate result is still a valid PDF
        assert result.startswith(b"%PDF")
        doc = fitz.open(stream=result, filetype="pdf")
        assert doc.page_count == 1
        doc.close()

    def test_customer_name_in_output(self, sample_pdf_bytes):
        """Customer name should appear in the overlay output text."""
        template_service = TemplateService(PROJECT_ROOT / "app" / "mappings")
        config = template_service.load("dealer_template")
        overlay = PDFOverlay(PROJECT_ROOT)
        result = overlay.overlay(sample_pdf_bytes, VALID_QUOTE, config)

        doc = fitz.open(stream=result, filetype="pdf")
        page = doc.load_page(0)
        text = page.get_text("text")
        doc.close()
        assert "John Smith" in text

    def test_product_model_in_output(self, sample_pdf_bytes):
        """Product model should appear in the overlay output text."""
        template_service = TemplateService(PROJECT_ROOT / "app" / "mappings")
        config = template_service.load("dealer_template")
        overlay = PDFOverlay(PROJECT_ROOT)
        result = overlay.overlay(sample_pdf_bytes, VALID_QUOTE, config)

        doc = fitz.open(stream=result, filetype="pdf")
        page = doc.load_page(0)
        text = page.get_text("text")
        doc.close()
        assert "Fendt 724 Vario" in text

    def test_missing_field_does_not_crash(self, sample_pdf_bytes):
        """A quote missing optional fields should still produce a PDF."""
        template_service = TemplateService(PROJECT_ROOT / "app" / "mappings")
        config = template_service.load("dealer_template")
        overlay = PDFOverlay(PROJECT_ROOT)

        quote_minimal = {**VALID_QUOTE}
        quote_minimal["product"] = {**VALID_QUOTE["product"], "image": None}

        result = overlay.overlay(sample_pdf_bytes, quote_minimal, config)
        assert result.startswith(b"%PDF")


# ── QuoteService end-to-end ───────────────────────────────────────────────────


class TestQuoteServiceE2E:
    def _make_service(self) -> QuoteService:
        template_service = TemplateService(PROJECT_ROOT / "app" / "mappings")
        return QuoteService(template_service, PROJECT_ROOT)

    def test_generate_returns_pdf(self, sample_pdf_bytes):
        svc = self._make_service()
        result = svc.generate(sample_pdf_bytes, VALID_QUOTE)
        assert result.startswith(b"%PDF")

    def test_invalid_pdf_raises(self):
        svc = self._make_service()
        with pytest.raises(ValueError, match="not a valid PDF"):
            svc.generate(b"garbage", VALID_QUOTE)

    def test_mock_quote_loads(self):
        svc = self._make_service()
        mock = svc.load_mock_quote()
        assert "customer" in mock
        assert "pricing" in mock

    def test_price_formatting_in_output(self, sample_pdf_bytes):
        """Total price should appear formatted in the generated PDF."""
        svc = self._make_service()
        result = svc.generate(sample_pdf_bytes, VALID_QUOTE)
        doc = fitz.open(stream=result, filetype="pdf")
        page = doc.load_page(0)
        text = page.get_text("text")
        doc.close()
        # EUR 183,840.00
        assert "183,840.00" in text

    def test_options_table_in_output(self, sample_pdf_bytes):
        """Options descriptions should appear in the generated PDF."""
        svc = self._make_service()
        result = svc.generate(sample_pdf_bytes, VALID_QUOTE)
        doc = fitz.open(stream=result, filetype="pdf")
        page = doc.load_page(0)
        text = page.get_text("text")
        doc.close()
        assert "Front Loader" in text

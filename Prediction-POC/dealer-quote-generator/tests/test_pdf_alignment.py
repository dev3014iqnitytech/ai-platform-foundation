"""
test_pdf_alignment.py
───────────────────────
Comprehensive test suite validating PDF alignment and layout accuracy.
Verifies that overlaid dynamic data aligns precisely with the template
underlines, labels, tables, and sections across various data lengths and edge cases.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict

import fitz  # PyMuPDF
import pytest

from app.services.quote_service import QuoteService
from app.services.template_service import TemplateService
from tests.conftest import PROJECT_ROOT, VALID_QUOTE


@pytest.fixture
def quote_service() -> QuoteService:
    template_service = TemplateService(PROJECT_ROOT / "app" / "mappings")
    return QuoteService(template_service, PROJECT_ROOT)


@pytest.fixture
def dealer_template_bytes() -> bytes:
    template_path = PROJECT_ROOT / "templates" / "dealer_template.pdf"
    return template_path.read_bytes()


class TestPDFAlignment:
    """Validates precise coordinate positioning of overlaid elements."""

    def test_customer_fields_do_not_overlap_labels(
        self, quote_service: QuoteService, dealer_template_bytes: bytes
    ):
        """Customer dynamic values must start after x=140 to avoid overlapping labels."""
        result_pdf = quote_service.generate(dealer_template_bytes, VALID_QUOTE)
        doc = fitz.open(stream=result_pdf, filetype="pdf")
        page = doc[0]

        words = page.get_text("words")
        doc.close()

        # Find customer name "John" and ensure its x0 >= 140
        customer_words = [w for w in words if w[4] in ("John", "Smith")]
        assert len(customer_words) >= 2, "Customer name not found in rendered PDF"
        for w in customer_words:
            assert w[0] >= 140.0, f"Customer word '{w[4]}' overlaps label (x0={w[0]} < 140.0)"

    def test_product_fields_do_not_overlap_labels(
        self, quote_service: QuoteService, dealer_template_bytes: bytes
    ):
        """Product dynamic values must start after x=140 to avoid overlapping labels."""
        result_pdf = quote_service.generate(dealer_template_bytes, VALID_QUOTE)
        doc = fitz.open(stream=result_pdf, filetype="pdf")
        page = doc[0]

        words = page.get_text("words")
        doc.close()

        # Check serial number "F724-2026-001"
        serial_words = [w for w in words if "F724-2026-001" in w[4]]
        assert len(serial_words) >= 1, "Serial number not found"
        for w in serial_words:
            assert w[0] >= 140.0, f"Serial number '{w[4]}' overlaps label (x0={w[0]} < 140.0)"

    def test_quote_reference_in_header_bar(
        self, quote_service: QuoteService, dealer_template_bytes: bytes
    ):
        """Quote reference number must appear in the top header (PyMuPDF y0 < 90)."""
        result_pdf = quote_service.generate(dealer_template_bytes, VALID_QUOTE)
        doc = fitz.open(stream=result_pdf, filetype="pdf")
        page = doc[0]

        words = page.get_text("words")
        doc.close()

        ref_words = [w for w in words if "QT-TEST-001" in w[4]]
        assert len(ref_words) >= 1, "Quote number not found"
        # In PyMuPDF coordinate system (origin top-left): top header is y0 < 90
        assert ref_words[0][1] < 90.0, f"Quote number placed outside header bar: y0={ref_words[0][1]}"
        assert ref_words[0][0] >= 400.0, f"Quote number x position incorrect: x0={ref_words[0][0]}"

    def test_pricing_summary_y_coordinates(
        self, quote_service: QuoteService, dealer_template_bytes: bytes
    ):
        """Pricing values must appear inside the PRICING SUMMARY section (fitz y0 between 600 and 700)."""
        result_pdf = quote_service.generate(dealer_template_bytes, VALID_QUOTE)
        doc = fitz.open(stream=result_pdf, filetype="pdf")
        page = doc[0]

        words = page.get_text("words")
        doc.close()

        # 183,840.00 is the total price from VALID_QUOTE
        total_words = [w for w in words if "183,840.00" in w[4]]
        assert len(total_words) >= 1, "Total price not found"
        w = total_words[0]
        # In PyMuPDF coords: Total price green bar is around y0=672 to 694
        assert 650.0 <= w[1] <= 700.0, f"Total price not in green total bar: y0={w[1]}"
        # Must be right-aligned (x1 close to 565)
        assert w[2] <= 570.0, f"Total price overflows right margin: x1={w[2]}"

    def test_no_duplicate_table_headers(
        self, quote_service: QuoteService, dealer_template_bytes: bytes
    ):
        """Options table headers ('Code', 'Description', 'Price') must not be rendered twice."""
        result_pdf = quote_service.generate(dealer_template_bytes, VALID_QUOTE)
        doc = fitz.open(stream=result_pdf, filetype="pdf")
        page = doc[0]

        words = page.get_text("words")
        doc.close()

        # Count occurrences of header words
        code_count = sum(1 for w in words if w[4] == "Code")
        desc_count = sum(1 for w in words if w[4] == "Description")
        assert code_count == 1, f"Expected exactly 1 'Code' header, found {code_count}"
        assert desc_count == 1, f"Expected exactly 1 'Description' header, found {desc_count}"

    def test_no_duplicate_currency_prefix(
        self, quote_service: QuoteService, dealer_template_bytes: bytes
    ):
        """Pricing total must not render duplicate 'EUR EUR'."""
        result_pdf = quote_service.generate(dealer_template_bytes, VALID_QUOTE)
        doc = fitz.open(stream=result_pdf, filetype="pdf")
        page = doc[0]
        full_text = page.get_text("text")
        doc.close()

        assert "EUR EUR" not in full_text, "Duplicate currency found in output"

    def test_dealer_info_in_footer(
        self, quote_service: QuoteService, dealer_template_bytes: bytes
    ):
        """Dealer information must appear within the footer dark bar (fitz y0 >= 780)."""
        result_pdf = quote_service.generate(dealer_template_bytes, VALID_QUOTE)
        doc = fitz.open(stream=result_pdf, filetype="pdf")
        page = doc[0]

        words = page.get_text("words")
        doc.close()

        contact_words = [w for w in words if w[4] == "Wilson"]
        assert len(contact_words) >= 1, "Dealer contact person 'Wilson' not found"
        assert contact_words[0][1] >= 780.0, f"Dealer contact person above footer bar: y0={contact_words[0][1]}"


class TestEdgeCases:
    """Verifies robustness across edge cases and extreme data lengths."""

    def test_long_strings_truncate_with_ellipsis(
        self, quote_service: QuoteService, dealer_template_bytes: bytes
    ):
        """Extremely long names and addresses must truncate with ellipsis to prevent layout overflow."""
        quote = {**VALID_QUOTE}
        quote["customer"] = {
            **VALID_QUOTE["customer"],
            "name": "Sir Jonathan Alexander Bartholomew Higgins the Third of Greater Manchester Estate",
            "address": "123 Very Long Highway Road Boulevard Way, Industrial Area, Building 99, Manchester, Postcode 12345, United Kingdom",
        }

        result_pdf = quote_service.generate(dealer_template_bytes, quote)
        doc = fitz.open(stream=result_pdf, filetype="pdf")
        page = doc[0]
        full_text = page.get_text("text")
        words = page.get_text("words")
        doc.close()

        assert "..." in full_text, "Ellipsis expected for truncated long text"
        # Ensure customer words stay before x1=385 (within the underline boundary)
        for w in words:
            if "Jonathan" in w[4]:
                assert w[2] <= 385.0, f"Truncated text overflowed line: x1={w[2]}"

    def test_single_option_table(
        self, quote_service: QuoteService, dealer_template_bytes: bytes
    ):
        """Quotes with a single option item render cleanly without errors."""
        quote = {**VALID_QUOTE, "options": [VALID_QUOTE["options"][0]]}
        result_pdf = quote_service.generate(dealer_template_bytes, quote)
        assert result_pdf.startswith(b"%PDF")

    def test_max_options_table(
        self, quote_service: QuoteService, dealer_template_bytes: bytes
    ):
        """Quotes with 4 option items fit within the 4 placeholder rows."""
        options = [
            {"code": f"OPT-00{i}", "description": f"Optional Equipment Component {i}", "price": 1000 * i}
            for i in range(1, 5)
        ]
        quote = {**VALID_QUOTE, "options": options}
        result_pdf = quote_service.generate(dealer_template_bytes, quote)
        doc = fitz.open(stream=result_pdf, filetype="pdf")
        page = doc[0]
        text = page.get_text("text")
        doc.close()

        for opt in options:
            assert opt["code"] in text
            assert opt["description"] in text

    def test_missing_optional_product_image(
        self, quote_service: QuoteService, dealer_template_bytes: bytes
    ):
        """Quote with None for image still renders successfully."""
        quote = {**VALID_QUOTE}
        quote["product"] = {**VALID_QUOTE["product"], "image": None}
        result_pdf = quote_service.generate(dealer_template_bytes, quote)
        assert result_pdf.startswith(b"%PDF")

    def test_all_user_templates_alignment_and_clean_underscores(
        self, quote_service: QuoteService
    ):
        """Verify alignment and absence of underscore guides across all user templates."""
        user_templates = [
            "dealer_template_one.pdf",
            "fendt_quote_template four.pdf",
            "fendt_dealer_quote_template_two.pdf",
            "fendt_dealer_quote_template three.pdf",
        ]
        for t_filename in user_templates:
            tpl_path = PROJECT_ROOT / "templates" / t_filename
            if not tpl_path.exists():
                continue
            pdf_bytes = tpl_path.read_bytes()
            res_pdf = quote_service.generate(pdf_bytes, VALID_QUOTE)
            assert res_pdf.startswith(b"%PDF")

            doc = fitz.open(stream=res_pdf, filetype="pdf")
            p0 = doc[0]
            text0 = p0.get_text()

            # Ensure customer name and serial number are present
            assert "John" in text0
            assert "F724-2026-001" in text0

            # Verify that unsightly underscore blank lines were removed
            for b in p0.get_text("dict")["blocks"]:
                if b.get("type") == 0:
                    for l in b.get("lines", []):
                        for s in l.get("spans", []):
                            t = s.get("text", "").strip()
                            assert not (len(t) > 3 and all(c == "_" for c in t)), (
                                f"Underscore guideline '{t}' was not redacted in {t_filename}"
                            )
            doc.close()


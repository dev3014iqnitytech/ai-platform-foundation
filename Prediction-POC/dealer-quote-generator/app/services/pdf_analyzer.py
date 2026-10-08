"""
PDF Analyzer — inspects an uploaded PDF and reports its structure.

Detects:
  - Page count and dimensions
  - AcroForm fields (fillable PDF)
  - Text blocks and approximate positions
  - Embedded images
  - Fonts used

This information is surfaced via the API so callers (and future AI assistants)
understand what they are working with.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class PageInfo:
    page_number: int        # 1-indexed
    width: float
    height: float
    text_blocks: List[Dict] = field(default_factory=list)
    image_count: int = 0
    fonts: List[str] = field(default_factory=list)


@dataclass
class AcroField:
    name: str
    field_type: str
    page: int               # 1-indexed
    rect: Tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF points


@dataclass
class PDFAnalysis:
    page_count: int
    is_fillable: bool
    acro_fields: List[AcroField]
    pages: List[PageInfo]
    has_images: bool
    all_fonts: List[str]
    pdf_version: str


# ── Analyzer ──────────────────────────────────────────────────────────────────


class PDFAnalyzer:
    """Stateless service that inspects a PDF given as raw bytes."""

    def analyze(self, pdf_bytes: bytes) -> PDFAnalysis:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            return self._run(doc)
        finally:
            doc.close()

    # ── Private ───────────────────────────────────────────────────────

    def _run(self, doc: fitz.Document) -> PDFAnalysis:
        acro_fields = self._extract_acro_fields(doc)
        pages = []
        all_fonts: set[str] = set()
        has_images = False

        for page_idx in range(doc.page_count):
            page = doc.load_page(page_idx)
            rect = page.rect

            # Text blocks
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)  # type: ignore[attr-defined]
            text_blocks = []
            for block in blocks.get("blocks", []):
                if block.get("type") == 0:  # text block
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            text_blocks.append(
                                {
                                    "text": span.get("text", "").strip(),
                                    "x": round(span["bbox"][0], 1),
                                    "y": round(span["bbox"][1], 1),
                                    "font": span.get("font", ""),
                                    "size": round(span.get("size", 0), 1),
                                }
                            )
                            all_fonts.add(span.get("font", ""))

            # Images
            img_list = page.get_images(full=False)
            if img_list:
                has_images = True

            # Fonts on page
            page_fonts = [f[3] for f in page.get_fonts(full=True)]

            pages.append(
                PageInfo(
                    page_number=page_idx + 1,
                    width=round(rect.width, 2),
                    height=round(rect.height, 2),
                    text_blocks=text_blocks[:50],  # cap for API response
                    image_count=len(img_list),
                    fonts=page_fonts,
                )
            )

        return PDFAnalysis(
            page_count=doc.page_count,
            is_fillable=len(acro_fields) > 0,
            acro_fields=acro_fields,
            pages=pages,
            has_images=has_images,
            all_fonts=sorted(all_fonts),
            pdf_version=doc.pdf_version(),
        )

    def _extract_acro_fields(self, doc: fitz.Document) -> List[AcroField]:
        """Extract AcroForm widget fields from the document."""
        fields: List[AcroField] = []
        for page_idx in range(doc.page_count):
            page = doc.load_page(page_idx)
            for widget in page.widgets() or []:
                fields.append(
                    AcroField(
                        name=widget.field_name or "",
                        field_type=widget.field_type_string or "unknown",
                        page=page_idx + 1,
                        rect=(
                            round(widget.rect.x0, 1),
                            round(widget.rect.y0, 1),
                            round(widget.rect.x1, 1),
                            round(widget.rect.y1, 1),
                        ),
                    )
                )
        return fields

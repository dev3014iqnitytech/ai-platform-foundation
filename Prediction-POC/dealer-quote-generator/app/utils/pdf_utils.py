"""
PDF utility helpers used across services.

Deliberately kept small — heavy logic lives in services.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
PDF_MAGIC = b"%PDF"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


# ── Validation ────────────────────────────────────────────────────────────────


def validate_pdf_bytes(data: bytes) -> None:
    """Raise ValueError if *data* does not look like a valid PDF."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"Uploaded file exceeds maximum allowed size of "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
        )
    if not data.startswith(PDF_MAGIC):
        raise ValueError("Uploaded file is not a valid PDF (missing %PDF header)")
    # Try opening with PyMuPDF to catch corrupt files
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        if doc.page_count == 0:
            raise ValueError("PDF file has zero pages")
        doc.close()
    except fitz.FileDataError as exc:
        raise ValueError(f"Corrupt or unreadable PDF: {exc}") from exc


# ── Font helpers ──────────────────────────────────────────────────────────────

# Mapping of clean names → ReportLab / fitz built-in font names
FONT_ALIASES: dict[str, str] = {
    "helvetica": "Helvetica",
    "helvetica-bold": "Helvetica-Bold",
    "helveticabold": "Helvetica-Bold",
    "times": "Times-Roman",
    "times-bold": "Times-Bold",
    "courier": "Courier",
    "courier-bold": "Courier-Bold",
}


def resolve_font(font_name: str, bold: bool = False) -> str:
    """Normalise a font name to a ReportLab-compatible identifier."""
    key = font_name.lower().strip()
    if bold and not key.endswith("bold"):
        key = key + "bold"
    resolved = FONT_ALIASES.get(key, font_name)
    return resolved


# ── PDF page-size helpers ─────────────────────────────────────────────────────


def get_page_dimensions(pdf_bytes: bytes, page_index: int = 0) -> tuple[float, float]:
    """Return (width, height) in PDF points for the given page (0-indexed)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc.load_page(page_index)
        rect = page.rect
        return float(rect.width), float(rect.height)
    finally:
        doc.close()


# ── Value formatting ──────────────────────────────────────────────────────────


def format_value(value: object, fmt: Optional[str] = None, currency: str = "EUR") -> str:
    """Convert a raw value to its display string."""
    if fmt == "currency":
        try:
            amount = float(value)  # type: ignore[arg-type]
            return f"{currency} {amount:,.2f}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


# ── Coordinate conversion ─────────────────────────────────────────────────────


def rl_to_fitz_y(y_rl: float, page_height: float) -> float:
    """
    Convert a ReportLab y-coordinate (bottom=0) to a PyMuPDF y-coordinate
    (top=0).  Both use PDF points.
    """
    return page_height - y_rl


def fitz_to_rl_y(y_fitz: float, page_height: float) -> float:
    """Inverse of rl_to_fitz_y."""
    return page_height - y_fitz


# ── Misc ──────────────────────────────────────────────────────────────────────


def resolve_asset_path(asset_path: str, base_dir: Path) -> Optional[Path]:
    """
    Resolve an asset path from the quote JSON.

    *asset_path* may be relative (e.g. 'assets/image.png') or absolute.
    Returns None if the file does not exist.

    Security: we reject any path that escapes *base_dir*.
    """
    candidate = (base_dir / asset_path).resolve()
    # Prevent path traversal outside the project root
    try:
        candidate.relative_to(base_dir.resolve())
    except ValueError:
        logger.warning("Rejected asset path outside base directory: %s", asset_path)
        return None
    if candidate.is_file():
        return candidate
    logger.warning("Asset not found: %s", candidate)
    return None

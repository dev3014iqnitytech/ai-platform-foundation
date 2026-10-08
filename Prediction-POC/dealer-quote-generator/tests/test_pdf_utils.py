"""
Tests for PDF utilities — validation, formatting, coordinate conversion.
"""

from __future__ import annotations

import io
import pytest

from app.utils.pdf_utils import (
    format_value,
    resolve_font,
    rl_to_fitz_y,
    fitz_to_rl_y,
    validate_pdf_bytes,
    resolve_asset_path,
)
from pathlib import Path


# ── validate_pdf_bytes ────────────────────────────────────────────────────────


class TestValidatePdfBytes:
    def test_valid_pdf(self, sample_pdf_bytes):
        # Should not raise
        validate_pdf_bytes(sample_pdf_bytes)

    def test_empty_bytes(self):
        with pytest.raises(ValueError, match="not a valid PDF"):
            validate_pdf_bytes(b"not a pdf")

    def test_too_large(self, sample_pdf_bytes):
        big = sample_pdf_bytes * 10000  # make it huge
        # Only raises if > 50MB; sample PDF is tiny so patch threshold
        from app.utils import pdf_utils
        original = pdf_utils.MAX_UPLOAD_BYTES
        pdf_utils.MAX_UPLOAD_BYTES = 10  # 10 bytes
        try:
            with pytest.raises(ValueError, match="exceeds maximum"):
                validate_pdf_bytes(sample_pdf_bytes)
        finally:
            pdf_utils.MAX_UPLOAD_BYTES = original

    def test_corrupt_pdf(self):
        with pytest.raises(ValueError):
            validate_pdf_bytes(b"%PDF-1.4 corrupt garbage!!!!")


# ── format_value ──────────────────────────────────────────────────────────────


class TestFormatValue:
    def test_plain_string(self):
        assert format_value("Hello") == "Hello"

    def test_currency_format(self):
        result = format_value(145000, fmt="currency", currency="EUR")
        assert "145,000.00" in result
        assert "EUR" in result

    def test_currency_zero(self):
        result = format_value(0, fmt="currency", currency="GBP")
        assert "0.00" in result

    def test_none_fmt(self):
        assert format_value(42, fmt=None) == "42"


# ── resolve_font ──────────────────────────────────────────────────────────────


class TestResolveFont:
    def test_helvetica(self):
        assert resolve_font("Helvetica") == "Helvetica"

    def test_bold_suffix(self):
        assert resolve_font("Helvetica", bold=True) == "Helvetica-Bold"

    def test_alias_lookup(self):
        assert resolve_font("times") == "Times-Roman"

    def test_unknown_font_passthrough(self):
        # Unknown fonts pass through as-is
        assert resolve_font("MyCustomFont") == "MyCustomFont"


# ── Coordinate conversion ─────────────────────────────────────────────────────


class TestCoordinateConversion:
    PAGE_HEIGHT = 842.0  # A4

    def test_bottom_corner(self):
        # RL y=0 → fitz y = page_height
        assert rl_to_fitz_y(0, self.PAGE_HEIGHT) == self.PAGE_HEIGHT

    def test_top_corner(self):
        # RL y=page_height → fitz y = 0
        assert rl_to_fitz_y(self.PAGE_HEIGHT, self.PAGE_HEIGHT) == 0

    def test_round_trip(self):
        y = 300.0
        assert fitz_to_rl_y(rl_to_fitz_y(y, self.PAGE_HEIGHT), self.PAGE_HEIGHT) == y


# ── resolve_asset_path ────────────────────────────────────────────────────────


class TestResolveAssetPath:
    def test_valid_path(self, tmp_path):
        # Create a real file
        asset = tmp_path / "assets" / "test.png"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"fake png")
        result = resolve_asset_path("assets/test.png", tmp_path)
        assert result == asset

    def test_missing_file(self, tmp_path):
        result = resolve_asset_path("assets/missing.png", tmp_path)
        assert result is None

    def test_path_traversal_rejected(self, tmp_path):
        result = resolve_asset_path("../../etc/passwd", tmp_path)
        # Either None (file doesn't exist) or None (rejected)
        assert result is None

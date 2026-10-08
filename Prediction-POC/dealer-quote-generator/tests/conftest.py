"""
Shared test fixtures.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Dict

import pytest

# ── Project root (used to resolve real files) ─────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Minimal valid quote ───────────────────────────────────────────────────────

VALID_QUOTE: Dict[str, Any] = {
    "quote": {
        "quoteNumber": "QT-TEST-001",
        "quoteDate": "2026-10-08",
        "validUntil": "2026-10-22",
    },
    "customer": {
        "name": "John Smith",
        "company": "ABC Farms",
        "address": "123 Farm Road, Manchester",
        "email": "john.smith@example.com",
        "phone": "+44 7700 900123",
    },
    "product": {
        "brand": "Fendt",
        "model": "Fendt 724 Vario",
        "series": "700 Vario",
        "configuration": "ProfiPlus",
        "serialNumber": "F724-2026-001",
    },
    "options": [
        {"code": "OPT-001", "description": "Front Loader", "price": 12500},
        {"code": "OPT-002", "description": "Front PTO", "price": 4200},
    ],
    "pricing": {
        "basePrice": 145000,
        "optionsPrice": 16700,
        "discount": 8500,
        "tax": 30640,
        "totalPrice": 183840,
        "currency": "EUR",
    },
    "dealer": {
        "name": "Example Fendt Dealer",
        "address": "Manchester, UK",
        "contactPerson": "David Wilson",
        "phone": "+44 161 1234567",
        "email": "sales@exampledealer.com",
    },
}


@pytest.fixture
def valid_quote() -> Dict[str, Any]:
    return dict(VALID_QUOTE)


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Generate a minimal valid PDF in memory using PyMuPDF."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text(
        (50, 750),
        "CUSTOMER INFORMATION\nCustomer Name: __________\nAddress: __________",
        fontsize=12,
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


@pytest.fixture
def project_root() -> Path:
    return PROJECT_ROOT

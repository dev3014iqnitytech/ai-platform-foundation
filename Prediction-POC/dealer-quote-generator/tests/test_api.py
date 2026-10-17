"""
FastAPI integration tests using httpx TestClient.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import VALID_QUOTE, PROJECT_ROOT


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def sample_pdf_bytes_module():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 750), "Sample Dealer Template", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


# ── Health ────────────────────────────────────────────────────────────────────


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


# ── Mock quote ────────────────────────────────────────────────────────────────


def test_mock_quote_returns_json(client):
    resp = client.get("/mock-quote")
    assert resp.status_code == 200
    data = resp.json()
    assert "customer" in data
    assert "pricing" in data


def test_list_quotes_endpoint(client):
    """Test /quotes and /api/quotes endpoints for dropdown menu population."""
    resp = client.get("/quotes")
    assert resp.status_code == 200
    quotes = resp.json().get("quotes")
    assert isinstance(quotes, list)
    assert len(quotes) == 3
    assert "QT-2026-001" in quotes
    assert "QT-2026-002" in quotes
    assert "QT-2026-003" in quotes

    # Also verify /api/quotes alias
    resp_api = client.get("/api/quotes")
    assert resp_api.status_code == 200
    assert resp_api.json()["quotes"] == quotes


def test_mock_quote_filtering_and_fallback(client):
    """Test filtering mock quote by quote_number and fallback behavior."""
    resp1 = client.get("/mock-quote?quote_number=QT-2026-002")
    assert resp1.status_code == 200
    assert resp1.json()["quote"]["quoteNumber"] == "QT-2026-002"
    assert resp1.json()["customer"]["name"] == "Emma Watson"

    resp2 = client.get("/mock-quote?quote_number=QT-2026-003")
    assert resp2.status_code == 200
    assert resp2.json()["quote"]["quoteNumber"] == "QT-2026-003"
    assert resp2.json()["customer"]["name"] == "Marcus Vance"

    # Fallback when unknown quoteNumber provided
    resp_fallback = client.get("/mock-quote?quote_number=QT-UNKNOWN-999")
    assert resp_fallback.status_code == 200
    assert resp_fallback.json()["quote"]["quoteNumber"] == "QT-2026-001"


# ── Template listing ──────────────────────────────────────────────────────────


def test_list_templates(client):
    resp = client.get("/templates")
    assert resp.status_code == 200
    assert "templates" in resp.json()
    assert "dealer_template" in resp.json()["templates"]


# ── Analyze template ──────────────────────────────────────────────────────────


def test_analyze_template(client, sample_pdf_bytes_module):
    resp = client.post(
        "/analyze-template",
        files={"file": ("test.pdf", sample_pdf_bytes_module, "application/pdf")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["page_count"] == 1
    assert "is_fillable" in data


def test_analyze_non_pdf_rejected(client):
    resp = client.post(
        "/analyze-template",
        files={"file": ("test.txt", b"not a pdf", "text/plain")},
    )
    assert resp.status_code == 400


# ── Generate quote ────────────────────────────────────────────────────────────


def test_generate_quote_returns_pdf(client, sample_pdf_bytes_module):
    resp = client.post(
        "/generate-quote",
        files={"file": ("template.pdf", sample_pdf_bytes_module, "application/pdf")},
        data={
            "quote_json": json.dumps(VALID_QUOTE),
            "template_name": "dealer_template",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_generate_quote_invalid_json(client, sample_pdf_bytes_module):
    resp = client.post(
        "/generate-quote",
        files={"file": ("template.pdf", sample_pdf_bytes_module, "application/pdf")},
        data={"quote_json": "{{broken json{{"},
    )
    assert resp.status_code == 400


def test_generate_quote_invalid_pdf(client):
    resp = client.post(
        "/generate-quote",
        files={"file": ("template.pdf", b"not-a-pdf", "application/pdf")},
        data={"quote_json": json.dumps(VALID_QUOTE)},
    )
    assert resp.status_code == 400


def test_generate_quote_non_pdf_file_rejected(client):
    resp = client.post(
        "/generate-quote",
        files={"file": ("template.txt", b"text content", "text/plain")},
        data={"quote_json": json.dumps(VALID_QUOTE)},
    )
    assert resp.status_code == 400


def test_generate_quote_missing_customer(client, sample_pdf_bytes_module):
    bad = {k: v for k, v in VALID_QUOTE.items() if k != "customer"}
    resp = client.post(
        "/generate-quote",
        files={"file": ("template.pdf", sample_pdf_bytes_module, "application/pdf")},
        data={"quote_json": json.dumps(bad)},
    )
    assert resp.status_code == 422


# ── Demo endpoint ─────────────────────────────────────────────────────────────


def test_demo_quote(client, sample_pdf_bytes_module):
    resp = client.post(
        "/demo-quote",
        files={"file": ("template.pdf", sample_pdf_bytes_module, "application/pdf")},
        data={"template_name": "dealer_template"},
    )
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
    assert resp.headers.get("X-Quote-Number") == "QT-2026-001"


def test_demo_quote_with_quote_number(client, sample_pdf_bytes_module):
    """Test demo quote when a specific quoteNumber sequence is selected."""
    resp = client.post(
        "/demo-quote",
        files={"file": ("template.pdf", sample_pdf_bytes_module, "application/pdf")},
        data={"template_name": "dealer_template", "quote_number": "QT-2026-002"},
    )
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
    assert resp.headers.get("X-Quote-Number") == "QT-2026-002"


def test_generate_quote_file_too_large(client):
    """Files exceeding 5MB are rejected with HTTP 400."""
    oversized = b"%PDF-1.4 " + (b"0" * (5 * 1024 * 1024 + 100))
    resp = client.post(
        "/generate-quote",
        files={"file": ("template.pdf", oversized, "application/pdf")},
        data={"quote_json": json.dumps(VALID_QUOTE)},
    )
    assert resp.status_code == 400
    assert "exceeds maximum allowed size" in resp.json()["detail"]

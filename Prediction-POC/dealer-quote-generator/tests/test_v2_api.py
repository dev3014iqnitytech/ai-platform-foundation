"""
Unit and integration tests for v2 Zero-Shot Dynamic Layout & Adaptive Rendering APIs.
Tests templates located in the templates/ folder:
  - templates/dealer_template.pdf
  - templates/fendt_dealer_quote_template.pdf
"""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import fitz

from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
DEALER_TEMPLATE = TEMPLATES_DIR / "dealer_template.pdf"
FENDT_TEMPLATE = TEMPLATES_DIR / "fendt_dealer_quote_template.pdf"
MOCK_QUOTE_FILE = PROJECT_ROOT / "mock" / "quote_response.json"


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def mock_quote_data():
    return json.loads(MOCK_QUOTE_FILE.read_text(encoding="utf-8"))


# ── V2 Template Discovery & Retrieval ────────────────────────────────────────


def test_v2_list_templates(client):
    """Verify v2 lists seeded and registered blueprints."""
    res = client.get("/api/v2/templates")
    assert res.status_code == 200
    data = res.json()
    assert "templates" in data
    ids = [t["id"] for t in data["templates"]]
    assert "dealer_template" in ids
    assert "fendt_dealer_quote_template" in ids


def test_v2_get_template_details(client):
    """Verify blueprint details can be fetched for fendt_dealer_quote_template."""
    res = client.get("/api/v2/templates/fendt_dealer_quote_template")
    assert res.status_code == 200
    body = res.json()
    assert body["template_id"] == "fendt_dealer_quote_template"
    assert "blueprint" in body
    assert "blueprint_yaml" in body
    assert body["blueprint"]["version"] == "2.0"
    # Check that elements exist
    elements = body["blueprint"]["elements"]
    assert len(elements) > 0


def test_v2_get_template_not_found(client):
    """Verify 404 on nonexistent template."""
    res = client.get("/api/v2/templates/nonexistent_template_999")
    assert res.status_code == 404


# ── V2 Template Upload & Zero-Shot Ingestion ─────────────────────────────────


def test_v2_upload_fendt_template(client):
    """Test uploading fendt_dealer_quote_template.pdf for zero-shot decomposition."""
    assert FENDT_TEMPLATE.exists(), "fendt_dealer_quote_template.pdf must exist"
    with open(FENDT_TEMPLATE, "rb") as f:
        res = client.post(
            "/api/v2/templates/upload",
            files={"file": ("fendt_dealer_quote_template.pdf", f, "application/pdf")},
            data={"template_name": "test_uploaded_fendt"},
        )
    assert res.status_code == 200
    payload = res.json()
    assert payload["template_name"] == "test_uploaded_fendt"
    assert "blueprint_yaml" in payload
    assert "elements" in payload["blueprint"]

    # Verify blueprint can now be retrieved
    get_res = client.get(f"/api/v2/templates/{payload['template_id']}")
    assert get_res.status_code == 200


def test_v2_upload_dealer_template(client):
    """Test uploading dealer_template.pdf for zero-shot decomposition."""
    assert DEALER_TEMPLATE.exists(), "dealer_template.pdf must exist"
    with open(DEALER_TEMPLATE, "rb") as f:
        res = client.post(
            "/api/v2/templates/upload",
            files={"file": ("dealer_template.pdf", f, "application/pdf")},
            data={"template_name": "test_uploaded_dealer"},
        )
    assert res.status_code == 200
    payload = res.json()
    assert payload["template_name"] == "test_uploaded_dealer"


# ── V2 Document Generation Endpoint ──────────────────────────────────────────


def test_v2_generate_document_fendt_standalone(client, mock_quote_data):
    """Generate document using fendt_dealer_quote_template blueprint in standalone mode."""
    req_body = {
        "template_id": "fendt_dealer_quote_template",
        "data": mock_quote_data,
        "mode": "standalone",
    }
    res = client.post("/api/v2/documents/generate", json=req_body)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF-")

    # Verify PDF content via PyMuPDF
    doc = fitz.open(stream=res.content, filetype="pdf")
    assert len(doc) >= 1
    page_text = doc[0].get_text()
    assert "John Smith" in page_text
    assert "Fendt 724 Vario" in page_text
    assert "QT-2026-00125" in page_text


def test_v2_generate_document_dealer_standalone(client, mock_quote_data):
    """Generate document using dealer_template blueprint in standalone mode."""
    req_body = {
        "template_id": "dealer_template",
        "data": mock_quote_data,
        "mode": "standalone",
    }
    res = client.post("/api/v2/documents/generate", json=req_body)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF-")

    doc = fitz.open(stream=res.content, filetype="pdf")
    page_text = doc[0].get_text()
    assert "John Smith" in page_text
    assert "QT-2026-00125" in page_text


def test_v2_generate_document_overlay_mode(client, mock_quote_data):
    """Generate document merging onto template PDF background."""
    req_body = {
        "template_id": "dealer_template",
        "data": mock_quote_data,
        "mode": "overlay",
    }
    res = client.post("/api/v2/documents/generate", json=req_body)
    assert res.status_code == 200
    assert res.content.startswith(b"%PDF-")

    doc = fitz.open(stream=res.content, filetype="pdf")
    page_text = doc[0].get_text()
    assert "John Smith" in page_text


# ── V2 Quote Convenience & Demo Endpoints ─────────────────────────────────────


def test_v2_get_mock_quote(client):
    """Test retrieving mock quote via v2 route."""
    res = client.get("/api/v2/mock-quote")
    assert res.status_code == 200
    data = res.json()
    assert data["quote"]["quoteNumber"] == "QT-2026-00125"
    assert data["customer"]["name"] == "John Smith"


def test_v2_demo_quote_fendt(client):
    """Test demo quote for fendt_dealer_quote_template with file upload and verify precise alignment."""
    with open(FENDT_TEMPLATE, "rb") as f:
        res = client.post(
            "/api/v2/demo-quote",
            files={"file": ("fendt_dealer_quote_template.pdf", f, "application/pdf")},
            data={"template_name": "fendt_dealer_quote_template"},
        )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "attachment;" in res.headers["content-disposition"]
    assert res.content.startswith(b"%PDF-")

    doc = fitz.open(stream=res.content, filetype="pdf")
    page = doc[0]
    words = page.get_text("words")
    doc.close()

    # 1. Customer Name "John" must be near x=130 and PyMuPDF y around 140-160
    name_words = [w for w in words if w[4] == "John"]
    assert len(name_words) >= 1
    assert 120.0 <= name_words[0][0] <= 145.0, f"Customer Name x-coord misaligned: {name_words[0][0]}"
    assert 140.0 <= name_words[0][1] <= 165.0, f"Customer Name y-coord misaligned: {name_words[0][1]}"

    # 2. Customer Email "john.smith@example.com" must be in the right column (x >= 350)
    email_words = [w for w in words if "example.com" in w[4]]
    assert len(email_words) >= 1
    assert email_words[0][0] >= 350.0, f"Customer Email not in right column: {email_words[0][0]}"

    # 3. Product Model "Fendt 724 Vario" must be in Product Info section
    model_words = [w for w in words if w[4] == "724"]
    assert len(model_words) >= 1
    assert 80.0 <= model_words[0][0] <= 120.0, f"Model x-coord misaligned: {model_words[0][0]}"
    assert 260.0 <= model_words[0][1] <= 290.0, f"Model y-coord misaligned: {model_words[0][1]}"

    # 4. Total Price must be in green banner (y between 520 and 545)
    total_words = [w for w in words if "194,520.00" in w[4]]
    assert len(total_words) >= 1
    assert 515.0 <= total_words[0][1] <= 545.0, f"Total price not in green banner: {total_words[0][1]}"

    # 5. Dealer Name must be in footer bar (y >= 570)
    dealer_words = [w for w in words if "Example" in w[4]]
    assert len(dealer_words) >= 1
    assert dealer_words[0][1] >= 570.0, f"Dealer name above footer bar: {dealer_words[0][1]}"


def test_v2_demo_quote_route_without_api_prefix(client):
    """Verify /v2/demo-quote works seamlessly without /api prefix."""
    with open(FENDT_TEMPLATE, "rb") as f:
        res = client.post(
            "/v2/demo-quote",
            files={"file": ("fendt_dealer_quote_template.pdf", f, "application/pdf")},
        )
    assert res.status_code == 200
    assert res.content.startswith(b"%PDF-")


def test_v2_demo_quote_dealer(client):
    """Test demo quote for dealer_template with file upload."""
    with open(DEALER_TEMPLATE, "rb") as f:
        res = client.post(
            "/api/v2/demo-quote",
            files={"file": ("dealer_template.pdf", f, "application/pdf")},
            data={"template_name": "dealer_template"},
        )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "attachment;" in res.headers["content-disposition"]
    assert res.content.startswith(b"%PDF-")

    doc = fitz.open(stream=res.content, filetype="pdf")
    page_text = doc[0].get_text()
    assert "John Smith" in page_text


def test_v2_demo_quote_get(client):
    """Test one-click direct browser download link via GET."""
    res = client.get("/api/v2/demo-quote?template_name=fendt_dealer_quote_template")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "attachment;" in res.headers["content-disposition"]
    assert res.content.startswith(b"%PDF-")


def test_v2_generate_quote_with_uploaded_file(client, mock_quote_data):
    """Test ad-hoc upload directly inside generate-quote endpoint."""
    with open(FENDT_TEMPLATE, "rb") as f:
        res = client.post(
            "/api/v2/generate-quote",
            files={"file": ("fendt_dealer_quote_template.pdf", f, "application/pdf")},
            data={
                "quote_json": json.dumps(mock_quote_data),
                "template_name": "ad_hoc_fendt",
                "mode": "standalone",
            },
        )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF-")

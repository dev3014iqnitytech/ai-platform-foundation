# 🚜 Dealer Quote Generator — Hackathon MVP

> **"Any dealer can provide their existing PDF quote template.  
> We dynamically populate it with quote information — without redesigning the dealer's document."**

---

## What It Does

```
Dealer PDF Template  +  Quote JSON
           ↓
   PDF Processing Engine
   (Analyzer → Mapper → Overlay/Filler)
           ↓
 Dealer-Branded Final Quote PDF
```

- **Type 1 – Fillable PDF**: Detects AcroForm fields and populates them directly.
- **Type 2 – Static PDF**: Builds a transparent overlay layer (ReportLab) and merges it onto the original using PyMuPDF — the dealer's design is never touched.

---

## Project Structure

```
dealer-quote-generator/
│
├── app/
│   ├── main.py                  # FastAPI app factory + lifespan
│   ├── api/
│   │   └── routes.py            # All HTTP endpoints
│   ├── services/
│   │   ├── pdf_analyzer.py      # Inspect PDF structure
│   │   ├── pdf_filler.py        # AcroForm filler (fillable PDFs)
│   │   ├── pdf_overlay.py       # Overlay engine (static PDFs)
│   │   ├── quote_service.py     # Orchestrator / entry point
│   │   └── template_service.py  # YAML mapping loader
│   ├── models/
│   │   └── quote_models.py      # Pydantic validation models
│   ├── mappings/
│   │   └── dealer_template.yaml # Field position mapping
│   └── utils/
│       └── pdf_utils.py         # Shared helpers
│
├── mock/
│   └── quote_response.json      # Mock quote data (isolated for easy swap)
├── templates/                   # Upload your dealer PDF here
├── assets/                      # Product images
├── output/                      # Generated PDFs (runtime)
├── tests/
│   ├── conftest.py
│   ├── test_quote_validation.py
│   ├── test_pdf_utils.py
│   ├── test_pdf_generation.py
│   └── test_api.py
├── generate_demo_template.py    # Script to create a demo dealer PDF
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## Installation

### Requirements

- Python 3.11+
- pip

### Steps

```bash
# 1. Clone / enter the project
cd dealer-quote-generator

# 2. (Optional) create a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Generate the demo dealer PDF template
python generate_demo_template.py
# → templates/dealer_template.pdf
```

---

## Starting the Application

```bash
uvicorn app.main:app --reload
```

- API: **http://localhost:8000**
- Swagger UI: **http://localhost:8000/docs**
- ReDoc: **http://localhost:8000/redoc**

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/mock-quote` | Returns bundled mock quote JSON |
| `GET` | `/templates` | Lists available YAML mappings |
| `POST` | `/analyze-template` | Inspect a dealer PDF structure |
| `POST` | `/generate-quote` | **Main endpoint** — generate the final PDF |
| `POST` | `/demo-quote` | Quick demo — uses mock quote automatically |

---

## How to Upload a Dealer PDF

### Via Swagger (`/docs`)

1. Open **http://localhost:8000/docs**
2. Click `POST /generate-quote`
3. Click **Try it out**
4. Upload your PDF in the `file` field
5. Paste the quote JSON into `quote_json`
6. Click **Execute**
7. Download the generated PDF from the response

### Via cURL

```bash
curl -X POST http://localhost:8000/generate-quote \
  -F "file=@templates/dealer_template.pdf" \
  -F 'quote_json=@mock/quote_response.json' \
  -F "template_name=dealer_template" \
  --output output/final_quote.pdf
```

> **Note:** `quote_json` must be a JSON *string* (not a file). Use `$(cat mock/quote_response.json)` on Linux/macOS or PowerShell's `Get-Content`.

### PowerShell Example

```powershell
$quoteJson = Get-Content mock\quote_response.json -Raw
$pdf = [System.IO.File]::ReadAllBytes("templates\dealer_template.pdf")
$boundary = [System.Guid]::NewGuid().ToString()
# ... or simply use the /docs Swagger UI for interactive testing
```

### Demo Endpoint (No JSON Needed)

```bash
curl -X POST http://localhost:8000/demo-quote \
  -F "file=@templates/dealer_template.pdf" \
  --output output/demo_quote.pdf
```

---

## How to Use the Mock Quote

The mock quote lives in `mock/quote_response.json`. It is completely isolated from the PDF processing code so it can later be replaced with a real CPQ/Quote API response.

```json
{
  "quote":    { "quoteNumber": "QT-2026-00125", ... },
  "customer": { "name": "John Smith", ... },
  "product":  { "model": "Fendt 724 Vario", ... },
  "options":  [ { "description": "Front Loader", "price": 12500 }, ... ],
  "pricing":  { "basePrice": 145000, "totalPrice": 194520, ... },
  "dealer":   { "name": "Example Fendt Dealer", ... }
}
```

Access it at any time via `GET /mock-quote`.

---

## How Field Mapping Works

Every dealer has a YAML mapping file in `app/mappings/`. It tells the engine:

- **Which JSON field** to read (`source: customer.name`)
- **Which page** to write it on (`page: 1`)
- **Where on the page** (`x: 50`, `y: 660` — ReportLab bottom-left origin)
- **How to format it** (`format: currency`)
- **What font/size/color** to use

```yaml
customer_name:
  source: customer.name
  page: 1
  x: 50
  y: 660
  font_size: 11
  bold: true
  color: [0, 0, 0]
```

The options table is also configured in YAML:

```yaml
options_table:
  page: 1
  start_x: 50
  start_y: 420
  row_height: 18
  max_rows: 10
  columns:
    code:       { label: "Code", x_offset: 0,   width: 80 }
    description:{ label: "Description", x_offset: 85, width: 200 }
    price:      { label: "Price", x_offset: 295, width: 100, format: currency }
```

---

## How to Add Another Dealer Template

1. **Create a new YAML** mapping in `app/mappings/`:

   ```bash
   cp app/mappings/dealer_template.yaml app/mappings/dealer_b.yaml
   ```

2. **Adjust the coordinates** in `dealer_b.yaml` to match the new PDF layout.
   - Use `POST /analyze-template` to inspect the PDF and identify text positions.

3. **Pass the new template name** when generating:

   ```bash
   curl -X POST http://localhost:8000/generate-quote \
     -F "file=@templates/dealer_b.pdf" \
     -F "quote_json=..." \
     -F "template_name=dealer_b" \
     --output output/dealer_b_quote.pdf
   ```

No application code changes required.

---

## How to Generate the Final PDF

The flow:

```
1. POST /generate-quote (PDF + JSON)
         ↓
2. Validate PDF (magic bytes, size, page count)
         ↓
3. Validate JSON (Pydantic models)
         ↓
4. Load YAML template mapping
         ↓
5. Analyze PDF → detect fillable vs static
         ↓
6a. Fillable: PDFFiller fills AcroForm widgets
6b. Static:   PDFOverlay builds ReportLab layer,
              merges with PyMuPDF show_pdf_page()
         ↓
7. Return generated PDF bytes
```

---

## Running Tests

```bash
pytest tests/ -v
```

Tests cover:
- Quote JSON validation (dates, emails, pricing totals)
- PDF validation (magic bytes, size limits, corrupt files)
- Font resolution and coordinate conversion
- Path traversal protection
- PDF overlay correctness (customer name/model/price appear in output)
- FastAPI endpoint integration tests

---

## Docker

```bash
# Build
docker build -t dealer-quote-generator .

# Run
docker run -p 8000:8000 dealer-quote-generator

# Access
open http://localhost:8000/docs
```

---

## Known MVP Limitations

| Limitation | Detail |
|-----------|--------|
| **Manual coordinates** | Field positions in YAML must be set manually. Requires knowledge of the PDF layout. Future: AI-assisted detection. |
| **Options table overflow** | If more than `max_rows` options exist, extra rows are truncated with a warning. No automatic pagination yet. |
| **Font matching** | New text always uses configured fonts (default: Helvetica). Does not auto-detect and match the dealer's original fonts. |
| **Right-aligned text** | Right-alignment in the options table is approximate (no text-width measurement). |
| **Single-page overlay** | All fields from the YAML must fit on page 1 (multi-page YAML is supported, but only if the dealer PDF has matching pages). |
| **No authentication** | The API is open — suitable for hackathon, not production. |
| **Local images only** | Product images must be local files (no URL download). |
| **No template versioning** | One YAML per dealer; no version history. |

---

## Future Production Architecture

```
React / Angular UI
        ↓
API Gateway (Azure APIM)
        ↓
Quote PDF Service  ←──────────────── Template Management Service
        ↓                                      ↓
Quote / CPQ API                      Azure Blob Storage
        ↓                            (PDF templates + mappings)
Document Storage (Azure Blob)
        ↓
Audit Logging / Telemetry
```

### Planned Enhancements

- **AI-assisted field detection** — dealer uploads PDF → AI suggests field mapping → dealer approves → mapping saved
- **Azure Blob Storage** for template and output PDF storage
- **Azure Functions / Container Apps** for serverless deployment
- **Database-backed template mappings** with versioning
- **Authentication / authorization** (Entra ID / OAuth2)
- **Digital signatures** on generated quotes
- **PDF encryption** for sensitive pricing
- **OCR support** for scanned dealer PDFs
- **Async PDF generation** with status polling
- **Audit logs** (who generated what quote, when)
- **Monitoring & telemetry** (Application Insights)

---

## Tech Stack

| Technology | Purpose |
|-----------|---------|
| **FastAPI** | REST API framework |
| **PyMuPDF (fitz)** | PDF inspection, merging, AcroForm |
| **ReportLab** | Transparent overlay layer generation |
| **Pydantic v2** | Quote data validation |
| **PyYAML** | Template mapping configuration |
| **pytest + httpx** | Testing |
| **uvicorn** | ASGI server |
| **Docker** | Containerisation |

---

*Built for the IqnityTech Hackathon — October 2026*

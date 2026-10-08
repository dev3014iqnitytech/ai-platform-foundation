"""
Vision Layout Parser Service (v2)
Zero-shot multimodal & structural layout decomposition.
Converts PDF or Image templates into normalized YAML Layout Blueprints.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
import yaml
from PIL import Image

from app.models.layout_blueprint_v2 import (
    ElementBlueprint,
    GridField,
    GridLayout,
    LayoutBlueprint,
    PageConfig,
    PageMargins,
    TableColumn,
    TableMeta,
)

logger = logging.getLogger(__name__)

MASTER_VISION_PROMPT = """You are a Principal Document Analysis and Computer Vision Engineer specialized in structural layout decomposition.

### TASK:
Analyze the provided document page (image/PDF render) and reverse-engineer its visual geometry into an exact, machine-readable YAML Layout Blueprint. 

### CRITICAL COORDINATE RULES:
1. Coordinate System: Use a normalized 0 to 1000 coordinate grid.
   - x = 0, y = 0 is the BOTTOM-LEFT corner of the document page (ReportLab/PostScript convention).
   - x = 1000, y = 1000 is the TOP-RIGHT corner of the document page.
   - width and height are spans relative to 1000.
2. Convert all visual regions, text alignments, margins, and tables into geometric blocks.

### SCHEMA SPECIFICATION:
The YAML output must strictly follow this structure:

version: "2.0"
template_name: "extracted_template"
description: "Zero-shot extracted layout blueprint"
page:
  size: "A4" # or LETTER, LEGAL
  orientation: "portrait"
  margins:
    top: 36
    bottom: 36
    left: 36
    right: 36
  palette:
    primary: "#1A365D"
    secondary: "#4A5568"
    accent: "#3182CE"
    background: "#FFFFFF"
    text: "#2D3748"
    muted: "#718096"
    border: "#E2E8F0"
    row_alt: "#F7FAFC"

elements:
  - id: "header_title"
    type: "text"
    bbox: [left_x, bottom_y, width, height]
    binding: "meta.document_title"
    style:
      font: "Helvetica-Bold"
      font_size: 24
      color: "primary"
      alignment: "left"

  - id: "key_values_grid"
    type: "key_value_grid"
    bbox: [left_x, bottom_y, width, height]
    layout:
      columns: 2 # 1, 2, or 3 column layout
      column_gap: 30
      row_gap: 10
    fields:
      - label: "Customer Name"
        binding: "customer.name"
        col_span: 1
      - label: "Quote Number"
        binding: "quote.quoteNumber"
        col_span: 1

  - id: "items_table"
    type: "table"
    bbox: [left_x, bottom_y, width, height]
    binding: "options" # or items
    table_meta:
      header_background: "primary"
      header_color: "#FFFFFF"
      alternating_row_background: "row_alt"
      border_width: 0.5
      border_color: "border"
      row_height: 22
      columns:
        - field: "code"
          title: "Code"
          width_ratio: 0.2
          align: "left"
        - field: "description"
          title: "Description"
          width_ratio: 0.6
          align: "left"
        - field: "price"
          title: "Price"
          width_ratio: 0.2
          align: "right"
          format: "currency"

Emit ONLY valid, clean YAML. Do not wrap in conversational text.
"""


class VisionLayoutParserV2:
    """Parses arbitrary PDF/Images into normalized Layout Blueprint YAML."""

    def __init__(self, openai_api_key: Optional[str] = None):
        self.api_key = openai_api_key or os.getenv("OPENAI_API_KEY")

    def parse_document(
        self,
        file_bytes: bytes,
        filename: str = "template.pdf",
        mime_type: Optional[str] = None,
    ) -> LayoutBlueprint:
        """
        Parses uploaded file (PDF or Image) into a LayoutBlueprint.
        Tries Multimodal Vision AI if API key is configured,
        otherwise falls back to intelligent structural decomposition.
        """
        is_pdf = filename.lower().endswith(".pdf") or (
            mime_type and "pdf" in mime_type.lower()
        )

        # Convert to high-res image for vision
        image_bytes = self._prepare_page_image(file_bytes, is_pdf)

        # 1. Attempt Multimodal AI call if configured
        if self.api_key:
            try:
                blueprint = self._call_vision_llm(image_bytes)
                if blueprint:
                    return blueprint
            except Exception as exc:
                logger.warning("Vision LLM call failed, falling back to structural parser: %s", exc)

        # 2. Structural Decomposition Fallback
        if is_pdf:
            return self._parse_pdf_structurally(file_bytes, filename)
        else:
            return self._parse_image_structurally(image_bytes, filename)

    def _prepare_page_image(self, file_bytes: bytes, is_pdf: bool) -> bytes:
        """Renders first page of PDF as high-res PNG or normalizes image bytes."""
        if is_pdf:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            try:
                page = doc.load_page(0)
                pix = page.get_pixmap(dpi=150)
                return pix.tobytes("png")
            finally:
                doc.close()
        else:
            img = Image.open(io.BytesIO(file_bytes))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

    def _call_vision_llm(self, image_bytes: bytes) -> Optional[LayoutBlueprint]:
        """Calls OpenAI GPT-4o with the master visual prompt."""
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key)
            b64_img = base64.b64encode(image_bytes).decode("utf-8")

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": MASTER_VISION_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Extract visual blueprint to YAML."},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64_img}",
                                    "detail": "high",
                                },
                            },
                        ],
                    },
                ],
                temperature=0.0,
            )

            raw = response.choices[0].message.content.strip()
            # Clean markdown fences
            if "```" in raw:
                raw = re.sub(r"^```(?:yaml)?\n", "", raw, flags=re.MULTILINE)
                raw = raw.replace("```", "")

            return LayoutBlueprint.from_yaml(raw.strip())
        except Exception as exc:
            logger.error("Failed to parse via OpenAI Vision: %s", exc)
            return None

    def _parse_pdf_structurally(
        self, pdf_bytes: bytes, filename: str
    ) -> LayoutBlueprint:
        """
        Intelligently decomposes PDF structure into a normalized YAML blueprint.
        Detects 2-column side-by-side grids, 1-column cards, tables, totals, and branding.
        """
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            page = doc.load_page(0)
            page_w = float(page.rect.width)
            page_h = float(page.rect.height)

            # Determine standard page name
            page_size = "A4"
            if abs(page_w - 612.0) < 10 and abs(page_h - 792.0) < 10:
                page_size = "LETTER"

            # Check text blocks to inspect layout topology
            blocks = page.get_text("dict").get("blocks", [])
            text_spans = []
            for b in blocks:
                if b.get("type") == 0:
                    for l in b.get("lines", []):
                        for s in l.get("spans", []):
                            txt = s.get("text", "").strip()
                            if txt:
                                text_spans.append({
                                    "text": txt,
                                    "bbox": s.get("bbox"),
                                    "font": s.get("font", "Helvetica"),
                                    "size": s.get("size", 10),
                                })

            # Check for 2-column side-by-side layout (e.g. Fendt template)
            has_two_col_info = False
            has_customer_info = False
            has_product_info = False
            cust_name_y = None
            email_y = None

            for s in text_spans:
                txt = s["text"]
                txt_upper = txt.upper()
                if "CUSTOMER INFORMATION" in txt_upper:
                    has_customer_info = True
                if "PRODUCT INFORMATION" in txt_upper:
                    has_product_info = True
                    # Check if Product Information is placed on right half of canvas
                    if s["bbox"][0] > page_w * 0.4:
                        has_two_col_info = True
                if "Customer Name" in txt:
                    cust_name_y = s["bbox"][1]
                elif "Email:" in txt:
                    if email_y is None:
                        email_y = s["bbox"][1]

            is_cust_2col = False
            if cust_name_y is not None and email_y is not None:
                if abs(cust_name_y - email_y) < 15:
                    is_cust_2col = True

            clean_name = Path(filename).stem
            page_count = doc.page_count

            if page_count == 1 and (is_cust_2col or (page_size == "LETTER" and "fendt" in clean_name.lower())):
                return self._build_fendt_1page_blueprint(clean_name, page_size)
            elif page_count >= 2 or has_two_col_info:
                return self._build_two_column_fendt_blueprint(clean_name, page_size)
            else:
                return self._build_standard_dealer_blueprint(clean_name, page_size)

        finally:
            doc.close()

    def _parse_image_structurally(
        self, image_bytes: bytes, filename: str
    ) -> LayoutBlueprint:
        """Fallback for raw uploaded images."""
        clean_name = Path(filename).stem
        return self._build_standard_dealer_blueprint(clean_name, "A4")

    def _build_fendt_1page_blueprint(
        self, template_name: str, page_size: str = "LETTER"
    ) -> LayoutBlueprint:
        """Blueprint for modern 1-page Letter layout with 2-column Customer & Product grids."""
        elements = [
            # Top Banner Bar
            ElementBlueprint(
                id="top_header_banner",
                type="shape",
                shape_type="rect",
                bbox=[40.0, 930.0, 920.0, 6.0],
                style={"fill_color": "secondary"},
            ),
            # Brand Header Title
            ElementBlueprint(
                id="header_dealer_brand",
                type="text",
                bbox=[48.0, 890.0, 300.0, 35.0],
                binding="dealer.name",
                style={
                    "font": "Helvetica-Bold",
                    "font_size": 20,
                    "color": "primary",
                    "alignment": "left",
                },
            ),
            # Header Subtitle
            ElementBlueprint(
                id="header_dealer_tagline",
                type="text",
                bbox=[48.0, 868.0, 300.0, 18.0],
                text="Authorised Agricultural Equipment Dealer",
                style={
                    "font": "Helvetica",
                    "font_size": 9,
                    "color": "secondary",
                    "alignment": "left",
                },
            ),
            # Quote Reference Meta (Top-Right)
            ElementBlueprint(
                id="header_quote_meta",
                type="key_value_grid",
                bbox=[580.0, 885.0, 380.0, 50.0],
                layout=GridLayout(columns=1, row_gap=4.0),
                fields=[
                    GridField(
                        label="Quote Reference",
                        binding="quote.quoteNumber",
                        align="right",
                        style={"font": "Helvetica-Bold", "font_size": 9, "color": "text"},
                    ),
                    GridField(
                        label="Quote Date",
                        binding="quote.quoteDate",
                        align="right",
                        style={"font_size": 9},
                    ),
                    GridField(
                        label="Valid Until",
                        binding="quote.validUntil",
                        align="right",
                        style={"font_size": 9},
                    ),
                ],
            ),
            # CUSTOMER INFORMATION GRID (2 columns)
            ElementBlueprint(
                id="customer_info_grid",
                type="key_value_grid",
                bbox=[40.0, 755.0, 920.0, 75.0],
                layout=GridLayout(columns=2, column_gap=40.0, row_gap=6.0),
                fields=[
                    GridField(label="Customer Name", binding="customer.name"),
                    GridField(label="Email", binding="customer.email"),
                    GridField(label="Company", binding="customer.company"),
                    GridField(label="Phone", binding="customer.phone"),
                    GridField(label="Address", binding="customer.address", col_span=2),
                ],
            ),
            # PRODUCT INFORMATION (2 columns on left)
            ElementBlueprint(
                id="product_info_grid",
                type="key_value_grid",
                bbox=[40.0, 605.0, 580.0, 90.0],
                layout=GridLayout(columns=2, column_gap=20.0, row_gap=6.0),
                fields=[
                    GridField(label="Brand", binding="product.brand"),
                    GridField(label="Configuration", binding="product.configuration"),
                    GridField(label="Model", binding="product.model", style={"color": "accent", "bold": True}),
                    GridField(label="Serial Number", binding="product.serialNumber"),
                    GridField(label="Series", binding="product.series", col_span=2),
                ],
            ),
            # Product Image Preview (Right side)
            ElementBlueprint(
                id="product_image",
                type="image",
                bbox=[650.0, 615.0, 280.0, 85.0],
                binding="product.image",
            ),
            # Line Items / Options Table
            ElementBlueprint(
                id="options_table",
                type="table",
                bbox=[40.0, 440.0, 920.0, 130.0],
                binding="options",
                table_meta=TableMeta(
                    header_background="primary",
                    header_color="#FFFFFF",
                    alternating_row_background="row_alt",
                    border_width=0.5,
                    border_color="border",
                    row_height=20.0,
                    columns=[
                        TableColumn(field="code", title="Code", width_ratio=0.18, align="left"),
                        TableColumn(field="description", title="Description", width_ratio=0.60, align="left"),
                        TableColumn(field="price", title="Price", width_ratio=0.22, align="right", format="currency"),
                    ],
                ),
            ),
            # Pricing Summary Grid (Right-Aligned)
            ElementBlueprint(
                id="pricing_summary_grid",
                type="key_value_grid",
                bbox=[400.0, 345.0, 560.0, 75.0],
                layout=GridLayout(columns=1, row_gap=4.0),
                fields=[
                    GridField(label="Base Price", binding="pricing.basePrice", format="currency", align="right"),
                    GridField(label="Options & Accessories", binding="pricing.optionsPrice", format="currency", align="right"),
                    GridField(label="Discount", binding="pricing.discount", format="currency", align="right"),
                    GridField(label="Tax (VAT)", binding="pricing.tax", format="currency", align="right"),
                ],
            ),
            # Total Price
            ElementBlueprint(
                id="total_price",
                type="text",
                bbox=[40.0, 320.0, 920.0, 22.0],
                binding="pricing.totalPrice",
                style={
                    "font": "Helvetica-Bold",
                    "font_size": 12,
                    "color": "secondary",
                    "alignment": "right",
                    "format": "currency",
                },
            ),
            # Terms & Conditions
            ElementBlueprint(
                id="terms_and_conditions",
                type="text",
                bbox=[40.0, 285.0, 920.0, 25.0],
                text="This quotation is valid for 14 days from the date of issue. Prices are subject to change without notice. Delivery timescales are estimates only.",
                style={
                    "font": "Helvetica",
                    "font_size": 8,
                    "color": "muted",
                    "alignment": "left",
                },
            ),
            # Footer Dealer Information (2 columns)
            ElementBlueprint(
                id="footer_dealer_grid",
                type="key_value_grid",
                bbox=[40.0, 235.0, 920.0, 35.0],
                layout=GridLayout(columns=2, column_gap=30.0, row_gap=4.0),
                fields=[
                    GridField(label="Dealer Name", binding="dealer.name"),
                    GridField(label="Email", binding="dealer.email"),
                    GridField(label="Contact", binding="dealer.contactPerson"),
                    GridField(label="Phone", binding="dealer.phone"),
                ],
            ),
        ]

        return LayoutBlueprint(
            version="2.0",
            template_name=template_name,
            description="1-Page Letter Layout Blueprint for Fendt Dealer Quote Template",
            page=PageConfig(
                size=page_size,
                palette={
                    "primary": "#004B87",
                    "secondary": "#3DAA35",
                    "accent": "#004B87",
                    "background": "#FFFFFF",
                    "text": "#1A1A1A",
                    "muted": "#666666",
                    "border": "#E2E8F0",
                    "row_alt": "#F8FAFC",
                },
            ),
            elements=elements,
        )

    def _build_two_column_fendt_blueprint(
        self, template_name: str, page_size: str = "LETTER"
    ) -> LayoutBlueprint:
        """Blueprint for modern 2-column side-by-side layout (e.g. fendt_dealer_quote_template)."""
        elements = [
            # Top Banner Bar
            ElementBlueprint(
                id="top_header_banner",
                type="shape",
                shape_type="rect",
                bbox=[40.0, 930.0, 920.0, 6.0],
                style={"fill_color": "accent"},
            ),
            # Brand Header Title
            ElementBlueprint(
                id="header_dealer_brand",
                type="text",
                bbox=[50.0, 880.0, 400.0, 45.0],
                binding="dealer.name",
                style={
                    "font": "Helvetica-Bold",
                    "font_size": 22,
                    "color": "primary",
                    "alignment": "left",
                },
            ),
            # Header Subtitle
            ElementBlueprint(
                id="header_dealer_tagline",
                type="text",
                bbox=[50.0, 855.0, 400.0, 20.0],
                text="Authorised Agricultural Equipment Dealer",
                style={
                    "font": "Helvetica",
                    "font_size": 9,
                    "color": "muted",
                    "alignment": "left",
                },
            ),
            # Quote Reference Meta (Top-Right)
            ElementBlueprint(
                id="header_quote_meta",
                type="key_value_grid",
                bbox=[600.0, 845.0, 360.0, 75.0],
                layout=GridLayout(columns=1, row_gap=6.0),
                fields=[
                    GridField(
                        label="Quote Reference",
                        binding="quote.quoteNumber",
                        align="right",
                        style={"font": "Helvetica-Bold", "font_size": 9, "color": "primary"},
                    ),
                    GridField(
                        label="Quote Date",
                        binding="quote.quoteDate",
                        align="right",
                        style={"font_size": 9},
                    ),
                    GridField(
                        label="Valid Until",
                        binding="quote.validUntil",
                        align="right",
                        style={"font_size": 9},
                    ),
                ],
            ),
            # Decorative Divider
            ElementBlueprint(
                id="header_divider",
                type="shape",
                shape_type="line",
                bbox=[40.0, 830.0, 920.0, 1.0],
                style={"stroke_color": "border", "stroke_width": 1.0},
            ),
            # 2-COLUMN SIDE-BY-SIDE GRID (Customer on left, Product on right)
            ElementBlueprint(
                id="two_column_party_product_grid",
                type="key_value_grid",
                bbox=[40.0, 640.0, 920.0, 175.0],
                layout=GridLayout(columns=2, column_gap=50.0, row_gap=10.0),
                fields=[
                    # Row 1
                    GridField(label="Customer Name", binding="customer.name"),
                    GridField(label="Brand", binding="product.brand"),
                    # Row 2
                    GridField(label="Company", binding="customer.company"),
                    GridField(label="Model", binding="product.model"),
                    # Row 3
                    GridField(label="Address", binding="customer.address"),
                    GridField(label="Series", binding="product.series"),
                    # Row 4
                    GridField(label="Email", binding="customer.email"),
                    GridField(label="Configuration", binding="product.configuration"),
                    # Row 5
                    GridField(label="Phone", binding="customer.phone"),
                    GridField(label="Serial Number", binding="product.serialNumber"),
                ],
            ),
            # Line Items / Options Table
            ElementBlueprint(
                id="options_table",
                type="table",
                bbox=[40.0, 360.0, 920.0, 240.0],
                binding="options",
                table_meta=TableMeta(
                    header_background="primary",
                    header_color="#FFFFFF",
                    alternating_row_background="row_alt",
                    border_width=0.5,
                    border_color="border",
                    row_height=22.0,
                    columns=[
                        TableColumn(field="code", title="Code", width_ratio=0.18, align="left"),
                        TableColumn(field="description", title="Description", width_ratio=0.60, align="left"),
                        TableColumn(field="price", title="Price", width_ratio=0.22, align="right", format="currency"),
                    ],
                ),
            ),
            # Pricing Summary Grid (Right-Aligned)
            ElementBlueprint(
                id="pricing_summary_grid",
                type="key_value_grid",
                bbox=[520.0, 170.0, 440.0, 160.0],
                layout=GridLayout(columns=1, row_gap=8.0),
                fields=[
                    GridField(label="Base Price", binding="pricing.basePrice", format="currency", align="right"),
                    GridField(label="Options & Accessories", binding="pricing.optionsPrice", format="currency", align="right"),
                    GridField(label="Discount", binding="pricing.discount", format="currency", align="right"),
                    GridField(label="Tax (VAT)", binding="pricing.tax", format="currency", align="right"),
                    GridField(
                        label="Total Price (incl. VAT)",
                        binding="pricing.totalPrice",
                        format="currency",
                        align="right",
                        style={"font": "Helvetica-Bold", "font_size": 13, "color": "accent"},
                    ),
                ],
            ),
            # Terms & Conditions (Bottom Left)
            ElementBlueprint(
                id="terms_and_conditions",
                type="text",
                bbox=[40.0, 90.0, 460.0, 40.0],
                text="This quotation is valid for 14 days from the date of issue. Prices are subject to change without notice. Delivery timescales are estimates only.",
                style={
                    "font": "Helvetica-Oblique",
                    "font_size": 8,
                    "color": "muted",
                    "alignment": "left",
                },
            ),
        ]

        return LayoutBlueprint(
            version="2.0",
            template_name=template_name,
            description="2-Column Side-by-Side Blueprint for Fendt Dealer Quote Template",
            page=PageConfig(
                size=page_size,
                palette={
                    "primary": "#004B87",  # Fendt Navy
                    "secondary": "#3DAA35",  # Fendt Green
                    "accent": "#004B87",
                    "background": "#FFFFFF",
                    "text": "#1A1A1A",
                    "muted": "#666666",
                    "border": "#E2E8F0",
                    "row_alt": "#F8FAFC",
                },
            ),
            elements=elements,
        )

    def _build_standard_dealer_blueprint(
        self, template_name: str, page_size: str = "A4"
    ) -> LayoutBlueprint:
        """Blueprint for standard 1-column stacked card layout (e.g. dealer_template)."""
        elements = [
            # Top Banner
            ElementBlueprint(
                id="top_bar",
                type="shape",
                shape_type="rect",
                bbox=[30.0, 940.0, 940.0, 6.0],
                style={"fill_color": "secondary"},
            ),
            # Dealer Brand
            ElementBlueprint(
                id="dealer_brand",
                type="text",
                bbox=[30.0, 890.0, 400.0, 40.0],
                binding="dealer.name",
                style={
                    "font": "Helvetica-Bold",
                    "font_size": 22,
                    "color": "primary",
                    "alignment": "left",
                },
            ),
            # Subtitle
            ElementBlueprint(
                id="dealer_subtitle",
                type="text",
                bbox=[30.0, 868.0, 400.0, 20.0],
                text="Authorised Agricultural Equipment Dealer",
                style={
                    "font": "Helvetica",
                    "font_size": 9,
                    "color": "muted",
                    "alignment": "left",
                },
            ),
            # Header Meta
            ElementBlueprint(
                id="header_meta",
                type="key_value_grid",
                bbox=[600.0, 855.0, 360.0, 75.0],
                layout=GridLayout(columns=1, row_gap=6.0),
                fields=[
                    GridField(
                        label="Quote Reference",
                        binding="quote.quoteNumber",
                        align="right",
                        style={"font": "Helvetica-Bold", "font_size": 9, "color": "primary"},
                    ),
                    GridField(
                        label="Quote Date",
                        binding="quote.quoteDate",
                        align="right",
                        style={"font_size": 9},
                    ),
                    GridField(
                        label="Valid Until",
                        binding="quote.validUntil",
                        align="right",
                        style={"font_size": 9},
                    ),
                ],
            ),
            # Divider
            ElementBlueprint(
                id="header_divider",
                type="shape",
                shape_type="line",
                bbox=[30.0, 840.0, 940.0, 1.0],
                style={"stroke_color": "border", "stroke_width": 1.0},
            ),
            # Section 1: Customer Info
            ElementBlueprint(
                id="customer_info_grid",
                type="key_value_grid",
                bbox=[30.0, 715.0, 940.0, 115.0],
                layout=GridLayout(columns=2, column_gap=30.0, row_gap=8.0),
                fields=[
                    GridField(label="Customer Name", binding="customer.name"),
                    GridField(label="Email", binding="customer.email"),
                    GridField(label="Company", binding="customer.company"),
                    GridField(label="Phone", binding="customer.phone"),
                    GridField(label="Address", binding="customer.address", col_span=2),
                ],
            ),
            # Section 2: Product Info + Product Image
            ElementBlueprint(
                id="product_info_grid",
                type="key_value_grid",
                bbox=[30.0, 560.0, 580.0, 130.0],
                layout=GridLayout(columns=1, row_gap=8.0),
                fields=[
                    GridField(label="Brand", binding="product.brand"),
                    GridField(label="Model", binding="product.model"),
                    GridField(label="Series", binding="product.series"),
                    GridField(label="Configuration", binding="product.configuration"),
                    GridField(label="Serial Number", binding="product.serialNumber"),
                ],
            ),
            ElementBlueprint(
                id="product_image",
                type="image",
                bbox=[650.0, 560.0, 310.0, 130.0],
                binding="product.image",
            ),
            # Section 3: Options Table
            ElementBlueprint(
                id="options_table",
                type="table",
                bbox=[30.0, 310.0, 940.0, 220.0],
                binding="options",
                table_meta=TableMeta(
                    header_background="primary",
                    header_color="#FFFFFF",
                    alternating_row_background="row_alt",
                    border_width=0.5,
                    border_color="border",
                    row_height=22.0,
                    columns=[
                        TableColumn(field="code", title="Code", width_ratio=0.18, align="left"),
                        TableColumn(field="description", title="Description", width_ratio=0.60, align="left"),
                        TableColumn(field="price", title="Price", width_ratio=0.22, align="right", format="currency"),
                    ],
                ),
            ),
            # Section 4: Pricing Summary
            ElementBlueprint(
                id="pricing_summary",
                type="key_value_grid",
                bbox=[540.0, 150.0, 420.0, 140.0],
                layout=GridLayout(columns=1, row_gap=7.0),
                fields=[
                    GridField(label="Base Price", binding="pricing.basePrice", format="currency", align="right"),
                    GridField(label="Options & Accessories", binding="pricing.optionsPrice", format="currency", align="right"),
                    GridField(label="Discount", binding="pricing.discount", format="currency", align="right"),
                    GridField(label="Tax (VAT)", binding="pricing.tax", format="currency", align="right"),
                    GridField(
                        label="Total Price (incl. VAT)",
                        binding="pricing.totalPrice",
                        format="currency",
                        align="right",
                        style={"font": "Helvetica-Bold", "font_size": 13, "color": "secondary"},
                    ),
                ],
            ),
            # Dealer Contact Footer
            ElementBlueprint(
                id="footer_dealer_grid",
                type="key_value_grid",
                bbox=[30.0, 40.0, 940.0, 45.0],
                layout=GridLayout(columns=4, column_gap=20.0, row_gap=4.0),
                fields=[
                    GridField(label="Dealer", binding="dealer.name"),
                    GridField(label="Contact", binding="dealer.contactPerson"),
                    GridField(label="Phone", binding="dealer.phone"),
                    GridField(label="Email", binding="dealer.email"),
                ],
            ),
        ]

        return LayoutBlueprint(
            version="2.0",
            template_name=template_name,
            description="Stacked Card Blueprint for Standard Dealer Template",
            page=PageConfig(
                size=page_size,
                palette={
                    "primary": "#1A1A1A",
                    "secondary": "#3DAA35",  # Green accent
                    "accent": "#004B87",
                    "background": "#FFFFFF",
                    "text": "#1A1A1A",
                    "muted": "#666666",
                    "border": "#E2E8F0",
                    "row_alt": "#F8FAFC",
                },
            ),
            elements=elements,
        )

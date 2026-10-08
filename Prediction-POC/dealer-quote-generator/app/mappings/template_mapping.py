"""
template_mapping.py — Python-based fallback field mapping.

This mirrors the YAML configuration for programmatic access.
It is NOT used by default (the YAML loader is preferred).
Use this as a reference, or when you need to construct a mapping
dynamically in code (e.g. for testing or AI-generated mappings).

To use:
    from app.mappings.template_mapping import FIELD_MAPPING, OPTIONS_TABLE, PRODUCT_IMAGE

Then pass them to the services if you bypass the YAML loader.
"""

from typing import Any, Dict

# ── Standard field mapping ─────────────────────────────────────────────────────
# Keys match the TemplateConfig.fields dict.
# All coordinates use ReportLab convention: origin = bottom-left.

FIELD_MAPPING: Dict[str, Dict[str, Any]] = {
    # Quote info
    "quote_number": {"source": "quote.quoteNumber", "page": 1, "x": 420, "y": 750, "font_size": 9},
    "quote_date":   {"source": "quote.quoteDate",   "page": 1, "x": 420, "y": 735, "font_size": 9},
    "quote_valid":  {"source": "quote.validUntil",  "page": 1, "x": 420, "y": 720, "font_size": 9},

    # Customer
    "customer_name":    {"source": "customer.name",    "page": 1, "x": 50, "y": 660, "font_size": 11, "bold": True},
    "customer_company": {"source": "customer.company", "page": 1, "x": 50, "y": 645, "font_size": 10},
    "customer_address": {"source": "customer.address", "page": 1, "x": 50, "y": 630, "font_size": 10},
    "customer_email":   {"source": "customer.email",   "page": 1, "x": 50, "y": 615, "font_size": 10},
    "customer_phone":   {"source": "customer.phone",   "page": 1, "x": 50, "y": 600, "font_size": 10},

    # Product
    "product_brand":         {"source": "product.brand",         "page": 1, "x": 50, "y": 530, "font_size": 11, "bold": True},
    "product_model":         {"source": "product.model",         "page": 1, "x": 50, "y": 515, "font_size": 12, "bold": True, "color": [0.0, 0.3, 0.6]},
    "product_series":        {"source": "product.series",        "page": 1, "x": 50, "y": 500, "font_size": 10},
    "product_configuration": {"source": "product.configuration", "page": 1, "x": 50, "y": 485, "font_size": 10},
    "product_serial":        {"source": "product.serialNumber",  "page": 1, "x": 50, "y": 470, "font_size": 9},

    # Pricing
    "pricing_base":     {"source": "pricing.basePrice",    "page": 1, "x": 450, "y": 370, "font_size": 10, "format": "currency"},
    "pricing_options":  {"source": "pricing.optionsPrice", "page": 1, "x": 450, "y": 355, "font_size": 10, "format": "currency"},
    "pricing_discount": {"source": "pricing.discount",     "page": 1, "x": 450, "y": 340, "font_size": 10, "format": "currency", "color": [0.7, 0.0, 0.0]},
    "pricing_tax":      {"source": "pricing.tax",          "page": 1, "x": 450, "y": 325, "font_size": 10, "format": "currency"},
    "pricing_total":    {"source": "pricing.totalPrice",   "page": 1, "x": 450, "y": 300, "font_size": 13, "bold": True, "format": "currency", "color": [0.0, 0.3, 0.6]},

    # Dealer
    "dealer_name":    {"source": "dealer.name",          "page": 1, "x": 50, "y": 80, "font_size": 10, "bold": True},
    "dealer_contact": {"source": "dealer.contactPerson", "page": 1, "x": 50, "y": 67, "font_size": 9},
    "dealer_phone":   {"source": "dealer.phone",         "page": 1, "x": 50, "y": 54, "font_size": 9},
    "dealer_email":   {"source": "dealer.email",         "page": 1, "x": 50, "y": 41, "font_size": 9},
}

# ── Options table config ───────────────────────────────────────────────────────

OPTIONS_TABLE: Dict[str, Any] = {
    "page": 1,
    "start_x": 50,
    "start_y": 420,
    "row_height": 18,
    "max_rows": 10,
    "columns": {
        "code":        {"label": "Code",        "x_offset": 0,   "width": 80,  "font_size": 9},
        "description": {"label": "Description", "x_offset": 85,  "width": 200, "font_size": 9},
        "price":       {"label": "Price",       "x_offset": 295, "width": 100, "font_size": 9, "format": "currency", "align": "right"},
    },
    "header": {"font_size": 9, "bold": True, "color": [1, 1, 1], "background": [0.0, 0.3, 0.6]},
}

# ── Product image config ───────────────────────────────────────────────────────

PRODUCT_IMAGE: Dict[str, Any] = {
    "source": "product.image",
    "page": 1,
    "x": 310,
    "y": 460,
    "width": 240,
    "height": 160,
}

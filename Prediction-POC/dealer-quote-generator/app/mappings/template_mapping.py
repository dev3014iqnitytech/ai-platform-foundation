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
    # Quote info (top dark header bar)
    "quote_number":      {"source": "quote.quoteNumber", "page": 1, "x": 478, "y": 802, "font_size": 9, "color": [1.0, 1.0, 1.0], "max_width": 85},
    "quote_date":        {"source": "quote.quoteDate",   "page": 1, "x": 478, "y": 789, "font_size": 9, "color": [1.0, 1.0, 1.0], "max_width": 85},
    "quote_valid_until": {"source": "quote.validUntil",  "page": 1, "x": 478, "y": 776, "font_size": 9, "color": [1.0, 1.0, 1.0], "max_width": 85},

    # Customer Information
    "customer_name":    {"source": "customer.name",    "page": 1, "x": 142, "y": 707, "font_size": 10, "bold": True, "max_width": 236},
    "customer_company": {"source": "customer.company", "page": 1, "x": 142, "y": 691, "font_size": 10, "max_width": 236},
    "customer_address": {"source": "customer.address", "page": 1, "x": 142, "y": 675, "font_size": 10, "max_width": 236},
    "customer_email":   {"source": "customer.email",   "page": 1, "x": 142, "y": 659, "font_size": 10, "max_width": 236},
    "customer_phone":   {"source": "customer.phone",   "page": 1, "x": 142, "y": 643, "font_size": 10, "max_width": 236},

    # Product Information
    "product_brand":         {"source": "product.brand",         "page": 1, "x": 142, "y": 552, "font_size": 10, "bold": True, "max_width": 150},
    "product_model":         {"source": "product.model",         "page": 1, "x": 142, "y": 536, "font_size": 11, "bold": True, "color": [0.0, 0.3, 0.6], "max_width": 150},
    "product_series":        {"source": "product.series",        "page": 1, "x": 142, "y": 520, "font_size": 10, "max_width": 150},
    "product_configuration": {"source": "product.configuration", "page": 1, "x": 142, "y": 504, "font_size": 10, "max_width": 150},
    "product_serial":        {"source": "product.serialNumber",  "page": 1, "x": 142, "y": 488, "font_size": 9, "color": [0.4, 0.4, 0.4], "max_width": 150},

    # Pricing Summary
    "pricing_base":     {"source": "pricing.basePrice",    "page": 1, "x": 565, "y": 220, "font_size": 9, "format": "currency", "align": "right"},
    "pricing_options":  {"source": "pricing.optionsPrice", "page": 1, "x": 565, "y": 205, "font_size": 9, "format": "currency", "align": "right"},
    "pricing_discount": {"source": "pricing.discount",     "page": 1, "x": 565, "y": 190, "font_size": 9, "format": "currency", "color": [0.7, 0.0, 0.0], "align": "right"},
    "pricing_tax":      {"source": "pricing.tax",          "page": 1, "x": 565, "y": 175, "font_size": 9, "format": "currency", "align": "right"},
    "pricing_total":    {"source": "pricing.totalPrice",   "page": 1, "x": 565, "y": 156, "font_size": 12, "bold": True, "format": "currency", "color": [1.0, 1.0, 1.0], "align": "right"},

    # Dealer Information (Footer)
    "dealer_name":    {"source": "dealer.name",          "page": 1, "x": 102, "y": 35, "font_size": 9, "bold": True, "color": [1.0, 1.0, 1.0], "max_width": 175},
    "dealer_contact": {"source": "dealer.contactPerson", "page": 1, "x": 82,  "y": 21, "font_size": 9, "color": [1.0, 1.0, 1.0], "max_width": 195},
    "dealer_phone":   {"source": "dealer.phone",         "page": 1, "x": 77,  "y": 8,  "font_size": 9, "color": [1.0, 1.0, 1.0], "max_width": 200},
    "dealer_email":   {"source": "dealer.email",         "page": 1, "x": 337, "y": 35, "font_size": 9, "color": [1.0, 1.0, 1.0], "max_width": 225},
}

# ── Options table config ───────────────────────────────────────────────────────

OPTIONS_TABLE: Dict[str, Any] = {
    "page": 1,
    "start_x": 33,
    "start_y": 364,
    "row_height": 18,
    "max_rows": 4,
    "columns": {
        "code":        {"label": "Code",        "x_offset": 0,   "width": 70,  "font_size": 9, "align": "left"},
        "description": {"label": "Description", "x_offset": 80,  "width": 340, "font_size": 9, "align": "left"},
        "price":       {"label": "Price",       "x_offset": 432, "width": 100, "font_size": 9, "format": "currency", "align": "right"},
    },
    "header": {"draw": False, "font_size": 9, "bold": True, "color": [1, 1, 1], "background": [0.0, 0.3, 0.6]},
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

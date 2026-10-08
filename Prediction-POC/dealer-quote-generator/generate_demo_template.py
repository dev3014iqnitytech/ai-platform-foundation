"""
generate_demo_template.py
─────────────────────────
Standalone script that generates a realistic dealer PDF template using
ReportLab. Run this ONCE to create templates/dealer_template.pdf.

Usage:
    python generate_demo_template.py

The generated PDF is a NON-fillable static PDF so the overlay strategy
is demonstrated in the MVP.
"""

from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_PATH = TEMPLATES_DIR / "dealer_template.pdf"
TEMPLATES_DIR.mkdir(exist_ok=True)

# ── Brand colours (Fendt-inspired) ────────────────────────────────────────────
BRAND_GREEN = colors.HexColor("#3DAA35")
BRAND_DARK = colors.HexColor("#1A1A1A")
BRAND_GREY = colors.HexColor("#666666")
BRAND_LIGHT = colors.HexColor("#F5F5F5")
BRAND_BLUE = colors.HexColor("#004B87")
WHITE = colors.white

W, H = A4  # 595 x 842 points

# ── Helper ─────────────────────────────────────────────────────────────────────


def draw_line(c: canvas.Canvas, x1, y1, x2, y2, color=BRAND_GREY, width=0.5):
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(x1, y1, x2, y2)


def draw_rect_filled(c: canvas.Canvas, x, y, w, h, fill_color, stroke_color=None):
    c.setFillColor(fill_color)
    if stroke_color:
        c.setStrokeColor(stroke_color)
    else:
        c.setStrokeColor(fill_color)
    c.rect(x, y, w, h, fill=1, stroke=0)


def text(c: canvas.Canvas, x, y, txt, font="Helvetica", size=10, color=BRAND_DARK):
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawString(x, y, txt)


def text_right(c: canvas.Canvas, x, y, txt, font="Helvetica", size=10, color=BRAND_DARK):
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawRightString(x, y, txt)


# ── Page builder ───────────────────────────────────────────────────────────────


def build_page(c: canvas.Canvas) -> None:
    """Draw the complete dealer template on the current page."""

    # ── TOP HEADER BAR ───────────────────────────────────────────────
    draw_rect_filled(c, 0, H - 90, W, 90, BRAND_DARK)

    # Dealer name / logo area
    c.setFont("Helvetica-Bold", 22)
    c.setFillColor(WHITE)
    c.drawString(30, H - 55, "FENDT DEALER")
    c.setFont("Helvetica", 10)
    c.setFillColor(BRAND_GREEN)
    c.drawString(30, H - 72, "Authorised Agricultural Equipment Dealer")

    # Right side header info
    c.setFont("Helvetica", 9)
    c.setFillColor(WHITE)
    c.drawRightString(W - 30, H - 40, "Quote Reference:  __________________")
    c.drawRightString(W - 30, H - 53, "Quote Date:           __________________")
    c.drawRightString(W - 30, H - 66, "Valid Until:              __________________")

    # ── GREEN ACCENT LINE ────────────────────────────────────────────
    draw_rect_filled(c, 0, H - 95, W, 5, BRAND_GREEN)

    # ── CUSTOMER INFORMATION SECTION ─────────────────────────────────
    y = H - 115
    draw_rect_filled(c, 28, y - 2, W - 56, 18, BRAND_BLUE)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(WHITE)
    c.drawString(33, y + 3, "CUSTOMER INFORMATION")

    y -= 20
    labels_left = [
        ("Customer Name:", y),
        ("Company:", y - 16),
        ("Address:", y - 32),
        ("Email:", y - 48),
        ("Phone:", y - 64),
    ]
    for label, ly in labels_left:
        text(c, 30, ly, label, "Helvetica-Bold", 9, BRAND_GREY)
        draw_line(c, 140, ly - 2, 380, ly - 2, BRAND_GREY, 0.3)

    # ── PRODUCT INFORMATION SECTION ───────────────────────────────────
    y = H - 270
    draw_rect_filled(c, 28, y - 2, W - 56, 18, BRAND_BLUE)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(WHITE)
    c.drawString(33, y + 3, "PRODUCT INFORMATION")

    y -= 20
    labels_prod = [
        ("Brand:", y),
        ("Model:", y - 16),
        ("Series:", y - 32),
        ("Configuration:", y - 48),
        ("Serial Number:", y - 64),
    ]
    for label, ly in labels_prod:
        text(c, 30, ly, label, "Helvetica-Bold", 9, BRAND_GREY)
        draw_line(c, 140, ly - 2, 295, ly - 2, BRAND_GREY, 0.3)

    # ── PRODUCT IMAGE PLACEHOLDER ─────────────────────────────────────
    img_x, img_y, img_w, img_h = 310, 460, 240, 160
    draw_rect_filled(c, img_x, img_y, img_w, img_h, BRAND_LIGHT, BRAND_GREY)
    c.setStrokeColor(BRAND_GREY)
    c.setLineWidth(0.5)
    c.rect(img_x, img_y, img_w, img_h, stroke=1, fill=0)
    text(c, img_x + 70, img_y + img_h / 2, "[ Product Image ]", "Helvetica", 10, BRAND_GREY)

    # ── OPTIONS TABLE ─────────────────────────────────────────────────
    y = H - 440
    draw_rect_filled(c, 28, y - 2, W - 56, 18, BRAND_BLUE)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(WHITE)
    c.drawString(33, y + 3, "SELECTED OPTIONS & ACCESSORIES")

    # Table header row
    y -= 22
    draw_rect_filled(c, 28, y - 2, W - 56, 16, BRAND_LIGHT)
    text(c, 33, y + 2, "Code", "Helvetica-Bold", 9, BRAND_DARK)
    text(c, 113, y + 2, "Description", "Helvetica-Bold", 9, BRAND_DARK)
    text_right(c, W - 30, y + 2, "Price", "Helvetica-Bold", 9, BRAND_DARK)
    draw_line(c, 28, y - 2, W - 28, y - 2, BRAND_GREY, 0.3)

    # Empty option rows (placeholder lines for overlay)
    for i in range(4):
        ry = y - 20 - (i * 18)
        draw_line(c, 28, ry, W - 28, ry, BRAND_LIGHT, 0.3)
        if i == 3:
            # Last row dotted to indicate "more possible"
            c.setDash(2, 3)
        c.setDash()

    # ── PRICING SUMMARY ───────────────────────────────────────────────
    y = H - 600
    draw_rect_filled(c, 28, y - 2, W - 56, 18, BRAND_BLUE)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(WHITE)
    c.drawString(33, y + 3, "PRICING SUMMARY")

    y -= 22
    pricing_rows = [
        ("Base Price:", y),
        ("Options & Accessories:", y - 15),
        ("Discount:", y - 30),
        ("Tax (VAT):", y - 45),
    ]
    for label, py in pricing_rows:
        text(c, 300, py, label, "Helvetica", 9, BRAND_GREY)
        draw_line(c, 430, py - 2, W - 30, py - 2, BRAND_GREY, 0.3)

    # Total price row (prominent)
    ty = y - 68
    draw_rect_filled(c, 28, ty - 4, W - 56, 22, BRAND_GREEN)
    text(c, 33, ty + 4, "TOTAL PRICE (incl. VAT):", "Helvetica-Bold", 11, WHITE)
    draw_line(c, 430, ty - 2, W - 30, ty - 2, WHITE, 0.3)

    # ── TERMS ─────────────────────────────────────────────────────────
    y = H - 730
    draw_line(c, 28, y, W - 28, y, BRAND_GREY, 0.3)
    text(c, 30, y - 14, "Terms & Conditions", "Helvetica-Bold", 8, BRAND_GREY)
    terms = (
        "This quotation is valid for 14 days from the date of issue. "
        "Prices are subject to change without notice. "
        "Delivery timescales are estimates only."
    )
    c.setFont("Helvetica", 7)
    c.setFillColor(BRAND_GREY)
    c.drawString(30, y - 25, terms)

    # ── FOOTER ────────────────────────────────────────────────────────
    draw_rect_filled(c, 0, 0, W, 55, BRAND_DARK)
    text(c, 30, 35, "Dealer Name:", "Helvetica-Bold", 8, WHITE)
    draw_line(c, 100, 33, 280, 33, BRAND_GREY, 0.3)
    text(c, 30, 21, "Contact:", "Helvetica-Bold", 8, WHITE)
    draw_line(c, 80, 19, 280, 19, BRAND_GREY, 0.3)
    text(c, 30, 8, "Phone:", "Helvetica-Bold", 8, WHITE)
    draw_line(c, 75, 6, 280, 6, BRAND_GREY, 0.3)

    text(c, 300, 35, "Email:", "Helvetica-Bold", 8, WHITE)
    draw_line(c, 335, 33, W - 30, 33, BRAND_GREY, 0.3)

    # Footer accent line
    draw_rect_filled(c, 0, 55, W, 3, BRAND_GREEN)


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    c = canvas.Canvas(str(OUTPUT_PATH), pagesize=A4)
    c.setTitle("Fendt Dealer Quote Template")
    c.setAuthor("Dealer Quote Generator")
    c.setSubject("Agricultural Equipment Quote")
    build_page(c)
    c.save()
    print(f"✅ Template created: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

"""
PDF Overlay — handles **non-fillable** (static) PDFs.

Strategy (3-layer merge):
  1. Keep the original dealer PDF exactly as-is (background).
  2. Build a transparent overlay page using ReportLab for each page
     that needs dynamic content.
  3. Merge each overlay onto the corresponding background page with
     PyMuPDF's show_pdf_page() so the original design is never touched.

Coordinate system note
-----------------------
YAML mapping uses ReportLab convention: origin bottom-left, y increases up.
PyMuPDF uses: origin top-left, y increases down.

Conversion: fitz_y = page_height - rl_y
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

from app.services.template_service import (
    FieldConfig,
    OptionsTableConfig,
    ProductImageConfig,
    TemplateConfig,
)
from app.utils.pdf_utils import format_value, resolve_asset_path, resolve_font

logger = logging.getLogger(__name__)


class PDFOverlay:
    """Overlays dynamic quote data on a static (non-fillable) dealer PDF."""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root

    # ── Public ────────────────────────────────────────────────────────

    def overlay(
        self,
        pdf_bytes: bytes,
        quote_data: Dict[str, Any],
        config: TemplateConfig,
    ) -> bytes:
        """
        Return the final dealer PDF with quote data overlaid.
        """
        currency = self._get_nested(quote_data, "pricing.currency") or "EUR"

        # Group fields by page (1-indexed)
        fields_by_page = self._group_fields_by_page(config, quote_data, currency)

        # Open the original PDF
        background_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        try:
            # Clean template underscore guides from background doc
            self._clean_template_underscores(background_doc)

            for page_idx in range(background_doc.page_count):
                page_number = page_idx + 1  # 1-indexed
                page = background_doc.load_page(page_idx)
                page_w = float(page.rect.width)
                page_h = float(page.rect.height)

                # Collect items for this page
                text_items = fields_by_page.get(page_number, [])

                # Options table (if on this page)
                table_cfg = config.options_table
                table_items: List[Dict] = []
                if table_cfg and table_cfg.page == page_number:
                    options = quote_data.get("options", [])
                    table_items = self._build_table_items(table_cfg, options, currency)

                # Product image (if on this page)
                img_cfg = config.product_image
                img_path: Optional[Path] = None
                if img_cfg and img_cfg.page == page_number:
                    raw_path = self._get_nested(quote_data, img_cfg.source)
                    if raw_path:
                        img_path = resolve_asset_path(str(raw_path), self._root)

                if not text_items and not table_items and img_path is None:
                    continue  # Nothing to overlay on this page

                # Build ReportLab overlay for this page
                overlay_bytes = self._build_overlay_page(
                    page_w, page_h, text_items, table_items, img_cfg, img_path
                )

                # Merge overlay onto background page
                overlay_doc = fitz.open(stream=overlay_bytes, filetype="pdf")
                overlay_page = overlay_doc.load_page(0)
                page.show_pdf_page(page.rect, overlay_doc, 0)
                overlay_doc.close()

            # Serialise
            buf = io.BytesIO()
            background_doc.save(buf, deflate=True, garbage=4)
            return buf.getvalue()

        finally:
            background_doc.close()

    # ── ReportLab overlay builder ─────────────────────────────────────

    def _build_overlay_page(
        self,
        width: float,
        height: float,
        text_items: List[Dict],
        table_items: List[Dict],
        img_cfg: Optional[ProductImageConfig],
        img_path: Optional[Path],
    ) -> bytes:
        """Build a single-page ReportLab PDF with transparent background."""
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(width, height))
        # Set page size explicitly in points
        c.setPageSize((width, height))

        # ── Draw text fields ──────────────────────────────────────
        for item in text_items:
            self._draw_text(c, item)

        # ── Draw options table ────────────────────────────────────
        for row_item in table_items:
            self._draw_text(c, row_item)

        # ── Draw product image ────────────────────────────────────
        if img_cfg and img_path and img_path.is_file():
            self._draw_image(c, img_cfg, img_path)

        c.save()
        return buf.getvalue()

    def _draw_text(self, c: rl_canvas.Canvas, item: Dict) -> None:
        """Draw a single text item onto the ReportLab canvas."""
        x = item["x"]
        y = item["y"]
        text_str = str(item["text"])
        font_name = item.get("font", "Helvetica")
        font_size = item.get("font_size", 10)
        color_rgb = item.get("color", [0, 0, 0])
        align = item.get("align", "left")
        max_width = item.get("max_width")

        try:
            r, g, b = float(color_rgb[0]), float(color_rgb[1]), float(color_rgb[2])
            c.setFillColorRGB(r, g, b)
        except Exception:
            c.setFillColorRGB(0, 0, 0)

        try:
            c.setFont(font_name, font_size)
        except Exception:
            font_name = "Helvetica"
            c.setFont(font_name, font_size)

        # Truncate text if max_width is specified and text exceeds available space
        if max_width and max_width > 0:
            current_w = stringWidth(text_str, font_name, font_size)
            if current_w > max_width:
                ellipsis = "..."
                while text_str and stringWidth(text_str + ellipsis, font_name, font_size) > max_width:
                    text_str = text_str[:-1]
                text_str = text_str + ellipsis

        if align == "right":
            c.drawRightString(x, y, text_str)
        elif align == "center":
            c.drawCentredString(x, y, text_str)
        else:
            c.drawString(x, y, text_str)

    def _draw_image(
        self,
        c: rl_canvas.Canvas,
        img_cfg: ProductImageConfig,
        img_path: Path,
    ) -> None:
        """Place a product image at the configured position."""
        try:
            c.drawImage(
                str(img_path),
                img_cfg.x,
                img_cfg.y,
                width=img_cfg.width,
                height=img_cfg.height,
                preserveAspectRatio=True,
                mask="auto",
            )
            logger.debug("Placed image %s at (%s, %s)", img_path.name, img_cfg.x, img_cfg.y)
        except Exception as exc:
            logger.warning("Could not place product image: %s", exc)

    # ── Data preparation ──────────────────────────────────────────────

    def _group_fields_by_page(
        self,
        config: TemplateConfig,
        quote_data: Dict[str, Any],
        currency: str,
    ) -> Dict[int, List[Dict]]:
        """Resolve all field values and group them by page number."""
        by_page: Dict[int, List[Dict]] = {}
        for _key, field_cfg in config.fields.items():
            raw_value = self._get_nested(quote_data, field_cfg.source)
            if raw_value is None:
                logger.debug("Field '%s' has no value in quote data — skipping", _key)
                continue

            display = format_value(raw_value, field_cfg.format, currency)
            font_name = resolve_font(field_cfg.font, field_cfg.bold)

            item = {
                "x": field_cfg.x,
                "y": field_cfg.y,
                "text": display,
                "font": font_name,
                "font_size": field_cfg.font_size,
                "color": field_cfg.color,
                "align": getattr(field_cfg, "align", "left"),
                "max_width": getattr(field_cfg, "max_width", None),
            }
            by_page.setdefault(field_cfg.page, []).append(item)
        return by_page

    def _build_table_items(
        self,
        table_cfg: OptionsTableConfig,
        options: List[Dict],
        currency: str,
    ) -> List[Dict]:
        """Build text items for the options table rows."""
        items: List[Dict] = []
        col_cfgs = table_cfg.columns

        # Header row (only if explicitly enabled to draw)
        if getattr(table_cfg.header, "draw", False):
            header_y = table_cfg.start_y + table_cfg.row_height
            h_cfg = table_cfg.header
            for col_key, col in col_cfgs.items():
                x = table_cfg.start_x + col.x_offset
                if col.align == "right":
                    x += col.width
                items.append({
                    "x": x,
                    "y": header_y,
                    "text": col.label,
                    "font": "Helvetica-Bold" if h_cfg.bold else "Helvetica",
                    "font_size": h_cfg.font_size,
                    "color": h_cfg.color,
                    "align": col.align,
                    "max_width": col.width if col.width > 0 else None,
                })

        # Data rows
        if len(options) > table_cfg.max_rows:
            logger.warning(
                "Options count (%d) exceeds max_rows (%d). "
                "Truncating — MVP limitation.",
                len(options), table_cfg.max_rows,
            )

        visible_options = options[: table_cfg.max_rows]
        for row_idx, opt in enumerate(visible_options):
            row_y = table_cfg.start_y - (row_idx * table_cfg.row_height)
            for col_key, col in col_cfgs.items():
                raw_val = opt.get(col_key)
                if raw_val is None:
                    continue
                display = format_value(raw_val, col.format, currency)
                x = table_cfg.start_x + col.x_offset
                if col.align == "right":
                    x += col.width
                items.append({
                    "x": x,
                    "y": row_y,
                    "text": display,
                    "font": "Helvetica",
                    "font_size": col.font_size,
                    "color": [0, 0, 0],
                    "align": col.align,
                    "max_width": col.width if col.width > 0 else None,
                })

        return items

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _clean_template_underscores(doc: fitz.Document) -> None:
        """
        Removes ugly printed text underscores (e.g. '______') used as blank field
        guides in static templates so they don't strike through overlaid text or
        wrap awkwardly onto subsequent lines.
        """
        for page in doc:
            has_redactions = False
            for b in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", []):
                if b.get("type") == 0:
                    for l in b.get("lines", []):
                        for s in l.get("spans", []):
                            t = s.get("text", "").strip()
                            if t and (all(c == "_" for c in t) or t.startswith("____")):
                                page.add_redact_annot(fitz.Rect(s["bbox"]), fill=False)
                                has_redactions = True
            if has_redactions:
                page.apply_redactions()

    @staticmethod
    def _get_nested(data: Dict[str, Any], dotpath: str) -> Optional[Any]:
        keys = dotpath.split(".")
        current: Any = data
        for k in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(k)
        return current


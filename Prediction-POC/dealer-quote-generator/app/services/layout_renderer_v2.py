"""
Layout Renderer Engine (v2)
Renders arbitrary business JSON onto canvas driven strictly by LayoutBlueprint YAML.
Supports standalone dynamic rendering and transparent template overlay rendering.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import fitz  # PyMuPDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter, legal
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Table, TableStyle

from app.models.layout_blueprint_v2 import (
    ElementBlueprint,
    LayoutBlueprint,
)
from app.utils.pdf_utils import resolve_asset_path, resolve_font

logger = logging.getLogger(__name__)


class LayoutRendererV2:
    """Dynamic PDF renderer interpreting LayoutBlueprint YAML."""

    PAGE_SIZES = {
        "A4": A4,
        "LETTER": letter,
        "LEGAL": legal,
    }

    def __init__(
        self,
        blueprint: LayoutBlueprint,
        data: Dict[str, Any],
        project_root: Optional[Path] = None,
    ):
        self.blueprint = blueprint
        self.data = data
        self.project_root = project_root or Path(__file__).resolve().parent.parent.parent
        self.palette = blueprint.page.palette

        # Determine canvas dimensions
        size_key = (blueprint.page.size or "A4").upper()
        base_size = self.PAGE_SIZES.get(size_key, A4)
        if blueprint.page.orientation == "landscape":
            self.page_width, self.page_height = base_size[1], base_size[0]
        else:
            self.page_width, self.page_height = base_size[0], base_size[1]

    # ── Public Methods ────────────────────────────────────────────────

    def render(
        self,
        background_pdf_bytes: Optional[bytes] = None,
        mode: str = "auto",
    ) -> bytes:
        """
        Renders the document.
        - "standalone": Renders directly from the YAML blueprint canvas.
        - "overlay": Overlays dynamic fields on top of background_pdf_bytes.
        - "auto": Overlays if background_pdf_bytes is present, else standalone.
        """
        if (mode == "overlay" or (mode == "auto" and background_pdf_bytes)) and background_pdf_bytes:
            return self.render_overlay(background_pdf_bytes)
        return self.render_standalone()

    def render_standalone(self) -> bytes:
        """Draws the entire visual layout directly onto a new PDF document."""
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(self.page_width, self.page_height))

        # Separate elements by page (1-indexed)
        elements_by_page = self._group_elements_by_page()
        max_page = max(elements_by_page.keys(), default=1)

        for p_num in range(1, max_page + 1):
            c.setPageSize((self.page_width, self.page_height))
            page_elements = elements_by_page.get(p_num, [])

            for el in page_elements:
                self._draw_element(c, el)

            c.showPage()

        c.save()
        buf.seek(0)
        return buf.getvalue()

    def render_overlay(self, background_pdf_bytes: bytes) -> bytes:
        """Draws dynamic text/values/tables as a transparent overlay on the background PDF."""
        bg_doc = fitz.open(stream=background_pdf_bytes, filetype="pdf")
        elements_by_page = self._group_elements_by_page()

        try:
            for page_idx in range(bg_doc.page_count):
                page_num = page_idx + 1
                bg_page = bg_doc.load_page(page_idx)
                pw, ph = float(bg_page.rect.width), float(bg_page.rect.height)

                page_elements = elements_by_page.get(page_num, [])
                if not page_elements:
                    continue

                # Generate transparent overlay for this page
                overlay_buf = io.BytesIO()
                c = rl_canvas.Canvas(overlay_buf, pagesize=(pw, ph))

                # Temporarily adapt dimensions to page
                orig_w, orig_h = self.page_width, self.page_height
                self.page_width, self.page_height = pw, ph

                for el in page_elements:
                    # In overlay mode, skip background/decorative static shapes
                    if el.type == "shape" and not el.binding:
                        continue
                    self._draw_element(c, el)

                self.page_width, self.page_height = orig_w, orig_h

                c.showPage()
                c.save()
                overlay_buf.seek(0)

                overlay_doc = fitz.open(stream=overlay_buf.getvalue(), filetype="pdf")
                bg_page.show_pdf_page(bg_page.rect, overlay_doc, 0)
                overlay_doc.close()

            out_buf = io.BytesIO()
            bg_doc.save(out_buf, deflate=True, garbage=4)
            return out_buf.getvalue()

        finally:
            bg_doc.close()

    # ── Drawing Dispatcher ────────────────────────────────────────────

    def _draw_element(self, c: rl_canvas.Canvas, el: ElementBlueprint) -> None:
        x, y, w, h = self._bbox_to_canvas(el.bbox)

        if el.type == "shape":
            self._draw_shape(c, el, x, y, w, h)
        elif el.type == "text":
            self._draw_text(c, el, x, y, w, h)
        elif el.type == "key_value_grid":
            self._draw_key_value_grid(c, el, x, y, w, h)
        elif el.type == "table":
            self._draw_table(c, el, x, y, w, h)
        elif el.type == "image":
            self._draw_image(c, el, x, y, w, h)

    def _draw_shape(
        self, c: rl_canvas.Canvas, el: ElementBlueprint, x: float, y: float, w: float, h: float
    ) -> None:
        style = el.style or {}
        fill = self._resolve_color(style.get("fill_color"))
        stroke = self._resolve_color(style.get("stroke_color"))
        stroke_width = float(style.get("stroke_width", 1.0))

        c.saveState()
        c.setFillColor(fill)
        c.setStrokeColor(stroke)
        c.setLineWidth(stroke_width)

        if el.shape_type == "line":
            c.line(x, y, x + w, y + h)
        else:  # rect
            has_fill = bool(style.get("fill_color"))
            has_stroke = bool(style.get("stroke_color"))
            c.rect(x, y, w, h, fill=1 if has_fill else 0, stroke=1 if has_stroke else 0)

        c.restoreState()

    def _draw_text(
        self, c: rl_canvas.Canvas, el: ElementBlueprint, x: float, y: float, w: float, h: float
    ) -> None:
        raw_val = self._resolve_binding(el.binding) if el.binding else (el.text or "")
        formatted_val = self._format_value(raw_val, (el.style or {}).get("format"))
        if not formatted_val:
            return

        style = el.style or {}
        font_name = resolve_font(style.get("font", "Helvetica"), style.get("bold", False))
        font_size = float(style.get("font_size", 10.0))
        color = self._resolve_color(style.get("color", "text"))
        alignment = style.get("alignment", "left").lower()

        c.saveState()
        c.setFont(font_name, font_size)
        c.setFillColor(color)

        lines = str(formatted_val).split("\n")
        line_height = font_size * 1.25
        curr_y = y + h - font_size

        for line in lines:
            if alignment == "right":
                c.drawRightString(x + w, curr_y, line)
            elif alignment == "center":
                c.drawCentredString(x + (w / 2.0), curr_y, line)
            else:
                c.drawString(x, curr_y, line)
            curr_y -= line_height

        c.restoreState()

    def _draw_key_value_grid(
        self, c: rl_canvas.Canvas, el: ElementBlueprint, x: float, y: float, w: float, h: float
    ) -> None:
        fields = el.fields or []
        if not fields:
            return

        layout = el.layout
        num_cols = layout.columns if layout else 1
        col_gap = self._scale_x(layout.column_gap if layout else 20.0)
        row_gap = self._scale_y(layout.row_gap if layout else 8.0)

        col_width = (w - (col_gap * (num_cols - 1))) / num_cols
        label_size = 8.0
        val_size = 10.0
        field_row_height = label_size + val_size + 4.0 + row_gap

        c.saveState()

        for idx, f in enumerate(fields):
            col_idx = idx % num_cols
            row_idx = idx // num_cols

            field_x = x + (col_idx * (col_width + col_gap))
            field_y = (y + h) - ((row_idx + 1) * field_row_height)

            label = f.label or ""
            raw_val = self._resolve_binding(f.binding)
            val = self._format_value(raw_val, f.format)
            field_align = f.align.lower()

            # Label (secondary / muted style)
            c.setFont("Helvetica-Bold", label_size)
            c.setFillColor(self._resolve_color("muted"))
            if field_align == "right":
                c.drawRightString(field_x + col_width, field_y + val_size + 2.0, label.upper())
            else:
                c.drawString(field_x, field_y + val_size + 2.0, label.upper())

            # Value
            custom_style = f.style or {}
            v_font = resolve_font(
                custom_style.get("font", "Helvetica"), custom_style.get("bold", False)
            )
            v_size = float(custom_style.get("font_size", val_size))
            v_color = self._resolve_color(custom_style.get("color", "text"))

            c.setFont(v_font, v_size)
            c.setFillColor(v_color)

            # Value string (supports multi-line e.g. address)
            lines = str(val).split("\n")
            line_offset = 0.0
            for line in lines:
                if field_align == "right":
                    c.drawRightString(field_x + col_width, field_y - line_offset, line)
                else:
                    c.drawString(field_x, field_y - line_offset, line)
                line_offset += (v_size * 1.15)

        c.restoreState()

    def _draw_table(
        self, c: rl_canvas.Canvas, el: ElementBlueprint, x: float, y: float, w: float, h: float
    ) -> None:
        table_meta = el.table_meta
        if not table_meta or not table_meta.columns:
            return

        columns = table_meta.columns
        binding = el.binding
        rows_data = self._resolve_binding(binding) or []

        col_widths = [col.width_ratio * w for col in columns]

        # Table matrix
        header_row = [col.title for col in columns]
        table_data = [header_row]

        for item in rows_data:
            row = []
            for col in columns:
                raw_val = self._resolve_binding(col.field, context=item)
                row.append(self._format_value(raw_val, col.format))
            table_data.append(row)

        tbl = Table(table_data, colWidths=col_widths, repeatRows=1)

        t_style = [
            ("BACKGROUND", (0, 0), (-1, 0), self._resolve_color(table_meta.header_background)),
            ("TEXTCOLOR", (0, 0), (-1, 0), self._resolve_color(table_meta.header_color)),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
            ("TOPPADDING", (0, 0), (-1, 0), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 9),
            ("TEXTCOLOR", (0, 1), (-1, -1), self._resolve_color("text")),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
            ("TOPPADDING", (0, 1), (-1, -1), 5),
            (
                "LINEBELOW",
                (0, 0),
                (-1, -1),
                table_meta.border_width,
                self._resolve_color(table_meta.border_color),
            ),
        ]

        if table_meta.alternating_row_background:
            alt_bg = self._resolve_color(table_meta.alternating_row_background)
            for i in range(1, len(table_data)):
                if i % 2 == 0:
                    t_style.append(("BACKGROUND", (0, i), (-1, i), alt_bg))

        for idx, col in enumerate(columns):
            align = col.align.upper()
            t_style.append(("ALIGN", (idx, 0), (idx, -1), align))

        tbl.setStyle(TableStyle(t_style))
        tbl.wrapOn(c, w, h)
        tw, th = tbl.wrap(w, h)
        tbl.drawOn(c, x, (y + h) - th)

    def _draw_image(
        self, c: rl_canvas.Canvas, el: ElementBlueprint, x: float, y: float, w: float, h: float
    ) -> None:
        raw_val = self._resolve_binding(el.binding) if el.binding else None
        if not raw_val:
            return

        img_path = resolve_asset_path(str(raw_val), self.project_root)
        if not img_path or not img_path.is_file():
            return

        c.saveState()
        try:
            c.drawImage(
                str(img_path),
                x,
                y,
                width=w,
                height=h,
                preserveAspectRatio=True,
                anchor="c",
            )
        except Exception as exc:
            logger.warning("Could not draw image at %s: %s", img_path, exc)
        finally:
            c.restoreState()

    # ── Helpers ───────────────────────────────────────────────────────

    def _bbox_to_canvas(self, bbox: List[float]) -> Tuple[float, float, float, float]:
        """Translates normalized 0-1000 scale to ReportLab points."""
        bx, by, bw, bh = bbox
        ax = (bx / 1000.0) * self.page_width
        ay = (by / 1000.0) * self.page_height
        aw = (bw / 1000.0) * self.page_width
        ah = (bh / 1000.0) * self.page_height
        return ax, ay, aw, ah

    def _scale_x(self, val: float) -> float:
        return (val / 1000.0) * self.page_width

    def _scale_y(self, val: float) -> float:
        return (val / 1000.0) * self.page_height

    def _resolve_color(self, ref: Optional[str]) -> colors.Color:
        if not ref:
            return colors.transparent
        hex_val = self.palette.get(ref, ref)
        try:
            return colors.HexColor(hex_val)
        except Exception:
            return colors.black

    def _resolve_binding(
        self, binding_path: Optional[str], context: Optional[dict] = None
    ) -> Any:
        if not binding_path:
            return ""
        target = context if context is not None else self.data
        keys = binding_path.split(".")
        for k in keys:
            if isinstance(target, dict) and k in target:
                target = target[k]
            elif hasattr(target, k):
                target = getattr(target, k)
            else:
                return ""
        return target

    def _format_value(self, value: Any, fmt: Optional[str]) -> str:
        if value is None or value == "":
            return ""
        if fmt == "currency":
            curr = self._resolve_binding("pricing.currency") or "EUR"
            from app.utils.pdf_utils import format_value
            return format_value(value, fmt="currency", currency=curr)
        return str(value)

    def _group_elements_by_page(self) -> Dict[int, List[ElementBlueprint]]:
        grouped: Dict[int, List[ElementBlueprint]] = {}
        for el in self.blueprint.elements:
            grouped.setdefault(el.page, []).append(el)
        return grouped

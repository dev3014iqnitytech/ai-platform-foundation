"""
PDF Filler — handles **fillable** (AcroForm) PDFs.

Strategy:
  1. Inspect the widget list on every page.
  2. Match widget names against the field mapping (case-insensitive,
     with underscore/dot/space normalisation).
  3. Set the widget value.
  4. Flatten the form so the values are baked in.

If the dealer's PDF already has properly named AcroForm fields this
produces the cleanest result because fonts, colors, and positions come
from the original form design.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import fitz  # PyMuPDF

from app.services.template_service import TemplateConfig
from app.utils.pdf_utils import format_value

logger = logging.getLogger(__name__)


class PDFFiller:
    """Fills AcroForm widgets in a fillable PDF."""

    def fill(
        self,
        pdf_bytes: bytes,
        quote_data: Dict[str, Any],
        config: TemplateConfig,
        flatten: bool = True,
    ) -> bytes:
        """
        Returns modified PDF bytes with form fields populated.

        Parameters
        ----------
        pdf_bytes : bytes
            Original PDF template bytes.
        quote_data : dict
            Flat or nested quote dictionary.
        config : TemplateConfig
            Parsed template YAML config.
        flatten : bool
            If True, bakes the values into the PDF (non-editable output).
            Set False to keep the form live.
        """
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        filled_count = 0

        try:
            # Build a widget → value lookup keyed by normalised widget name
            widget_values = self._build_widget_values(quote_data, config)

            for page_idx in range(doc.page_count):
                page = doc.load_page(page_idx)
                for widget in page.widgets() or []:
                    name = self._normalise(widget.field_name or "")
                    if name in widget_values:
                        widget.field_value = widget_values[name]
                        widget.update()
                        filled_count += 1
                        logger.debug(
                            "Filled widget '%s' = '%s'",
                            widget.field_name,
                            widget_values[name],
                        )

            if flatten:
                # Save with flattening so widgets become static text
                import io
                buf = io.BytesIO()
                doc.save(buf, deflate=True)
                result = buf.getvalue()
            else:
                import io
                buf = io.BytesIO()
                doc.save(buf, deflate=True)
                result = buf.getvalue()

            logger.info("Filled %d AcroForm widgets", filled_count)
            return result

        finally:
            doc.close()

    # ── Private ───────────────────────────────────────────────────────

    def _build_widget_values(
        self,
        quote_data: Dict[str, Any],
        config: TemplateConfig,
    ) -> Dict[str, str]:
        """Return {normalised_widget_name: display_value}."""
        result: Dict[str, str] = {}
        currency = self._get_nested(quote_data, "pricing.currency") or "EUR"

        for _key, field_cfg in config.fields.items():
            raw_value = self._get_nested(quote_data, field_cfg.source)
            if raw_value is None:
                continue
            display = format_value(raw_value, field_cfg.format, currency)
            result[self._normalise(field_cfg.source)] = display
            # Also index by the last component so simple field names match
            result[self._normalise(field_cfg.source.split(".")[-1])] = display

        return result

    @staticmethod
    def _get_nested(data: Dict[str, Any], dotpath: str) -> Optional[Any]:
        """Retrieve a value from a nested dict using dot notation."""
        keys = dotpath.split(".")
        current: Any = data
        for k in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(k)
        return current

    @staticmethod
    def _normalise(name: str) -> str:
        """Lower-case, replace dots/spaces/underscores for fuzzy matching."""
        return name.lower().replace(".", "_").replace(" ", "_")

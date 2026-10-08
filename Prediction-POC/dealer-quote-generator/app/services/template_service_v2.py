"""
Template Service (v2)
Manages dynamic LayoutBlueprint YAML files, template discovery, and caching.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.models.layout_blueprint_v2 import LayoutBlueprint
from app.services.vision_parser_v2 import VisionLayoutParserV2

logger = logging.getLogger(__name__)


class TemplateServiceV2:
    """Registry and storage manager for v2 LayoutBlueprints."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.v2_dir = project_root / "app" / "mappings" / "v2"
        self.templates_dir = project_root / "templates"
        self.v2_dir.mkdir(parents=True, exist_ok=True)
        self.parser = VisionLayoutParserV2()
        self._seed_default_templates()

    def _seed_default_templates(self) -> None:
        """Ensures dealer_template, fendt_dealer_quote_template, and fendt_dealer_quote_template1 are saved in v2 format."""
        defaults = [
            ("dealer_template", "A4"),
            ("fendt_dealer_quote_template", "LETTER"),
            ("fendt_dealer_quote_template1", "LETTER"),
        ]
        for name, size in defaults:
            target_yaml = self.v2_dir / f"{name}.yaml"
            if not target_yaml.exists():
                logger.info("Generating default v2 blueprint for '%s'...", name)
                source_pdf = self.templates_dir / f"{name}.pdf"
                if source_pdf.exists():
                    bp = self.parser.parse_document(
                        source_pdf.read_bytes(), filename=f"{name}.pdf"
                    )
                else:
                    if name == "fendt_dealer_quote_template":
                        bp = self.parser._build_fendt_1page_blueprint(name, size)
                    elif "fendt" in name:
                        bp = self.parser._build_two_column_fendt_blueprint(name, size)
                    else:
                        bp = self.parser._build_standard_dealer_blueprint(name, size)
                self.save_blueprint(bp)

    def generate_and_save_dynamic_template(
        self,
        pdf_bytes: bytes,
        filename: str = "template.pdf",
        custom_name: Optional[str] = None,
    ) -> str:
        """
        Dynamically analyzes an uploaded template PDF, generates matching YAML mappings
        (both TemplateConfig YAML and LayoutBlueprint YAML), persists them, and returns
        the template name for instant rendering.
        """
        import uuid
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        num_pages = len(doc)
        page0 = doc[0]
        p0_w, p0_h = float(page0.rect.width), float(page0.rect.height)
        is_letter = abs(p0_w - 612.0) < 10 and abs(p0_h - 792.0) < 10

        blocks = page0.get_text("dict").get("blocks", [])
        text_spans = []
        for b in blocks:
            if b.get("type") == 0:
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        txt = s.get("text", "").strip()
                        if txt:
                            text_spans.append({"text": txt, "bbox": s.get("bbox")})

        cust_name_y = None
        email_y = None
        for s in text_spans:
            txt = s["text"]
            if "Customer Name" in txt:
                cust_name_y = s["bbox"][1]
            elif "Email:" in txt and email_y is None:
                email_y = s["bbox"][1]

        is_cust_2col = False
        if cust_name_y is not None and email_y is not None:
            if abs(cust_name_y - email_y) < 15:
                is_cust_2col = True

        text0 = page0.get_text()
        doc.close()

        clean_name = (
            custom_name.strip()
            if custom_name
            else Path(filename).stem
        )
        if not clean_name or clean_name.lower() in ("upload", "template", "file"):
            clean_name = f"dynamic_{uuid.uuid4().hex[:6]}"

        # Select layout mapping based on structural layout characteristics
        if "SAMPLE" in text0 and "DEALER" in text0:
            mapping_dict = self._get_fendt_sample_tractor_mapping(clean_name)
        elif num_pages == 1 and (is_cust_2col or (is_letter and "fendt" in clean_name.lower())):
            mapping_dict = self._get_fendt_1page_mapping(clean_name)
        elif num_pages >= 2:
            mapping_dict = self._get_fendt_2page_mapping(clean_name)
        else:
            mapping_dict = self._get_dealer_1page_mapping(clean_name)

        # 1. Persist v1 TemplateConfig YAML
        mappings_v1_dir = self.project_root / "app" / "mappings"
        mappings_v1_dir.mkdir(parents=True, exist_ok=True)
        v1_yaml_path = mappings_v1_dir / f"{clean_name}.yaml"
        v1_yaml_path.write_text(yaml.dump(mapping_dict, sort_keys=False, default_flow_style=False), encoding="utf-8")
        logger.info("Dynamically generated v1 mapping YAML at %s", v1_yaml_path)

        # 2. Persist v2 LayoutBlueprint YAML
        blueprint = self.parser.parse_document(pdf_bytes, filename=f"{clean_name}.pdf")
        blueprint.template_name = clean_name
        self.save_blueprint(blueprint)

        return clean_name

    def _get_fendt_1page_mapping(self, name: str) -> Dict[str, Any]:
        return {
            "template_name": name,
            "version": "2.0",
            "description": "Dynamically generated Fendt 1-page Letter template mapping",
            "defaults": {
                "font": "Helvetica",
                "font_size": 9,
                "color": [0, 0, 0],
                "bold": False,
                "align": "left",
            },
            "fields": {
                "quote_number": {
                    "source": "quote.quoteNumber",
                    "page": 1,
                    "x": 450,
                    "y": 736,
                    "font_size": 9,
                    "color": [1.0, 1.0, 1.0],
                    "max_width": 120,
                },
                "quote_date": {
                    "source": "quote.quoteDate",
                    "page": 1,
                    "x": 450,
                    "y": 721,
                    "font_size": 9,
                    "color": [1.0, 1.0, 1.0],
                    "max_width": 120,
                },
                "quote_valid_until": {
                    "source": "quote.validUntil",
                    "page": 1,
                    "x": 450,
                    "y": 706,
                    "font_size": 9,
                    "color": [1.0, 1.0, 1.0],
                    "max_width": 120,
                },
                "customer_name": {
                    "source": "customer.name",
                    "page": 1,
                    "x": 130,
                    "y": 640,
                    "font_size": 9,
                    "bold": True,
                    "max_width": 150,
                },
                "customer_email": {
                    "source": "customer.email",
                    "page": 1,
                    "x": 365,
                    "y": 640,
                    "font_size": 9,
                    "max_width": 150,
                },
                "customer_company": {
                    "source": "customer.company",
                    "page": 1,
                    "x": 130,
                    "y": 621,
                    "font_size": 9,
                    "max_width": 150,
                },
                "customer_phone": {
                    "source": "customer.phone",
                    "page": 1,
                    "x": 365,
                    "y": 621,
                    "font_size": 9,
                    "max_width": 150,
                },
                "customer_address": {
                    "source": "customer.address",
                    "page": 1,
                    "x": 130,
                    "y": 602,
                    "font_size": 9,
                    "max_width": 150,
                },
                "product_brand": {
                    "source": "product.brand",
                    "page": 1,
                    "x": 85,
                    "y": 545,
                    "font_size": 9,
                    "bold": True,
                    "max_width": 100,
                },
                "product_configuration": {
                    "source": "product.configuration",
                    "page": 1,
                    "x": 290,
                    "y": 545,
                    "font_size": 9,
                    "max_width": 95,
                },
                "product_model": {
                    "source": "product.model",
                    "page": 1,
                    "x": 85,
                    "y": 513,
                    "font_size": 9,
                    "bold": True,
                    "color": [0.0, 0.29, 0.53],
                    "max_width": 100,
                },
                "product_serial": {
                    "source": "product.serialNumber",
                    "page": 1,
                    "x": 290,
                    "y": 513,
                    "font_size": 9,
                    "color": [0.3, 0.3, 0.3],
                    "max_width": 95,
                },
                "product_series": {
                    "source": "product.series",
                    "page": 1,
                    "x": 85,
                    "y": 486,
                    "font_size": 9,
                    "max_width": 100,
                },
                "pricing_base": {
                    "source": "pricing.basePrice",
                    "page": 1,
                    "x": 570,
                    "y": 328,
                    "font_size": 9,
                    "format": "currency",
                    "align": "right",
                },
                "pricing_options": {
                    "source": "pricing.optionsPrice",
                    "page": 1,
                    "x": 570,
                    "y": 313,
                    "font_size": 9,
                    "format": "currency",
                    "align": "right",
                },
                "pricing_discount": {
                    "source": "pricing.discount",
                    "page": 1,
                    "x": 570,
                    "y": 298,
                    "font_size": 9,
                    "format": "currency",
                    "color": [0.7, 0.0, 0.0],
                    "align": "right",
                },
                "pricing_tax": {
                    "source": "pricing.tax",
                    "page": 1,
                    "x": 570,
                    "y": 283,
                    "font_size": 9,
                    "format": "currency",
                    "align": "right",
                },
                "pricing_total": {
                    "source": "pricing.totalPrice",
                    "page": 1,
                    "x": 268,
                    "y": 261,
                    "font_size": 11,
                    "bold": True,
                    "format": "currency",
                    "color": [1.0, 1.0, 1.0],
                    "align": "left",
                },
                "dealer_name": {
                    "source": "dealer.name",
                    "page": 1,
                    "x": 102,
                    "y": 208,
                    "font_size": 8,
                    "bold": True,
                    "color": [1.0, 1.0, 1.0],
                    "max_width": 115,
                },
                "dealer_email": {
                    "source": "dealer.email",
                    "page": 1,
                    "x": 345,
                    "y": 208,
                    "font_size": 8,
                    "color": [1.0, 1.0, 1.0],
                    "max_width": 115,
                },
                "dealer_contact": {
                    "source": "dealer.contactPerson",
                    "page": 1,
                    "x": 82,
                    "y": 192,
                    "font_size": 8,
                    "color": [1.0, 1.0, 1.0],
                    "max_width": 115,
                },
                "dealer_phone": {
                    "source": "dealer.phone",
                    "page": 1,
                    "x": 348,
                    "y": 192,
                    "font_size": 8,
                    "color": [1.0, 1.0, 1.0],
                    "max_width": 115,
                },
            },
            "options_table": {
                "page": 1,
                "start_x": 42,
                "start_y": 420,
                "row_height": 16,
                "max_rows": 4,
                "columns": {
                    "code": {"label": "Code", "x_offset": 0, "width": 70, "font_size": 9, "align": "left"},
                    "description": {"label": "Description", "x_offset": 80, "width": 340, "font_size": 9, "align": "left"},
                    "price": {"label": "Price", "x_offset": 430, "width": 100, "font_size": 9, "format": "currency", "align": "right"},
                },
                "header": {"draw": False},
            },
            "product_image": {
                "source": "product.image",
                "page": 1,
                "x": 400,
                "y": 496,
                "width": 173,
                "height": 68,
            },
        }

    def _get_fendt_sample_tractor_mapping(self, name: str) -> Dict[str, Any]:
        p4_path = self.project_root / "app" / "mappings" / "fendt_quote_template four.yaml"
        if p4_path.exists():
            d = yaml.safe_load(p4_path.read_text(encoding="utf-8"))
            d["template_name"] = name
            return d
        return self._get_fendt_1page_mapping(name)

    def _get_fendt_2page_mapping(self, name: str) -> Dict[str, Any]:
        p2_path = self.project_root / "app" / "mappings" / "fendt_dealer_quote_template three.yaml"
        if not p2_path.exists():
            p2_path = self.project_root / "app" / "mappings" / "fendt_dealer_quote_template1.yaml"
        if p2_path.exists():
            d = yaml.safe_load(p2_path.read_text(encoding="utf-8"))
            d["template_name"] = name
            return d
        return self._get_fendt_1page_mapping(name)

    def _get_dealer_1page_mapping(self, name: str) -> Dict[str, Any]:
        dealer_path = self.project_root / "app" / "mappings" / "dealer_template.yaml"
        if dealer_path.exists():
            return yaml.safe_load(dealer_path.read_text(encoding="utf-8"))
        return self._get_fendt_1page_mapping(name)

    def list_blueprints(self) -> List[Dict[str, Any]]:
        """Lists all registered v2 blueprints with summary metadata."""
        results = []
        for yaml_path in sorted(self.v2_dir.glob("*.yaml")):
            try:
                bp = self.get_blueprint(yaml_path.stem)
                results.append({
                    "id": yaml_path.stem,
                    "name": bp.template_name,
                    "description": bp.description,
                    "version": bp.version,
                    "page_size": bp.page.size,
                    "elements_count": len(bp.elements),
                })
            except Exception as exc:
                logger.warning("Could not read blueprint %s: %s", yaml_path, exc)
        return results

    def get_blueprint(self, template_id_or_name: str) -> LayoutBlueprint:
        """Retrieves and parses a LayoutBlueprint by name or ID."""
        clean_id = Path(template_id_or_name).stem
        yaml_path = self.v2_dir / f"{clean_id}.yaml"
        if not yaml_path.exists():
            raise FileNotFoundError(f"V2 Template blueprint '{clean_id}' not found.")
        return LayoutBlueprint.from_yaml(yaml_path.read_text(encoding="utf-8"))

    def save_blueprint(self, blueprint: LayoutBlueprint) -> Path:
        """Saves a LayoutBlueprint to the v2 storage folder."""
        file_path = self.v2_dir / f"{blueprint.template_name}.yaml"
        file_path.write_text(blueprint.to_yaml(), encoding="utf-8")
        logger.info("Saved v2 blueprint to %s", file_path)
        return file_path

    def get_template_pdf_bytes(self, template_name: str) -> Optional[bytes]:
        """Loads source template PDF from templates directory if available."""
        clean_name = Path(template_name).stem
        pdf_path = self.templates_dir / f"{clean_name}.pdf"
        if pdf_path.exists():
            return pdf_path.read_bytes()
        return None

    def load_mock_quote(self) -> Dict[str, Any]:
        """Loads the mock quote JSON payload for rapid testing and demos."""
        mock_path = self.project_root / "mock" / "quote_response.json"
        if not mock_path.exists():
            raise FileNotFoundError(f"Mock quote file not found at {mock_path}")
        return json.loads(mock_path.read_text(encoding="utf-8"))

"""
Template service — loads and validates YAML mapping files.

Each dealer has their own YAML. This service finds, loads, and
provides typed access to the mapping config without coupling it to
the PDF engine.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Pydantic config models ────────────────────────────────────────────────────


class ColumnConfig(BaseModel):
    label: str
    x_offset: float
    width: float
    font_size: int = 9
    format: Optional[str] = None
    align: str = "left"


class TableHeaderConfig(BaseModel):
    draw: bool = False
    font_size: int = 9
    bold: bool = True
    color: List[float] = Field(default=[1, 1, 1])
    background: List[float] = Field(default=[0.0, 0.3, 0.6])


class OptionsTableConfig(BaseModel):
    page: int = 1
    start_x: float = 33
    start_y: float = 364
    row_height: float = 18
    max_rows: int = 10
    columns: Dict[str, ColumnConfig] = {}
    header: TableHeaderConfig = Field(default_factory=TableHeaderConfig)


class ProductImageConfig(BaseModel):
    source: str = "product.image"
    page: int = 1
    x: float
    y: float
    width: float
    height: float


class FieldConfig(BaseModel):
    source: str                     # dot-notation path into quote JSON
    page: int = 1                   # 1-indexed
    x: float
    y: float                        # ReportLab convention: bottom=0
    font: str = "Helvetica"
    font_size: int = 10
    bold: bool = False
    color: List[float] = Field(default=[0.0, 0.0, 0.0])
    format: Optional[str] = None   # e.g. "currency"
    align: str = "left"            # "left", "right", "center"
    max_width: Optional[float] = None


class TemplateDefaults(BaseModel):
    font: str = "Helvetica"
    font_size: int = 10
    color: List[float] = Field(default=[0.0, 0.0, 0.0])
    bold: bool = False
    align: str = "left"


class TemplateConfig(BaseModel):
    template_name: str
    version: str = "1.0"
    description: str = ""
    defaults: TemplateDefaults = Field(default_factory=TemplateDefaults)
    fields: Dict[str, FieldConfig] = {}
    options_table: Optional[OptionsTableConfig] = None
    product_image: Optional[ProductImageConfig] = None


# ── Service ───────────────────────────────────────────────────────────────────


class TemplateService:
    """Loads template YAML mappings and provides field lookup."""

    def __init__(self, mappings_dir: Path) -> None:
        self._dir = mappings_dir
        self._cache: Dict[str, TemplateConfig] = {}

    # ── Public API ────────────────────────────────────────────────────

    def load(self, template_name: str) -> TemplateConfig:
        """Load and cache a template config by name (without extension)."""
        if template_name in self._cache:
            return self._cache[template_name]

        path = self._dir / f"{template_name}.yaml"
        if not path.is_file():
            raise FileNotFoundError(
                f"Template mapping '{template_name}' not found at {path}"
            )

        logger.info("Loading template mapping: %s", path)
        with open(path, "r", encoding="utf-8") as fh:
            raw: Dict[str, Any] = yaml.safe_load(fh)

        # Apply defaults to every field that hasn't overridden them
        defaults_raw = raw.get("defaults", {})
        defaults = TemplateDefaults(**defaults_raw)
        fields_raw: Dict[str, Any] = raw.get("fields", {})

        merged_fields: Dict[str, FieldConfig] = {}
        for key, cfg in fields_raw.items():
            # Merge defaults → field config (field wins)
            merged = {
                "font": defaults.font,
                "font_size": defaults.font_size,
                "color": defaults.color,
                "bold": defaults.bold,
                "align": defaults.align,
            }
            merged.update(cfg)
            merged_fields[key] = FieldConfig(**merged)

        raw["fields"] = merged_fields

        # Parse options_table
        if "options_table" in raw:
            raw["options_table"] = OptionsTableConfig(**raw["options_table"])

        # Parse product_image
        if "product_image" in raw:
            raw["product_image"] = ProductImageConfig(**raw["product_image"])

        config = TemplateConfig(**{k: v for k, v in raw.items() if k != "defaults"})
        config.defaults = defaults
        self._cache[template_name] = config
        logger.info(
            "Template '%s' loaded with %d fields", template_name, len(config.fields)
        )
        return config

    def list_templates(self) -> List[str]:
        """Return all available template names (YAML filenames without ext)."""
        return [p.stem for p in self._dir.glob("*.yaml")]

    def invalidate_cache(self, template_name: Optional[str] = None) -> None:
        if template_name:
            self._cache.pop(template_name, None)
        else:
            self._cache.clear()

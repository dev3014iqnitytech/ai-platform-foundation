"""
Layout Blueprint Schema (v2)
Standardized Pydantic models for zero-shot dynamic layout blueprints.
Decouples visual layout geometry and styling from business domain data.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import yaml


class PageMargins(BaseModel):
    top: float = 36.0
    bottom: float = 36.0
    left: float = 36.0
    right: float = 36.0


class PageConfig(BaseModel):
    size: str = "A4"  # A4, LETTER, LEGAL
    orientation: str = "portrait"  # portrait or landscape
    margins: PageMargins = Field(default_factory=PageMargins)
    palette: Dict[str, str] = Field(
        default_factory=lambda: {
            "primary": "#1A365D",
            "secondary": "#4A5568",
            "accent": "#3182CE",
            "background": "#FFFFFF",
            "text": "#2D3748",
            "muted": "#718096",
            "border": "#E2E8F0",
            "row_alt": "#F7FAFC",
        }
    )


class GridField(BaseModel):
    label: str
    binding: str
    format: Optional[str] = None  # currency, date, etc.
    col_span: int = 1
    align: str = "left"  # left, center, right
    style: Optional[Dict[str, Any]] = None


class GridLayout(BaseModel):
    columns: int = 1
    column_gap: float = 30.0
    row_gap: float = 10.0


class TableColumn(BaseModel):
    field: str
    title: str
    width_ratio: float = 0.2
    align: str = "left"
    format: Optional[str] = None


class TableMeta(BaseModel):
    header_background: str = "primary"
    header_color: str = "#FFFFFF"
    alternating_row_background: Optional[str] = "row_alt"
    border_width: float = 0.5
    border_color: str = "border"
    row_height: float = 22.0
    columns: List[TableColumn] = Field(default_factory=list)


class ElementBlueprint(BaseModel):
    id: str
    type: str  # text, shape, image, key_value_grid, table
    bbox: List[float]  # [x, y, width, height] normalized (0-1000) or absolute
    page: int = 1
    shape_type: Optional[str] = "rect"  # rect, line
    text: Optional[str] = None
    binding: Optional[str] = None
    style: Optional[Dict[str, Any]] = None
    layout: Optional[GridLayout] = None
    fields: Optional[List[GridField]] = None
    table_meta: Optional[TableMeta] = None


class LayoutBlueprint(BaseModel):
    version: str = "2.0"
    template_name: str
    description: Optional[str] = None
    page: PageConfig = Field(default_factory=PageConfig)
    elements: List[ElementBlueprint] = Field(default_factory=list)

    def to_yaml(self) -> str:
        data = self.model_dump(exclude_none=True)
        return yaml.dump(data, sort_keys=False, default_flow_style=False)

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "LayoutBlueprint":
        data = yaml.safe_load(yaml_content)
        return cls(**data)

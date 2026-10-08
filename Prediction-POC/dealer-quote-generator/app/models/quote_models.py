"""
Pydantic models for quote data.

These models validate the incoming JSON quote payload before processing.
They are intentionally kept separate from PDF/API logic.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, EmailStr, field_validator, model_validator
import re


# ─────────────────────────────────────────────────────────────────────────────
# Sub-models
# ─────────────────────────────────────────────────────────────────────────────


class QuoteInfo(BaseModel):
    quoteNumber: str
    quoteDate: str
    validUntil: str

    @field_validator("quoteDate", "validUntil")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError(f"Date '{v}' must be in YYYY-MM-DD format")
        return v


class Customer(BaseModel):
    name: str
    company: Optional[str] = None
    address: str
    email: str
    phone: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Customer name cannot be empty")
        return v.strip()

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError(f"Invalid email: {v}")
        return v.lower().strip()


class Product(BaseModel):
    brand: str
    model: str
    series: Optional[str] = None
    configuration: Optional[str] = None
    serialNumber: Optional[str] = None
    image: Optional[str] = None  # local path relative to project root

    @field_validator("brand", "model")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class Option(BaseModel):
    code: Optional[str] = None
    description: str
    price: float

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Option price cannot be negative")
        return v


class Pricing(BaseModel):
    basePrice: float
    optionsPrice: float
    discount: float = 0.0
    tax: float = 0.0
    totalPrice: float
    currency: str = "EUR"

    @field_validator("basePrice", "totalPrice")
    @classmethod
    def price_positive(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Price cannot be negative")
        return v

    @model_validator(mode="after")
    def validate_total(self) -> "Pricing":
        expected = self.basePrice + self.optionsPrice - self.discount + self.tax
        # Allow 1% tolerance for rounding
        if abs(expected - self.totalPrice) > max(1.0, self.totalPrice * 0.01):
            raise ValueError(
                f"totalPrice {self.totalPrice} does not match computed "
                f"{expected:.2f} (base + options - discount + tax)"
            )
        return self


class Dealer(BaseModel):
    name: str
    address: Optional[str] = None
    contactPerson: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Root model
# ─────────────────────────────────────────────────────────────────────────────


class QuoteRequest(BaseModel):
    """Root quote payload — validated before PDF generation."""

    quote: QuoteInfo
    customer: Customer
    product: Product
    options: List[Option] = []
    pricing: Pricing
    dealer: Dealer

    model_config = {"extra": "forbid"}

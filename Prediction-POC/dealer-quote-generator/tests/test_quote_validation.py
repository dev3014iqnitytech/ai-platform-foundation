"""
Tests for Quote JSON validation (Pydantic models).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.quote_models import Customer, Pricing, QuoteInfo, QuoteRequest
from tests.conftest import VALID_QUOTE


# ── QuoteInfo ─────────────────────────────────────────────────────────────────


class TestQuoteInfo:
    def test_valid(self):
        qi = QuoteInfo(quoteNumber="QT-001", quoteDate="2026-10-08", validUntil="2026-10-22")
        assert qi.quoteNumber == "QT-001"

    def test_invalid_date_format(self):
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            QuoteInfo(quoteNumber="X", quoteDate="08-10-2026", validUntil="2026-10-22")


# ── Customer ──────────────────────────────────────────────────────────────────


class TestCustomer:
    def test_valid(self):
        c = Customer(name="John Smith", address="Nowhere", email="j@example.com")
        assert c.email == "j@example.com"

    def test_empty_name(self):
        with pytest.raises(ValidationError):
            Customer(name="   ", address="x", email="j@x.com")

    def test_invalid_email(self):
        with pytest.raises(ValidationError, match="Invalid email"):
            Customer(name="Bob", address="x", email="not-an-email")

    def test_email_lowercased(self):
        c = Customer(name="Bob", address="x", email="BOB@EXAMPLE.COM")
        assert c.email == "bob@example.com"


# ── Pricing ───────────────────────────────────────────────────────────────────


class TestPricing:
    def test_valid(self):
        p = Pricing(
            basePrice=100, optionsPrice=10, discount=5, tax=21, totalPrice=126
        )
        assert p.totalPrice == 126

    def test_negative_base_price(self):
        with pytest.raises(ValidationError):
            Pricing(
                basePrice=-1, optionsPrice=0, discount=0, tax=0, totalPrice=-1
            )

    def test_total_mismatch(self):
        with pytest.raises(ValidationError, match="totalPrice"):
            Pricing(
                basePrice=100, optionsPrice=0, discount=0, tax=0, totalPrice=999
            )


# ── Full QuoteRequest ─────────────────────────────────────────────────────────


class TestQuoteRequest:
    def test_valid_full_quote(self):
        q = QuoteRequest(**VALID_QUOTE)
        assert q.customer.name == "John Smith"
        assert q.product.model == "Fendt 724 Vario"
        assert q.pricing.totalPrice == 183840

    def test_missing_customer(self):
        bad = {k: v for k, v in VALID_QUOTE.items() if k != "customer"}
        with pytest.raises(ValidationError):
            QuoteRequest(**bad)

    def test_extra_fields_forbidden(self):
        bad = {**VALID_QUOTE, "unexpectedField": "oops"}
        with pytest.raises(ValidationError):
            QuoteRequest(**bad)

    def test_option_negative_price(self):
        bad = {
            **VALID_QUOTE,
            "options": [{"code": "X", "description": "Test", "price": -100}],
        }
        with pytest.raises(ValidationError):
            QuoteRequest(**bad)

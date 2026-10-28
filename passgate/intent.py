"""Approved purchase intent and vendor terms.

The model may *propose* fields by reading unstructured text, but the Intent that
gates spending is a typed, validated object. `parse_terms_text` is a small
deterministic extractor used for the demo and for tests; an LLM extractor can be
plugged in behind the same signature (it must return the same dataclass, and the
verifier still compares field by field).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_HALF_UP


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class VendorTerms:
    """What a vendor quote/invoice says (evidence)."""
    vendor: str
    currency: str
    monthly_price: Decimal
    annual_price: Decimal | None
    merchant_category_code: str
    cancellation: str  # e.g. "anytime", "end_of_term", "none"
    billing_term: str | None = None  # term the document bills: MONTHLY / ANNUAL
    amount_due: Decimal | None = None  # amount this document asks to charge

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = str(v)
        return d


@dataclass(frozen=True)
class Intent:
    """The approved intent: exactly what the agent is allowed to buy."""
    vendor: str
    currency: str
    billing_term: str  # MONTHLY | ANNUAL
    amount: Decimal  # per charge, major units
    merchant_category_code: str
    cancellation: str
    reconsider_on: str | None = None  # ISO date
    rationale: list[str] = field(default_factory=list)

    def bound_fields(self) -> dict:
        """Fields a PASS is bound to (order-stable)."""
        return {
            "vendor": self.vendor.strip().lower(),
            "currency": self.currency.upper(),
            "billing_term": self.billing_term.upper(),
            "amount": str(money(self.amount)),
            "merchant_category_code": self.merchant_category_code,
            "cancellation": self.cancellation.lower(),
        }


_NUM = r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)"


def parse_terms_text(text: str) -> VendorTerms:
    """Deterministic extractor for the demo quote/invoice format.

    Recognised lines (case-insensitive):
      Vendor: Acme Analytics
      Currency: USD
      Monthly: 1200            (or "Monthly price: $1,200.00")
      Annual: 11808
      MCC: 5734
      Cancellation: anytime
      Billing term: MONTHLY    (invoice only)
      Amount due: 1200         (invoice only)
    """
    def grab(label: str, pattern: str = r"(.+)") -> str | None:
        m = re.search(rf"^\s*{label}\s*:\s*{pattern}\s*$", text, re.I | re.M)
        return m.group(1).strip() if m else None

    def num(label: str) -> Decimal | None:
        m = re.search(rf"^\s*{label}\s*:\s*[^0-9\n]*{_NUM}", text, re.I | re.M)
        return money(m.group(1).replace(",", "")) if m else None

    vendor = grab("vendor")
    currency = (grab("currency") or "").upper()
    monthly = num(r"monthly(?: price)?")
    if not vendor or not currency or monthly is None:
        raise ValueError("terms missing vendor/currency/monthly price")
    return VendorTerms(
        vendor=vendor,
        currency=currency,
        monthly_price=monthly,
        annual_price=num(r"annual(?: price)?"),
        merchant_category_code=grab("mcc", r"([0-9]{4})") or "",
        cancellation=(grab("cancellation") or "none").lower(),
        billing_term=(grab("billing term") or None) and grab("billing term").upper(),
        amount_due=num("amount due"),
    )

"""Decide: compute the cash effect of each billing option against a reserve floor.

Pure code. The model can explain the result but never computes it.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from .intent import Intent, VendorTerms, money


@dataclass(frozen=True)
class CashForecast:
    opening_cash: Decimal          # from GET /balances/current (available, in currency)
    weekly_net_outflow: Decimal    # other committed burn per week
    reserve_floor: Decimal
    horizon_weeks: int = 12


@dataclass(frozen=True)
class OptionResult:
    term: str
    total_12m: Decimal
    first_breach_week: int | None  # 1-based week where cash < floor, None if never
    min_cash: Decimal


def simulate(terms: VendorTerms, fc: CashForecast, term: str) -> OptionResult:
    cash = money(fc.opening_cash)
    min_cash = cash
    breach = None
    if term == "ANNUAL":
        if terms.annual_price is None:
            raise ValueError("vendor has no annual price")
        cash -= terms.annual_price
        total = terms.annual_price
    else:
        total = terms.monthly_price * 12
    for week in range(1, fc.horizon_weeks + 1):
        if term == "MONTHLY" and (week - 1) % 4 == 0:  # charge at start of each 4-week month
            cash -= terms.monthly_price
        cash -= fc.weekly_net_outflow
        min_cash = min(min_cash, cash)
        if breach is None and cash < fc.reserve_floor:
            breach = week
    return OptionResult(term, money(total), breach, money(min_cash))


def decide(terms: VendorTerms, fc: CashForecast, today: dt.date | None = None) -> tuple[Intent, dict]:
    """Return the intent to approve plus the full analysis for the approver."""
    today = today or dt.date.today()
    monthly = simulate(terms, fc, "MONTHLY")
    annual = simulate(terms, fc, "ANNUAL") if terms.annual_price is not None else None
    rationale: list[str] = []
    if annual and annual.first_breach_week is None:
        choice, amount = "ANNUAL", terms.annual_price
        rationale.append("Annual keeps cash above the reserve floor across the horizon.")
        reconsider = None
    else:
        choice, amount = "MONTHLY", terms.monthly_price
        if annual:
            saving = money(monthly.total_12m - annual.total_12m)
            pct = (saving / monthly.total_12m * 100).quantize(Decimal("0.1"))
            rationale.append(
                f"Annual saves {saving} {terms.currency} ({pct}%) but cash falls below the "
                f"{fc.reserve_floor} reserve in week {annual.first_breach_week}."
            )
        if monthly.first_breach_week is not None:
            rationale.append(f"WARNING: even monthly breaches the reserve in week {monthly.first_breach_week}; escalate.")
        reconsider = (today + dt.timedelta(days=90)).isoformat()
        rationale.append(f"Choose monthly; reconsider annual on {reconsider} with a fresh balance read.")
    intent = Intent(
        vendor=terms.vendor,
        currency=terms.currency,
        billing_term=choice,
        amount=money(amount),
        merchant_category_code=terms.merchant_category_code,
        cancellation=terms.cancellation,
        reconsider_on=reconsider,
        rationale=rationale,
    )
    analysis = {"monthly": monthly.__dict__, "annual": annual.__dict__ if annual else None}
    return intent, analysis

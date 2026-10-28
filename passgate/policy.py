"""Spend policy: turn a verified intent into Airwallex card authorization controls.

Code builds the payload; the model never does.
Field names checked against the Airwallex Docs MCP (Create a Card), 2026-10-28.
"""
from __future__ import annotations

from .intent import Intent, money


def card_controls(intent: Intent) -> dict:
    amt = float(money(intent.amount))
    limits = [{"interval": "PER_TRANSACTION", "amount": amt}]  # inclusive
    # PER_TRANSACTION is not cumulative: cap the period too.
    limits.append({"interval": "MONTHLY" if intent.billing_term == "MONTHLY" else "ALL_TIME", "amount": amt})
    return {
        "allowed_transaction_count": "MULTIPLE" if intent.billing_term == "MONTHLY" else "SINGLE",
        "transaction_limits": {"currency": intent.currency, "limits": limits},
        "allowed_currencies": [intent.currency],
        "allowed_merchant_categories": [intent.merchant_category_code],
    }


def card_create_payload(intent: Intent, cardholder_id: str, request_id: str, created_by: str) -> dict:
    return {
        "request_id": request_id,
        "cardholder_id": cardholder_id,
        "created_by": created_by,
        "form_factor": "VIRTUAL",
        "is_personalized": False,  # DELEGATE cardholders take non-personalized cards
        "program": {"purpose": "COMMERCIAL"},
        "nick_name": f"PassGate {intent.vendor}"[:32],
        "authorization_controls": card_controls(intent),
        "metadata": {"passgate": "1", "vendor": intent.vendor[:40]},
    }

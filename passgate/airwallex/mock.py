"""In-memory stand-in for the Airwallex Issuing sandbox.

Emulates the documented authorization-control behaviour so the full PassGate flow
runs without credentials: PER_TRANSACTION (inclusive), MONTHLY/ALL_TIME cumulative
caps, allowed_currencies, allowed_merchant_categories, card status, and the
failure_reason values from the Kit 2 guide. It is NOT authoritative: every rule here
must be re-confirmed on the real sandbox (see BLOCKED.md / scripts/day1_check.py).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from .base import AirwallexError


class MockIssuingClient:
    name = "mock"

    def __init__(self, balances: dict[str, float] | None = None):
        self.balances = {k: Decimal(str(v)) for k, v in (balances or {"USD": 64000}).items()}
        self.cardholders: dict[str, dict] = {}
        self.cards: dict[str, dict] = {}
        self.txs: list[dict] = []
        self._seen_request_ids: set[str] = set()

    def _req(self, request_id: str):
        if request_id in self._seen_request_ids:
            raise AirwallexError(400, "duplicate_request_id", request_id)
        self._seen_request_ids.add(request_id)

    def get_balance(self, currency: str) -> float:
        return float(self.balances.get(currency, Decimal(0)))

    def create_cardholder(self, email: str, request_id: str) -> dict:
        self._req(request_id)
        cid = str(uuid.uuid4())
        self.cardholders[cid] = {"cardholder_id": cid, "type": "DELEGATE", "email": email, "status": "READY"}
        return self.cardholders[cid]

    def get_cardholder(self, cardholder_id: str) -> dict:
        return self.cardholders[cardholder_id]

    def create_card(self, payload: dict) -> dict:
        self._req(payload["request_id"])
        if payload["cardholder_id"] not in self.cardholders:
            raise AirwallexError(400, "validation_error", "unknown cardholder_id")
        ac = payload["authorization_controls"]
        if "allowed_transaction_count" not in ac:
            raise AirwallexError(400, "validation_error", "allowed_transaction_count required")
        cid = str(uuid.uuid4())
        self.cards[cid] = {"card_id": cid, "card_status": "ACTIVE", "form_factor": payload["form_factor"],
                           "authorization_controls": ac, "nick_name": payload.get("nick_name")}
        return self.cards[cid]

    def get_card(self, card_id: str) -> dict:
        return self.cards[card_id]

    def _spent(self, card_id: str) -> Decimal:
        return sum((Decimal(str(t["amount"])) for t in self.txs
                    if t["card_id"] == card_id and t["status"] in ("PENDING", "APPROVED")), Decimal(0))

    def get_card_limits(self, card_id: str) -> dict:
        ac = self.cards[card_id]["authorization_controls"]
        spent = self._spent(card_id)
        out = []
        for lim in ac["transaction_limits"]["limits"]:
            amt = Decimal(str(lim["amount"]))
            rem = amt if lim["interval"] == "PER_TRANSACTION" else amt - spent
            out.append({"interval": lim["interval"], "amount": float(amt), "remaining": float(rem)})
        return {"currency": ac["transaction_limits"]["currency"], "limits": out}

    def set_card_status(self, card_id: str, status: str, request_id: str) -> dict:
        self._req(request_id)
        card = self.cards[card_id]
        if card["card_status"] == "CLOSED":
            raise AirwallexError(400, "card_closed", "closed cards cannot change status")
        if status not in ("ACTIVE", "INACTIVE", "CLOSED"):
            raise AirwallexError(400, "validation_error", status)
        card["card_status"] = status
        return card

    def sim_authorize(self, card_id, amount, currency, mcc, merchant) -> dict:
        card = self.cards[card_id]
        ac = card["authorization_controls"]
        amt = Decimal(str(amount))
        reason = None
        if card["card_status"] == "CLOSED":
            reason = "CARD_CLOSED"
        elif card["card_status"] != "ACTIVE":
            reason = "CARD_INACTIVE"
        elif ac.get("allowed_currencies") and currency not in ac["allowed_currencies"]:
            reason = "CURRENCY_NOT_ALLOWED"
        elif ac.get("allowed_merchant_categories") and mcc not in ac["allowed_merchant_categories"]:
            reason = "MERCHANT_CATEGORY_NOT_ALLOWED"
        else:
            spent = self._spent(card_id)
            for lim in ac["transaction_limits"]["limits"]:
                cap = Decimal(str(lim["amount"]))
                if lim["interval"] == "PER_TRANSACTION" and amt > cap:
                    reason = "LIMIT_EXCEEDED"
                elif lim["interval"] != "PER_TRANSACTION" and spent + amt > cap:
                    reason = "LIMIT_EXCEEDED"
            if (reason is None and ac.get("allowed_transaction_count") == "SINGLE"
                    and any(t["card_id"] == card_id and t["status"] != "FAILED" for t in self.txs)):
                reason = "LIMIT_EXCEEDED"
        lc = str(uuid.uuid4())
        tx = {"transaction_id": str(uuid.uuid4()), "card_transaction_lifecycle_id": lc, "card_id": card_id,
              "amount": float(amt), "currency": currency, "merchant": {"name": merchant, "category_code": mcc},
              "transaction_type": "AUTHORIZATION", "status": "FAILED" if reason else "PENDING",
              "failure_reason": reason}
        self.txs.append(tx)
        return dict(tx)

    def sim_capture(self, lifecycle_id: str) -> dict:
        for t in self.txs:
            if t["card_transaction_lifecycle_id"] == lifecycle_id and t["status"] == "PENDING":
                t["status"], t["transaction_type"] = "APPROVED", "CLEARING"
                self.balances[t["currency"]] = self.balances.get(t["currency"], Decimal(0)) - Decimal(str(t["amount"]))
                return dict(t)
        raise AirwallexError(404, "not_found", lifecycle_id)

    def list_transactions(self, card_id: str) -> list[dict]:
        return [dict(t) for t in self.txs if t["card_id"] == card_id]

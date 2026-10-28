"""Orchestration: Decide -> Verify -> Spend, with an audit log.

Permissions:
  agent may:      read balances, compute options, run the verifier, issue a card scoped
                  to a PASS, freeze a card (kill-switch, always allowed)
  needs approval: approving an intent; any FAIL (price/terms change); unfreezing after FAIL
  never:          see API keys, set limits by hand, unfreeze without a valid PASS
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

from .airwallex.base import IssuingClient
from .decide import CashForecast, decide
from .intent import Intent, VendorTerms
from .policy import card_create_payload
from .verifier import Verdict, check_pass, verify


def rid() -> str:
    return str(uuid.uuid4())


@dataclass
class PassGate:
    client: IssuingClient
    approver: str = "Finance approver"
    audit: list[dict] = field(default_factory=list)
    cardholder_id: str | None = None
    card_id: str | None = None
    intent: Intent | None = None
    last_pass: Verdict | None = None

    def log(self, step: str, **data):
        self.audit.append({"ts": round(time.time(), 3), "step": step, **data})

    # 1. Decide
    def propose(self, terms: VendorTerms, weekly_net_outflow, reserve_floor) -> tuple[Intent, dict]:
        cash = self.client.get_balance(terms.currency)
        fc = CashForecast(opening_cash=cash, weekly_net_outflow=weekly_net_outflow, reserve_floor=reserve_floor)
        intent, analysis = decide(terms, fc)
        self.log("decide", balance=cash, choice=intent.billing_term, amount=str(intent.amount),
                 rationale=intent.rationale, analysis=json.loads(json.dumps(analysis, default=str)))
        return intent, analysis

    def approve(self, intent: Intent, approved_by: str | None = None):
        self.intent = intent
        self.log("human_approval", by=approved_by or self.approver, intent=intent.bound_fields())

    # 2. Verify
    def verify(self, evidence: VendorTerms, raw: str = "") -> Verdict:
        assert self.intent, "no approved intent"
        v = verify(self.intent, evidence, raw)
        self.log("verify", result=v.result, diff=v.diff, terms_hash=v.terms_hash, pass_id=v.pass_id)
        if not v.ok and self.card_id:
            self.freeze("verifier FAIL")
        if v.ok:
            self.last_pass = v
        return v

    # 3. Spend
    def arm_card(self, cardholder_email: str) -> str:
        """Issue (first time) or unfreeze (renewal) the card. Requires a valid PASS."""
        if not (self.last_pass and check_pass(self.last_pass, self.intent)):
            self.log("arm_refused", reason="no valid PASS")
            raise PermissionError("no valid PASS for the approved intent")
        if not self.cardholder_id:
            ch = self.client.create_cardholder(cardholder_email, rid())
            self.cardholder_id = ch["cardholder_id"]
            self.log("cardholder_created", cardholder_id=self.cardholder_id, status=ch.get("status"))
        if not self.card_id:
            payload = card_create_payload(self.intent, self.cardholder_id, rid(), self.approver)
            card = self.client.create_card(payload)
            self.card_id = card["card_id"]
            self.log("card_created", card_id=self.card_id, status=card.get("card_status"),
                     controls=payload["authorization_controls"])
        else:
            self.client.set_card_status(self.card_id, "ACTIVE", rid())
            self.log("card_unfrozen", card_id=self.card_id, pass_id=self.last_pass.pass_id)
        self.last_pass = None  # a PASS arms the card exactly once
        return self.card_id

    def freeze(self, reason: str):
        if self.card_id:
            self.client.set_card_status(self.card_id, "INACTIVE", rid())
            self.log("card_frozen", card_id=self.card_id, reason=reason)

    def charge(self, amount, currency, mcc, merchant, capture=True) -> dict:
        """Sandbox-only: simulate the vendor's charge hitting the card."""
        tx = self.client.sim_authorize(self.card_id, amount, currency, mcc, merchant)
        if capture and tx.get("status") != "FAILED" and not tx.get("failure_reason"):
            tx = self.client.sim_capture(tx["card_transaction_lifecycle_id"])
        self.log("charge", amount=amount, currency=currency, mcc=mcc,
                 status=tx.get("status"), type=tx.get("transaction_type"), failure_reason=tx.get("failure_reason"))
        return tx

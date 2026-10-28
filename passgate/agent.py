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
from dataclasses import dataclass, field, replace
from decimal import Decimal

from .airwallex.base import IssuingClient
from .approvals import Approval, ApprovalStore
from .decide import CashForecast, decide
from .intent import Intent, VendorTerms, money
from .policy import card_controls, card_create_payload
from .verifier import Verdict, check_pass, verify
from .webhook import charge_violations, verify_signature


def rid() -> str:
    return str(uuid.uuid4())


@dataclass
class PassGate:
    client: IssuingClient
    approver: str = "Finance approver"
    # A renewal price rise above this percent needs a human; at/below it auto-PASSes.
    reapproval_threshold_pct: Decimal = Decimal("10")
    audit: list[dict] = field(default_factory=list)
    cardholder_id: str | None = None
    card_id: str | None = None
    intent: Intent | None = None
    last_pass: Verdict | None = None
    approvals: ApprovalStore = field(default_factory=ApprovalStore)
    exceptions: list[dict] = field(default_factory=list)
    # approval_id -> (new_intent, new_terms, cardholder_email, raw) awaiting a human
    _pending_renewals: dict = field(default_factory=dict)

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
            # Renewal: if the approved terms changed, push the new per-charge / monthly
            # limits onto the card before unfreezing, so the card equals the new PASS.
            new_controls = card_controls(self.intent)
            current = self.client.get_card(self.card_id).get("authorization_controls")
            if current != new_controls:
                self.client.update_card_controls(self.card_id, new_controls, rid())
                self.log("card_limits_updated", card_id=self.card_id,
                         pass_id=self.last_pass.pass_id, controls=new_controls)
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

    # 4. Re-approval on renewal (price change)
    def _renewal_amount(self, terms: VendorTerms) -> Decimal:
        """The amount the renewal charges, projected onto the approved billing term.

        Mirrors verifier.evidence_fields so an auto-approved intent verifies cleanly.
        """
        term = (terms.billing_term or self.intent.billing_term).upper()
        if terms.amount_due is not None:
            return money(terms.amount_due)
        return money(terms.monthly_price if term == "MONTHLY" else (terms.annual_price or 0))

    @staticmethod
    def _rise_pct(old: Decimal, new: Decimal) -> Decimal:
        old, new = money(old), money(new)
        if old <= 0:
            return Decimal("100") if new > old else Decimal("0")
        return ((new - old) / old * 100).quantize(Decimal("0.1"))

    def renew(self, new_terms: VendorTerms, cardholder_email: str, raw: str = "") -> dict:
        """Handle a renewal whose price may have moved.

        Small change (<= threshold, or a price drop): auto re-approve, mint a fresh
        PASS, update the card limits, re-arm. Price rise above the threshold: queue a
        human approval and leave the card frozen until someone decides.
        """
        assert self.intent, "no approved intent to renew"
        old = money(self.intent.amount)
        new_amount = self._renewal_amount(new_terms)
        rise = self._rise_pct(old, new_amount)
        new_intent = replace(
            self.intent,
            vendor=new_terms.vendor,
            currency=new_terms.currency,
            amount=new_amount,
            merchant_category_code=new_terms.merchant_category_code,
            cancellation=new_terms.cancellation,
            rationale=[f"Renewal {self.intent.vendor}: {old} -> {new_amount} "
                       f"{new_terms.currency} ({rise:+}% vs approved)."],
        )
        if rise > self.reapproval_threshold_pct:
            self.freeze("price rise above re-approval threshold; awaiting human")
            a = self.approvals.add(
                kind="renewal_price_rise", vendor=new_intent.vendor,
                summary=(f"{new_intent.vendor} renewal up {rise}% "
                         f"({old} -> {new_amount} {new_intent.currency}); approve to update the card."),
                currency=new_intent.currency, old_amount=str(old), new_amount=str(new_amount),
                rise_pct=str(rise),
                detail={"threshold_pct": str(self.reapproval_threshold_pct),
                        "billing_term": new_intent.billing_term},
            )
            self._pending_renewals[a.id] = (new_intent, new_terms, cardholder_email, raw)
            self.log("reapproval_required", approval_id=a.id, rise_pct=str(rise),
                     threshold_pct=str(self.reapproval_threshold_pct),
                     old_amount=str(old), new_amount=str(new_amount))
            return {"status": "PENDING_APPROVAL", "approval_id": a.id, "rise_pct": str(rise)}

        self.log("reapproval_auto", rise_pct=str(rise),
                 threshold_pct=str(self.reapproval_threshold_pct),
                 old_amount=str(old), new_amount=str(new_amount))
        result = self._apply_renewal(new_intent, new_terms, cardholder_email, raw,
                                     approved_by="auto-reapproval (within threshold)")
        result["rise_pct"] = str(rise)
        return result

    def _apply_renewal(self, new_intent: Intent, new_terms: VendorTerms,
                       cardholder_email: str, raw: str, approved_by: str) -> dict:
        self.approve(new_intent, approved_by)
        v = self.verify(new_terms, raw)
        if not v.ok:
            return {"status": "VERIFY_FAILED", "diff": v.diff}
        card = self.arm_card(cardholder_email)
        return {"status": "APPROVED", "card_id": card, "amount": str(new_intent.amount),
                "limits": self.client.get_card_limits(card)}

    def decide_approval(self, approval_id: str, approve: bool, by: str) -> dict:
        """Apply a human decision from the approval screen."""
        ctx = self._pending_renewals.pop(approval_id, None)
        if not approve:
            self.approvals.reject(approval_id, by)
            self.log("reapproval_rejected", approval_id=approval_id, by=by)
            return {"status": "REJECTED", "approval_id": approval_id}
        self.approvals.approve(approval_id, by)
        self.log("reapproval_approved", approval_id=approval_id, by=by)
        if ctx is None:
            return {"status": "APPROVED", "approval_id": approval_id}
        new_intent, new_terms, email, raw = ctx
        result = self._apply_renewal(new_intent, new_terms, email, raw, approved_by=by)
        result["approval_id"] = approval_id
        return result

    # 5. Webhook-style alert handler
    def handle_webhook(self, event: dict, *, secret=None, timestamp=None,
                       signature=None, raw_body=None) -> dict:
        """Inspect an inbound issuing-transaction event; freeze + log on any breach.

        If `secret` is given, the HMAC signature (x-timestamp + raw_body) must verify
        or the event is rejected unprocessed. A charge over the approved amount, from a
        different merchant category, or in a disallowed currency freezes the card and
        records an exception for a human.
        """
        if secret is not None:
            body = raw_body if raw_body is not None else json.dumps(event, sort_keys=True)
            if not verify_signature(secret, timestamp, body, signature):
                self.log("webhook_rejected", reason="bad_signature")
                return {"action": "REJECTED", "reason": "bad_signature"}

        data = event.get("data", event)
        card_id = data.get("card_id") or self.card_id
        amount = data.get("amount") or data.get("transaction_amount") or 0
        currency = data.get("currency") or data.get("transaction_currency") or ""
        merchant = data.get("merchant") or {}
        mcc = (merchant.get("category_code") if isinstance(merchant, dict) else None) \
            or data.get("merchant_category_code") or ""
        merchant_name = merchant.get("name") if isinstance(merchant, dict) else data.get("merchant_name")

        try:
            controls = self.client.get_card(card_id)["authorization_controls"]
        except Exception:  # noqa: BLE001 - unknown card: nothing to enforce against
            self.log("webhook_ignored", reason="unknown_card", card_id=card_id)
            return {"action": "IGNORED", "reason": "unknown_card"}

        violations = charge_violations(controls, amount, currency, mcc)
        if not violations:
            self.log("webhook_ok", card_id=card_id, amount=amount, currency=currency, mcc=mcc)
            return {"action": "OK", "violations": []}

        self.freeze("webhook alert: " + ", ".join(violations))
        exc = {
            "ts": round(time.time(), 3), "card_id": card_id, "violations": violations,
            "amount": amount, "currency": currency, "mcc": mcc, "merchant": merchant_name,
            "event": event.get("name") or event.get("type"),
        }
        self.exceptions.append(exc)
        self.log("webhook_exception", **exc)
        return {"action": "FROZEN", "violations": violations, "exception": exc}

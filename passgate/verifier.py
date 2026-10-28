"""Verify: deterministic comparison of evidence (quote/invoice) against the approved intent.

A match yields a signed PASS bound to a hash of the terms. A mismatch yields a FAIL
with a field-by-field diff for a human. No model is involved here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field

from .intent import Intent, VendorTerms, money


def _canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()


def terms_hash(fields: dict) -> str:
    return hashlib.sha256(_canon(fields)).hexdigest()


def _signing_key() -> bytes:
    k = os.environ.get("PASSGATE_SIGNING_KEY")
    if k:
        return k.encode()
    # Ephemeral per-process key for local/demo runs; never printed.
    global _EPHEMERAL
    try:
        return _EPHEMERAL
    except NameError:
        _EPHEMERAL = secrets.token_bytes(32)
        return _EPHEMERAL


@dataclass
class Verdict:
    result: str                     # PASS | FAIL
    intent_fields: dict
    evidence_fields: dict
    diff: dict = field(default_factory=dict)  # field -> {"approved": x, "evidence": y}
    terms_hash: str | None = None
    evidence_hash: str | None = None
    pass_id: str | None = None
    issued_at: int | None = None
    signature: str | None = None

    @property
    def ok(self) -> bool:
        return self.result == "PASS"

    def record(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def evidence_fields(ev: VendorTerms, intent: Intent) -> dict:
    """Project the evidence onto the fields the intent is bound to."""
    term = (ev.billing_term or intent.billing_term).upper()
    if ev.amount_due is not None:
        amount = ev.amount_due
    else:
        amount = ev.monthly_price if term == "MONTHLY" else (ev.annual_price or money(0))
    return {
        "vendor": ev.vendor.strip().lower(),
        "currency": ev.currency.upper(),
        "billing_term": term,
        "amount": str(money(amount)),
        "merchant_category_code": ev.merchant_category_code,
        "cancellation": ev.cancellation.lower(),
    }


def verify(intent: Intent, evidence: VendorTerms, evidence_raw: str = "") -> Verdict:
    want = intent.bound_fields()
    got = evidence_fields(evidence, intent)
    diff = {k: {"approved": want[k], "evidence": got.get(k)} for k in want if want[k] != got.get(k)}
    ev_hash = hashlib.sha256(evidence_raw.encode()).hexdigest() if evidence_raw else None
    if diff:
        return Verdict("FAIL", want, got, diff=diff, evidence_hash=ev_hash)
    th = terms_hash(want)
    pid = str(uuid.uuid4())
    ts = int(time.time())
    msg = _canon({"pass_id": pid, "terms_hash": th, "evidence_hash": ev_hash, "issued_at": ts})
    sig = hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()
    return Verdict("PASS", want, got, terms_hash=th, evidence_hash=ev_hash,
                   pass_id=pid, issued_at=ts, signature=sig)


def check_pass(v: Verdict, intent: Intent) -> bool:
    """Re-verify a PASS before any card authority is granted."""
    if not v.ok or v.signature is None:
        return False
    if terms_hash(intent.bound_fields()) != v.terms_hash:
        return False
    msg = _canon({"pass_id": v.pass_id, "terms_hash": v.terms_hash,
                  "evidence_hash": v.evidence_hash, "issued_at": v.issued_at})
    exp = hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(exp, v.signature)

"""Webhook-style alert handler for inbound issuing-transaction events.

Mirrors the shape of Airwallex `issuing.transaction.*` webhooks. Two pure helpers
here; the side effects (freeze card, record exception) live on the agent so they go
through the same audit log and kill-switch as everything else.

Signature scheme matches Airwallex: HMAC-SHA256 over (x-timestamp + raw_body),
compared in constant time, with a replay window on the timestamp.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal


def verify_signature(secret, timestamp, raw_body, signature,
                     *, tolerance_s: int = 300, now: float | None = None) -> bool:
    """True iff `signature` is a valid, in-window HMAC for this body.

    secret/raw_body may be str or bytes; timestamp is the x-timestamp header value.
    """
    if not secret or not signature:
        return False
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return False
    now = time.time() if now is None else now
    if abs(now - ts) > tolerance_s:
        return False
    key = secret.encode() if isinstance(secret, str) else secret
    body = raw_body.encode() if isinstance(raw_body, str) else raw_body
    msg = str(timestamp).encode() + body
    expected = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def charge_violations(controls: dict, amount, currency: str, mcc: str) -> list[str]:
    """Which approved controls this charge breaks (empty list == within approval).

    Checks the same dimensions the card itself enforces, so the webhook alert and a
    live decline agree: approved amount (PER_TRANSACTION), merchant category, currency.
    """
    violations: list[str] = []
    amt = Decimal(str(amount))

    allowed_cur = controls.get("allowed_currencies")
    if allowed_cur and currency not in allowed_cur:
        violations.append("CURRENCY_NOT_ALLOWED")

    allowed_mcc = controls.get("allowed_merchant_categories")
    if allowed_mcc and str(mcc) not in [str(m) for m in allowed_mcc]:
        violations.append("MERCHANT_CATEGORY_NOT_ALLOWED")

    for lim in controls.get("transaction_limits", {}).get("limits", []):
        if lim.get("interval") == "PER_TRANSACTION" and amt > Decimal(str(lim["amount"])):
            violations.append("AMOUNT_EXCEEDS_APPROVED")
            break

    return violations

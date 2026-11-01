"""End-to-end demo (mock by default; PASSGATE_BACKEND=rest for the real sandbox).

  python3 -m passgate.demo            # mock
  PASSGATE_BACKEND=rest python3 -m passgate.demo
"""
from __future__ import annotations

import json
import time
import sys

from .agent import PassGate
from .airwallex import make_client
from .intent import parse_terms_text

QUOTE = """Vendor: Acme Analytics
Currency: USD
Monthly price: $1,200.00
Annual price: $11,808.00
MCC: 5734
Cancellation: anytime
"""
INVOICE_M1 = QUOTE + "Billing term: MONTHLY\nAmount due: 1200.00\n"
INVOICE_M2 = QUOTE.replace("$1,200.00", "$1,380.00") + "Billing term: MONTHLY\nAmount due: 1380.00\n"
# Renewal invoices for the re-approval demo:
INVOICE_SMALL = QUOTE.replace("$1,200.00", "$1,248.00") + "Billing term: MONTHLY\nAmount due: 1248.00\n"   # +4% vs 1200
INVOICE_BIG = QUOTE.replace("$1,200.00", "$1,435.20") + "Billing term: MONTHLY\nAmount due: 1435.20\n"      # +15% vs 1248


def say(*a):
    print(*a, flush=True)


def main(backend: str | None = None) -> int:
    client = make_client(backend)
    pg = PassGate(client)
    say(f"== backend: {client.name}")
    terms = parse_terms_text(QUOTE)

    say("\n== 1. DECIDE")
    # Keep the same story on any backend: reserve floor sits 14,000 below live cash
    # (mock: 64,000 cash / 50,000 floor). A large sandbox balance would otherwise
    # make annual "safe", and the monthly invoices below would correctly FAIL.
    cash = client.get_balance(terms.currency)
    floor = max(float(cash) - 14000, 0)
    intent, analysis = pg.propose(terms, weekly_net_outflow=350, reserve_floor=floor)
    for line in intent.rationale:
        say("  -", line)
    say("  options:", json.dumps(analysis, default=str))
    pg.approve(intent, "Toni (demo approver)")

    say("\n== 2. VERIFY month-1 invoice")
    v = pg.verify(parse_terms_text(INVOICE_M1), INVOICE_M1)
    say(f"  {v.result} terms_hash={(v.terms_hash or '-')[:16]}... pass_id={v.pass_id}")
    if not v.ok:
        say("  diff:", json.dumps(v.diff, default=str))
        return 1

    say("\n== 3. SPEND")
    card = pg.arm_card(f"passgate-agent+{int(time.time())}@example.com")  # sandbox rejects reused cardholder emails
    say("  card:", card, "limits:", json.dumps(client.get_card_limits(card)))
    ok = pg.charge(1200.00, "USD", "5734", "Acme Analytics")
    say(f"  exact amount      -> {ok.get('status')} {ok.get('transaction_type')}")
    for label, args in [("$0.01 over", (1200.01, "USD", "5734")),
                        ("EUR", (1200.00, "EUR", "5734")),
                        ("other MCC 5812", (10.00, "USD", "5812"))]:
        t = pg.charge(*args, "Acme Analytics", capture=False)
        say(f"  {label:<17} -> {t.get('status')} {t.get('failure_reason')}")
    pg.freeze("cleared; re-freeze until next PASS")

    say(f"\n== 4. MONTH 2: small price rise (+4%, <= {pg.reapproval_threshold_pct}% threshold) -> auto re-approval")
    r = pg.renew(parse_terms_text(INVOICE_SMALL), "passgate-agent@example.com", INVOICE_SMALL)
    say(f"  rise {r['rise_pct']}% -> {r['status']}  new per-charge/monthly limits:",
        json.dumps(client.get_card_limits(card)))
    # Per-charge cap moved 1200 -> 1248: a charge just over the NEW cap still declines.
    say("  $0.01 over new cap ->", pg.charge(1248.01, "USD", "5734", "Acme Analytics", capture=False)["failure_reason"])
    pg.freeze("cleared; re-freeze until next PASS")

    say(f"\n== 5. MONTH 3: big price rise (+15%, > {pg.reapproval_threshold_pct}% threshold) -> human approval")
    r = pg.renew(parse_terms_text(INVOICE_BIG), "passgate-agent@example.com", INVOICE_BIG)
    say(f"  rise {r['rise_pct']}% -> {r['status']}  card={client.get_card(card)['card_status']} (frozen, no auto-PASS)")
    pend = pg.approvals.pending()[0]
    say("  pending approval:", pend.summary)
    say("  (human opens the approval screen: python3 -m passgate.approval_server)")
    done = pg.decide_approval(pend.id, approve=True, by="Toni (demo approver)")
    say(f"  human approved -> {done['status']}  updated limits:", json.dumps(done["limits"]))

    say("\n== 6. WEBHOOK ALERT: a charge arrives that exceeds the approved amount")
    event = {"name": "issuing.transaction.failed",
             "data": {"card_id": card, "amount": 5000.00, "currency": "USD",
                      "merchant": {"name": "Acme Analytics", "category_code": "5734"}}}
    res = pg.handle_webhook(event)
    say(f"  {res['action']} {res['violations']}  card={client.get_card(card)['card_status']}  exceptions={len(pg.exceptions)}")

    say("\n== audit log steps:", [a["step"] for a in pg.audit])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))

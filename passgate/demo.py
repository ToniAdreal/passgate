"""End-to-end demo (mock by default; PASSGATE_BACKEND=rest for the real sandbox).

  python3 -m passgate.demo            # mock
  PASSGATE_BACKEND=rest python3 -m passgate.demo
"""
from __future__ import annotations

import json
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


def say(*a):
    print(*a, flush=True)


def main(backend: str | None = None) -> int:
    client = make_client(backend)
    pg = PassGate(client)
    say(f"== backend: {client.name}")
    terms = parse_terms_text(QUOTE)

    say("\n== 1. DECIDE")
    intent, analysis = pg.propose(terms, weekly_net_outflow=350, reserve_floor=50000)
    for line in intent.rationale:
        say("  -", line)
    say("  options:", json.dumps(analysis, default=str))
    pg.approve(intent, "Toni (demo approver)")

    say("\n== 2. VERIFY month-1 invoice")
    v = pg.verify(parse_terms_text(INVOICE_M1), INVOICE_M1)
    say(f"  {v.result} terms_hash={v.terms_hash[:16]}... pass_id={v.pass_id}")

    say("\n== 3. SPEND")
    card = pg.arm_card("passgate-agent@example.com")
    say("  card:", card, "limits:", json.dumps(client.get_card_limits(card)))
    ok = pg.charge(1200.00, "USD", "5734", "Acme Analytics")
    say(f"  exact amount      -> {ok.get('status')} {ok.get('transaction_type')}")
    for label, args in [("$0.01 over", (1200.01, "USD", "5734")),
                        ("EUR", (1200.00, "EUR", "5734")),
                        ("other MCC 5812", (10.00, "USD", "5812"))]:
        t = pg.charge(*args, "Acme Analytics", capture=False)
        say(f"  {label:<17} -> {t.get('status')} {t.get('failure_reason')}")
    pg.freeze("cleared; re-freeze until next PASS")

    say("\n== 4. MONTH 2: vendor raises price")
    v2 = pg.verify(parse_terms_text(INVOICE_M2), INVOICE_M2)
    say(f"  {v2.result} diff={json.dumps(v2.diff)}  card={client.get_card(card)['card_status']}")
    try:
        pg.arm_card("passgate-agent@example.com")
    except PermissionError as e:
        say("  arm refused:", e)
    say("  -> escalated to human; human re-approves at 1380.00")
    from dataclasses import replace
    from .intent import money
    pg.approve(replace(intent, amount=money("1380.00")), "Toni (demo approver)")
    # NB: in production the card's limits would be updated via /issuing/cards/{id}/update here
    v3 = pg.verify(parse_terms_text(INVOICE_M2), INVOICE_M2)
    say(f"  re-verify: {v3.result}")

    say("\n== audit log steps:", [a["step"] for a in pg.audit])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))

"""Day-1 sandbox capability check (REAL Airwallex sandbox; sandbox money only).

Answers: can this (China-registered) sandbox account create a cardholder + virtual
card via API, and do card controls decline as documented?
Prints only IDs/statuses/failure reasons. Never prints keys or tokens or PAN/CVV.

  cp .env.example .env   # fill AIRWALLEX_CLIENT_ID / AIRWALLEX_API_KEY
  python3 scripts/day1_check.py
"""
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from passgate.airwallex.base import AirwallexError  # noqa: E402
from passgate.airwallex.rest import RestIssuingClient  # noqa: E402
from passgate.intent import Intent, money  # noqa: E402
from passgate.policy import card_create_payload  # noqa: E402

out = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}


def step(name, fn):
    try:
        r = fn()
        out[name] = {"ok": True, **(r if isinstance(r, dict) else {"result": r})}
    except AirwallexError as e:
        out[name] = {"ok": False, "status": e.status, "code": e.code, "message": e.message}
    except Exception as e:  # noqa: BLE001
        out[name] = {"ok": False, "error": type(e).__name__, "message": str(e)[:200]}
    print(name, json.dumps(out[name]), flush=True)
    return out[name]


c = RestIssuingClient.from_env()
step("auth", lambda: (c._auth(), {"token": "obtained"})[1])
step("balance_usd", lambda: {"available": c.get_balance("USD")})
step("balance_cny", lambda: {"available": c.get_balance("CNY")})
ch = step("create_cardholder", lambda: {k: v for k, v in c.create_cardholder(
    "passgate-agent@example.com", str(uuid.uuid4())).items() if k in ("cardholder_id", "status", "type")})
if ch.get("ok"):
    time.sleep(3)
    intent = Intent("Acme Analytics", "USD", "MONTHLY", money("1200"), "5734", "anytime")
    card = step("create_card", lambda: {k: v for k, v in c.create_card(card_create_payload(
        intent, ch["cardholder_id"], str(uuid.uuid4()), "PassGate Day1")).items()
        if k in ("card_id", "card_status", "form_factor", "brand")})
    if card.get("ok"):
        cid = card["card_id"]
        time.sleep(3)
        step("get_card", lambda: {k: v for k, v in c.get_card(cid).items() if k in ("card_status",)})
        step("limits", lambda: c.get_card_limits(cid))
        for label, amt, cur, mcc in [("sim_exact", 1200.00, "USD", "5734"), ("sim_over", 1200.01, "USD", "5734"),
                                     ("sim_eur", 100.00, "EUR", "5734"), ("sim_mcc", 10.00, "USD", "5812")]:
            r = step(label, lambda: {k: v for k, v in c.sim_authorize(cid, amt, cur, mcc, "Acme Analytics").items()
                                     if k in ("status", "failure_reason", "transaction_type", "card_transaction_lifecycle_id")})
            if label == "sim_exact" and r.get("ok") and r.get("card_transaction_lifecycle_id"):
                step("sim_capture", lambda: {k: v for k, v in c.sim_capture(r["card_transaction_lifecycle_id"]).items()
                                             if k in ("status", "transaction_type")})
        time.sleep(3)
        step("transactions", lambda: {"items": [{k: t.get(k) for k in ("transaction_type", "status", "failure_reason", "transaction_amount", "transaction_currency")} for t in c.list_transactions(cid)]})
        step("freeze", lambda: {k: v for k, v in c.set_card_status(cid, "INACTIVE", str(uuid.uuid4())).items() if k == "card_status"})

Path("runs").mkdir(exist_ok=True)
p = Path("runs") / f"day1_{int(time.time())}.json"
p.write_text(json.dumps(out, indent=2))
print("saved", p)

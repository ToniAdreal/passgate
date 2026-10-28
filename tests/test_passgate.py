import json
import threading
import unittest
import urllib.request
from dataclasses import replace
from decimal import Decimal

from passgate.agent import PassGate
from passgate.airwallex.mock import MockIssuingClient
from passgate.approval_server import make_server
from passgate.approvals import ApprovalStore
from passgate.demo import INVOICE_M1, INVOICE_M2, INVOICE_SMALL, QUOTE
from passgate.intent import money, parse_terms_text
from passgate.verifier import check_pass, verify
from passgate.webhook import charge_violations, verify_signature


def armed():
    pg = PassGate(MockIssuingClient({"USD": 64000}))
    intent, _ = pg.propose(parse_terms_text(QUOTE), 350, 50000)
    pg.approve(intent)
    assert pg.verify(parse_terms_text(INVOICE_M1), INVOICE_M1).ok
    pg.arm_card("a@example.com")
    return pg, intent


class T(unittest.TestCase):
    def test_decide_monthly_when_annual_breaks_reserve(self):
        pg = PassGate(MockIssuingClient({"USD": 64000}))
        intent, a = pg.propose(parse_terms_text(QUOTE), 350, 50000)
        self.assertEqual(intent.billing_term, "MONTHLY")
        self.assertEqual(a["annual"].first_breach_week if hasattr(a["annual"], "first_breach_week") else a["annual"]["first_breach_week"], 7)

    def test_decide_annual_when_cash_is_ample(self):
        pg = PassGate(MockIssuingClient({"USD": 200000}))
        intent, _ = pg.propose(parse_terms_text(QUOTE), 350, 50000)
        self.assertEqual(intent.billing_term, "ANNUAL")
        self.assertEqual(intent.amount, money("11808"))

    def test_verify_fail_diff_and_pass_binding(self):
        pg = PassGate(MockIssuingClient())
        intent, _ = pg.propose(parse_terms_text(QUOTE), 350, 50000)
        v = verify(intent, parse_terms_text(INVOICE_M2), INVOICE_M2)
        self.assertEqual(v.result, "FAIL")
        self.assertEqual(set(v.diff), {"amount"})
        ok = verify(intent, parse_terms_text(INVOICE_M1), INVOICE_M1)
        self.assertTrue(check_pass(ok, intent))
        self.assertFalse(check_pass(ok, replace(intent, amount=money("1200.01"))))
        ok.signature = "0" * 64
        self.assertFalse(check_pass(ok, intent))

    def test_no_pass_no_card(self):
        pg = PassGate(MockIssuingClient())
        intent, _ = pg.propose(parse_terms_text(QUOTE), 350, 50000)
        pg.approve(intent)
        with self.assertRaises(PermissionError):
            pg.arm_card("a@example.com")
        self.assertIsNone(pg.card_id)

    def test_controls_and_declines(self):
        pg, _ = armed()
        self.assertEqual(pg.charge(1200.00, "USD", "5734", "Acme")["transaction_type"], "CLEARING")
        self.assertEqual(pg.charge(1200.01, "USD", "5734", "Acme", capture=False)["failure_reason"], "LIMIT_EXCEEDED")
        self.assertEqual(pg.charge(1, "EUR", "5734", "Acme", capture=False)["failure_reason"], "CURRENCY_NOT_ALLOWED")
        self.assertEqual(pg.charge(1, "USD", "5812", "X", capture=False)["failure_reason"], "MERCHANT_CATEGORY_NOT_ALLOWED")

    def test_per_transaction_is_inclusive_and_monthly_is_cumulative(self):
        pg, _ = armed()
        self.assertIsNone(pg.charge(600, "USD", "5734", "Acme")["failure_reason"])
        self.assertIsNone(pg.charge(600, "USD", "5734", "Acme")["failure_reason"])
        self.assertEqual(pg.charge(0.01, "USD", "5734", "Acme", capture=False)["failure_reason"], "LIMIT_EXCEEDED")

    def test_fail_freezes_and_pass_is_single_use(self):
        pg, _ = armed()
        pg.freeze("cleared")
        self.assertEqual(pg.charge(1, "USD", "5734", "Acme", capture=False)["failure_reason"], "CARD_INACTIVE")
        self.assertFalse(pg.verify(parse_terms_text(INVOICE_M2), INVOICE_M2).ok)
        with self.assertRaises(PermissionError):
            pg.arm_card("a@example.com")
        self.assertTrue(pg.verify(parse_terms_text(INVOICE_M1), INVOICE_M1).ok)
        pg.arm_card("a@example.com")
        with self.assertRaises(PermissionError):  # PASS consumed
            pg.arm_card("a@example.com")


class Renewal(unittest.TestCase):
    def test_small_rise_auto_reapproves_and_updates_limits(self):
        pg, intent = armed()
        pg.charge(1200.00, "USD", "5734", "Acme")  # spend, then freeze (as in real month end)
        pg.freeze("cleared")
        self.assertEqual(pg.intent.amount, money("1200.00"))
        r = pg.renew(parse_terms_text(INVOICE_SMALL), "a@example.com", INVOICE_SMALL)  # +4%
        self.assertEqual(r["status"], "APPROVED")
        self.assertEqual(pg.intent.amount, money("1248.00"))
        # card is live again and both limits were pushed to the new amount
        self.assertEqual(pg.client.get_card(pg.card_id)["card_status"], "ACTIVE")
        limits = {l["interval"]: l["amount"] for l in pg.client.get_card_limits(pg.card_id)["limits"]}
        self.assertEqual(limits["PER_TRANSACTION"], 1248.0)
        self.assertEqual(limits["MONTHLY"], 1248.0)
        self.assertIn("card_limits_updated", [a["step"] for a in pg.audit])

    def test_big_rise_requires_human_and_stays_frozen(self):
        pg, _ = armed()
        pg.freeze("cleared")
        # +15% vs 1200 (> 10% default threshold)
        inv = QUOTE.replace("$1,200.00", "$1,380.00") + "Billing term: MONTHLY\nAmount due: 1380.00\n"
        r = pg.renew(parse_terms_text(inv), "a@example.com", inv)
        self.assertEqual(r["status"], "PENDING_APPROVAL")
        self.assertEqual(pg.intent.amount, money("1200.00"))          # intent NOT changed yet
        self.assertEqual(pg.client.get_card(pg.card_id)["card_status"], "INACTIVE")
        self.assertEqual(len(pg.approvals.pending()), 1)
        limits = {l["interval"]: l["amount"] for l in pg.client.get_card_limits(pg.card_id)["limits"]}
        self.assertEqual(limits["PER_TRANSACTION"], 1200.0)           # limits NOT changed yet

    def test_human_approval_applies_renewal(self):
        pg, _ = armed()
        pg.freeze("cleared")
        inv = QUOTE.replace("$1,200.00", "$1,380.00") + "Billing term: MONTHLY\nAmount due: 1380.00\n"
        r = pg.renew(parse_terms_text(inv), "a@example.com", inv)
        res = pg.decide_approval(r["approval_id"], approve=True, by="Toni")
        self.assertEqual(res["status"], "APPROVED")
        self.assertEqual(pg.intent.amount, money("1380.00"))
        self.assertEqual(pg.client.get_card(pg.card_id)["card_status"], "ACTIVE")
        self.assertEqual(pg.approvals.get(r["approval_id"]).status, "APPROVED")

    def test_human_rejection_keeps_card_frozen(self):
        pg, _ = armed()
        pg.freeze("cleared")
        inv = QUOTE.replace("$1,200.00", "$1,380.00") + "Billing term: MONTHLY\nAmount due: 1380.00\n"
        r = pg.renew(parse_terms_text(inv), "a@example.com", inv)
        res = pg.decide_approval(r["approval_id"], approve=False, by="Toni")
        self.assertEqual(res["status"], "REJECTED")
        self.assertEqual(pg.intent.amount, money("1200.00"))
        self.assertEqual(pg.client.get_card(pg.card_id)["card_status"], "INACTIVE")
        self.assertEqual(pg.approvals.get(r["approval_id"]).status, "REJECTED")

    def test_configurable_threshold(self):
        pg, _ = armed()
        pg.reapproval_threshold_pct = Decimal("20")   # now +15% is within tolerance
        pg.freeze("cleared")
        r = pg.renew(parse_terms_text(INVOICE_SMALL), "a@example.com", INVOICE_SMALL)
        self.assertEqual(r["status"], "APPROVED")


class Webhook(unittest.TestCase):
    def test_over_amount_freezes_and_records_exception(self):
        pg, _ = armed()
        event = {"name": "issuing.transaction.failed",
                 "data": {"card_id": pg.card_id, "amount": 5000, "currency": "USD",
                          "merchant": {"name": "Acme", "category_code": "5734"}}}
        res = pg.handle_webhook(event)
        self.assertEqual(res["action"], "FROZEN")
        self.assertIn("AMOUNT_EXCEEDS_APPROVED", res["violations"])
        self.assertEqual(pg.client.get_card(pg.card_id)["card_status"], "INACTIVE")
        self.assertEqual(len(pg.exceptions), 1)

    def test_wrong_merchant_category_freezes(self):
        pg, _ = armed()
        event = {"data": {"card_id": pg.card_id, "amount": 10, "currency": "USD",
                          "merchant": {"name": "Casino", "category_code": "7995"}}}
        res = pg.handle_webhook(event)
        self.assertEqual(res["action"], "FROZEN")
        self.assertIn("MERCHANT_CATEGORY_NOT_ALLOWED", res["violations"])

    def test_clean_charge_leaves_card_active(self):
        pg, _ = armed()
        event = {"data": {"card_id": pg.card_id, "amount": 1200, "currency": "USD",
                          "merchant": {"name": "Acme", "category_code": "5734"}}}
        res = pg.handle_webhook(event)
        self.assertEqual(res["action"], "OK")
        self.assertEqual(pg.client.get_card(pg.card_id)["card_status"], "ACTIVE")
        self.assertEqual(pg.exceptions, [])

    def test_signature_gate(self):
        pg, _ = armed()
        secret, ts = "shh", 1000.0
        event = {"data": {"card_id": pg.card_id, "amount": 5000, "currency": "USD",
                          "merchant": {"category_code": "5734"}}}
        body = json.dumps(event, sort_keys=True)
        import hashlib
        import hmac
        good = hmac.new(secret.encode(), str(ts).encode() + body.encode(), hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature(secret, ts, body, good, now=ts))
        self.assertFalse(verify_signature(secret, ts, body, "deadbeef", now=ts))
        self.assertFalse(verify_signature(secret, ts, body, good, now=ts + 10_000))  # replay window
        # bad signature => rejected, card untouched
        res = pg.handle_webhook(event, secret=secret, timestamp=ts, signature="nope", raw_body=body)
        self.assertEqual(res["action"], "REJECTED")
        self.assertEqual(pg.client.get_card(pg.card_id)["card_status"], "ACTIVE")

    def test_charge_violations_helper(self):
        controls = {"allowed_currencies": ["USD"], "allowed_merchant_categories": ["5734"],
                    "transaction_limits": {"currency": "USD",
                                           "limits": [{"interval": "PER_TRANSACTION", "amount": 1200.0}]}}
        self.assertEqual(charge_violations(controls, 1200, "USD", "5734"), [])
        self.assertEqual(charge_violations(controls, 1200.01, "USD", "5734"), ["AMOUNT_EXCEEDS_APPROVED"])
        self.assertIn("CURRENCY_NOT_ALLOWED", charge_violations(controls, 10, "EUR", "5734"))


class ApprovalStoreT(unittest.TestCase):
    def test_lifecycle(self):
        s = ApprovalStore()
        a = s.add(kind="renewal_price_rise", vendor="Acme", summary="up")
        self.assertEqual(s.pending(), [a])
        s.approve(a.id, "Toni")
        self.assertEqual(s.pending(), [])
        self.assertEqual(s.get(a.id).status, "APPROVED")
        with self.assertRaises(ValueError):
            s.reject(a.id, "Toni")  # already decided
        with self.assertRaises(KeyError):
            s.approve("missing", "Toni")


class ApprovalServer(unittest.TestCase):
    def test_http_list_and_decide(self):
        calls = []

        def on_decide(aid, approve, by):
            calls.append((aid, approve, by))
            return {"status": "APPROVED" if approve else "REJECTED", "approval_id": aid}

        store = ApprovalStore()
        a = store.add(kind="renewal_price_rise", vendor="Acme", summary="up 15%",
                      currency="USD", old_amount="1200.00", new_amount="1380.00", rise_pct="15.0")
        srv = make_server(store, on_decide=on_decide, port=0)
        host, port = srv.server_address
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://{host}:{port}"
            page = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn("PassGate", page)
            data = json.loads(urllib.request.urlopen(base + "/api/approvals").read())
            self.assertEqual(len(data["pending"]), 1)
            self.assertEqual(data["pending"][0]["vendor"], "Acme")
            req = urllib.request.Request(
                base + "/api/decide",
                data=json.dumps({"id": a.id, "decision": "approve", "by": "Toni"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            out = json.loads(urllib.request.urlopen(req).read())
            self.assertEqual(out["status"], "APPROVED")
            self.assertEqual(calls, [(a.id, True, "Toni")])
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()

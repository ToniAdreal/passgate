import unittest
from dataclasses import replace

from passgate.agent import PassGate
from passgate.airwallex.mock import MockIssuingClient
from passgate.demo import INVOICE_M1, INVOICE_M2, QUOTE
from passgate.intent import money, parse_terms_text
from passgate.verifier import check_pass, verify


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


if __name__ == "__main__":
    unittest.main()

"""Offline tests for LLM-proposed / verifier-decided invoice extraction.

Every test forces the deterministic heuristic path (`allow_llm=False`) so the
suite never touches the network, regardless of whether ANTHROPIC_API_KEY happens
to be set in the environment.
"""
import os
import unittest
from decimal import Decimal

from passgate.agent import PassGate
from passgate.airwallex.mock import MockIssuingClient
from passgate.demo import QUOTE
from passgate.extract import (
    DEFAULT_MODEL,
    Extraction,
    extract,
    extract_terms,
    heuristic_terms,
    read_document,
    _terms_from_dict,
)
from passgate.intent import VendorTerms, money, parse_terms_text
from passgate.verifier import verify

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples")


def sample(name: str) -> str:
    with open(os.path.join(SAMPLES, name), encoding="utf-8") as f:
        return f.read()


def approved_intent():
    """The demo's approved intent (MONTHLY $1,200 Acme Analytics, USD, MCC 5734)."""
    pg = PassGate(MockIssuingClient({"USD": 64000}))
    intent, _ = pg.propose(parse_terms_text(QUOTE), 350, 50000)
    return intent


class ExtractOffline(unittest.TestCase):
    def test_returns_vendorterms_never_a_verdict(self):
        terms = extract_terms(sample("invoice_match.txt"), allow_llm=False)
        self.assertIsInstance(terms, VendorTerms)

    def test_heuristic_is_the_offline_source(self):
        ex = extract(sample("invoice_match.txt"), allow_llm=False)
        self.assertIsInstance(ex, Extraction)
        self.assertEqual(ex.source, "heuristic")
        self.assertIsNone(ex.model)

    def test_matching_invoice_extracts_expected_fields(self):
        t = heuristic_terms(sample("invoice_match.txt"))
        self.assertEqual(t.vendor, "Acme Analytics")
        self.assertEqual(t.currency, "USD")
        self.assertEqual(t.monthly_price, money("1200"))
        self.assertEqual(t.annual_price, money("11808"))
        self.assertEqual(t.merchant_category_code, "5734")
        self.assertEqual(t.cancellation, "anytime")
        self.assertEqual(t.billing_term, "MONTHLY")
        self.assertEqual(t.amount_due, money("1200"))

    def test_matching_invoice_passes_the_verifier(self):
        intent = approved_intent()
        raw = sample("invoice_match.txt")
        v = verify(intent, extract_terms(raw, allow_llm=False), raw)
        self.assertEqual(v.result, "PASS")

    def test_changed_amount_fails_on_amount(self):
        intent = approved_intent()
        raw = sample("invoice_amount_changed.txt")
        v = verify(intent, extract_terms(raw, allow_llm=False), raw)
        self.assertEqual(v.result, "FAIL")
        self.assertIn("amount", v.diff)
        self.assertEqual(v.diff["amount"]["evidence"], "1380.00")

    def test_changed_term_fails_on_billing_term(self):
        intent = approved_intent()
        raw = sample("invoice_term_changed.txt")
        v = verify(intent, extract_terms(raw, allow_llm=False), raw)
        self.assertEqual(v.result, "FAIL")
        self.assertIn("billing_term", v.diff)
        self.assertEqual(v.diff["billing_term"]["evidence"], "ANNUAL")


class DictValidation(unittest.TestCase):
    def test_coerces_model_style_strings(self):
        t = _terms_from_dict({
            "vendor": " Acme Analytics ",
            "currency": "usd",
            "monthly_price": "$1,200.00",
            "annual_price": "11808",
            "merchant_category_code": "5734",
            "cancellation": "Anytime",
            "billing_term": "monthly",
            "amount_due": "1200.00",
        })
        self.assertEqual(t.vendor, "Acme Analytics")
        self.assertEqual(t.currency, "USD")
        self.assertEqual(t.monthly_price, money("1200"))
        self.assertEqual(t.billing_term, "MONTHLY")
        self.assertEqual(t.cancellation, "anytime")

    def test_missing_required_field_raises(self):
        with self.assertRaises(ValueError):
            _terms_from_dict({"vendor": "Acme", "currency": "USD", "monthly_price": None})


class EmailBody(unittest.TestCase):
    def test_extracts_from_free_form_email_body(self):
        body = (
            "Hi there,\n\n"
            "Vendor: Acme Analytics\n"
            "Here is your renewal. The Monthly price is $1,200.00 (annual price $11,808.00).\n"
            "Billing term: Monthly. Currency: USD. MCC: 5734.\n"
            "You can cancel anytime. Amount due: $1,200.00.\n\nThanks!"
        )
        intent = approved_intent()
        v = verify(intent, extract_terms(body, allow_llm=False), body)
        self.assertEqual(v.result, "PASS")


class ReadDocument(unittest.TestCase):
    def test_reads_plain_text_file(self):
        text = read_document(os.path.join(SAMPLES, "invoice_match.txt"))
        self.assertIn("Acme Analytics", text)


class Constants(unittest.TestCase):
    def test_default_model_is_small_and_cheap(self):
        self.assertEqual(DEFAULT_MODEL, "claude-haiku-4-5")
        self.assertIsInstance(money("1"), Decimal)


if __name__ == "__main__":
    unittest.main()

"""LLM extraction of PDF/email invoices into a typed `VendorTerms`.

Design rule (same as the rest of PassGate): **the model proposes, the verifier
decides.** This module reads unstructured invoice/quote text (plain text, an email
body, or a PDF via `pdftotext`) and produces a `VendorTerms` dataclass. It never
calls the verifier and never emits PASS/FAIL — the caller feeds the returned
`VendorTerms` to the existing deterministic `verifier.verify`, which is the only
thing allowed to decide whether a card may spend.

Two extraction paths:

  * **LLM** (when `ANTHROPIC_API_KEY` is set and `allow_llm=True`): one call to the
    Anthropic Messages API with a small/cheap model and a strict JSON schema, so
    the reply is constrained to the `VendorTerms` shape. The JSON is still
    validated and coerced into the real dataclass on our side.
  * **Heuristic** (no key, call fails, or validation fails): a deterministic
    regex extractor. This is what makes the tests and the demo run fully offline.

We talk to the API over stdlib `urllib` on purpose: PassGate is a zero-dependency
(stdlib-only) project, and raw HTTP keeps it that way while leaving the offline
path trivially importable. The API key is read from the environment and is never
logged, printed, or returned.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

from .intent import VendorTerms, money

# A small, cheap model is plenty for structured field extraction.
DEFAULT_MODEL = "claude-haiku-4-5"
_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


@dataclass
class Extraction:
    """What was extracted, and how."""
    terms: VendorTerms
    source: str            # "llm" | "heuristic"
    model: str | None = None   # model id when source == "llm"


# --------------------------------------------------------------------------- #
# Document loading (plain text / email body / PDF via pdftotext)
# --------------------------------------------------------------------------- #

def read_document(path: str) -> str:
    """Return the text of an invoice file.

    `.pdf` is converted with `pdftotext` when that binary is available (part of
    poppler-utils); any other extension is read as UTF-8 text. Raises if a PDF is
    given but `pdftotext` is missing, so the caller can decide what to do.
    """
    p = str(path)
    if p.lower().endswith(".pdf"):
        if shutil.which("pdftotext") is None:
            raise RuntimeError(
                "pdftotext not found (install poppler-utils) — or pass the text directly"
            )
        out = subprocess.run(
            ["pdftotext", "-layout", p, "-"],
            capture_output=True, text=True,
        )
        if out.returncode != 0:
            raise RuntimeError(f"pdftotext failed: {out.stderr.strip()}")
        return out.stdout
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def extract_terms(text: str, *, allow_llm: bool = True, model: str = DEFAULT_MODEL,
                  api_key: str | None = None, timeout: float = 20.0) -> VendorTerms:
    """Extract `VendorTerms` from invoice/quote text (headline API)."""
    return extract(text, allow_llm=allow_llm, model=model,
                   api_key=api_key, timeout=timeout).terms


def extract(text: str, *, allow_llm: bool = True, model: str = DEFAULT_MODEL,
            api_key: str | None = None, timeout: float = 20.0) -> Extraction:
    """Extract `VendorTerms` and report whether the LLM or the fallback was used.

    Falls back to the deterministic heuristic whenever the model is disabled, no
    key is present, the API call fails, or the reply does not validate. The
    extractor never decides PASS/FAIL — run the result through `verifier.verify`.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if allow_llm and key:
        try:
            raw = _call_anthropic(text, model=model, api_key=key, timeout=timeout)
            terms = _terms_from_dict(raw)
            return Extraction(terms=terms, source="llm", model=model)
        except Exception:
            # Network error, missing/garbled JSON, schema mismatch, bad coercion:
            # fall through to the deterministic path so callers never hard-fail.
            pass
    return Extraction(terms=heuristic_terms(text), source="heuristic")


# --------------------------------------------------------------------------- #
# LLM path
# --------------------------------------------------------------------------- #

# Money fields are strings so the model can't hand us lossy floats.
_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {"type": "string"},
        "currency": {"type": "string"},
        "monthly_price": {"type": ["string", "null"]},
        "annual_price": {"type": ["string", "null"]},
        "merchant_category_code": {"type": "string"},
        "cancellation": {"type": "string"},
        "billing_term": {"type": ["string", "null"]},
        "amount_due": {"type": ["string", "null"]},
    },
    "required": [
        "vendor", "currency", "monthly_price", "annual_price",
        "merchant_category_code", "cancellation", "billing_term", "amount_due",
    ],
    "additionalProperties": False,
}

_SYSTEM = (
    "You extract structured fields from a SaaS vendor quote or invoice. "
    "Return only the requested fields. Rules: money fields are plain decimal "
    "strings with no currency symbol or thousands separators (e.g. \"1200.00\"); "
    "use null when a field is absent. `currency` is the ISO 4217 code (e.g. USD). "
    "`billing_term` is MONTHLY or ANNUAL (the term THIS document bills), or null "
    "if the document does not say. `amount_due` is the amount this document asks "
    "to be charged now. `merchant_category_code` is the 4-digit MCC if present, "
    "else an empty string. `cancellation` is one of anytime, end_of_term, none. "
    "You only propose values; a separate deterministic verifier decides whether "
    "they are acceptable, so never editorialize — just extract."
)


def _call_anthropic(text: str, *, model: str, api_key: str, timeout: float) -> dict:
    body = {
        "model": model,
        "max_tokens": 1024,
        "system": _SYSTEM,
        "messages": [{
            "role": "user",
            "content": "Extract the fields from this document:\n\n" + text,
        }],
        "output_config": {"format": {"type": "json_schema", "schema": _SCHEMA}},
    }
    req = urllib.request.Request(
        _API_URL,
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,                 # read from env; never logged
            "anthropic-version": _ANTHROPIC_VERSION,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    for block in data.get("content", []):
        if block.get("type") == "text":
            return json.loads(block["text"])
    raise ValueError("no text block in model response")


def _terms_from_dict(d: dict) -> VendorTerms:
    """Validate/coerce a model (or any) dict into `VendorTerms`.

    Raises on missing required evidence so the caller can fall back. The verifier
    still compares field-by-field afterwards, so a malformed proposal that slips
    through here simply becomes a FAIL, never a spurious PASS.
    """
    if not isinstance(d, dict):
        raise ValueError("expected an object")

    def s(key: str) -> str | None:
        v = d.get(key)
        if v is None:
            return None
        v = str(v).strip()
        return v or None

    def m(key: str):
        v = s(key)
        return money(v.replace(",", "").lstrip("$€£").strip()) if v else None

    vendor = s("vendor")
    currency = (s("currency") or "").upper()
    monthly = m("monthly_price")
    if not vendor or not currency or monthly is None:
        raise ValueError("missing vendor/currency/monthly_price")

    term = s("billing_term")
    return VendorTerms(
        vendor=vendor,
        currency=currency,
        monthly_price=monthly,
        annual_price=m("annual_price"),
        merchant_category_code=(s("merchant_category_code") or ""),
        cancellation=(s("cancellation") or "none").lower(),
        billing_term=term.upper() if term else None,
        amount_due=m("amount_due"),
    )


# --------------------------------------------------------------------------- #
# Deterministic heuristic fallback (offline)
# --------------------------------------------------------------------------- #

_NUM = r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
_MARKER = re.compile(r"^\s*[*#\-=]", re.I)


def _num_after(label: str, text: str):
    """First number that appears after `label` on the same line."""
    m = re.search(label + r"[^0-9\n]*" + _NUM, text, re.I)
    return money(m.group(1).replace(",", "")) if m else None


def _label(label: str, text: str) -> str | None:
    m = re.search(rf"^\s*{label}\s*:\s*(.+?)\s*$", text, re.I | re.M)
    return m.group(1).strip() if m else None


def _vendor(text: str) -> str | None:
    v = _label("vendor", text) or _label("from", text) or _label("bill from", text)
    if v:
        return v
    # Otherwise the first real line that isn't a decoration/marker.
    for line in text.splitlines():
        line = line.strip()
        if line and not _MARKER.match(line):
            return re.sub(r"[.,]+$", "", line).strip()
    return None


def _currency(text: str) -> str:
    c = _label("currency", text)
    if c:
        mm = re.search(r"[A-Za-z]{3}", c)
        if mm:
            return mm.group(0).upper()
    mm = re.search(r"\b(USD|EUR|GBP|AUD|CAD|CNY|JPY)\b", text)
    if mm:
        return mm.group(1).upper()
    for sym, code in (("$", "USD"), ("€", "EUR"), ("£", "GBP")):
        if sym in text:
            return code
    return "USD"


def _cancellation(text: str) -> str:
    raw = _label("cancellation", text) or ""
    blob = (raw + " " + text).lower()
    if "anytime" in blob or "any time" in blob:
        return "anytime"
    if "end of term" in blob or "end_of_term" in blob or "end-of-term" in blob:
        return "end_of_term"
    if raw:
        return re.sub(r"\s+", "_", raw.strip().lower())
    return "none"


def _mcc(text: str) -> str:
    m = re.search(r"\bMCC\b\s*:?\s*([0-9]{4})", text, re.I) or \
        re.search(r"merchant\s+category(?:\s+code)?\s*:?\s*([0-9]{4})", text, re.I)
    return m.group(1) if m else ""


def heuristic_terms(text: str) -> VendorTerms:
    """Deterministic regex extractor. No network, no model."""
    vendor = _vendor(text)
    currency = _currency(text)
    monthly = _num_after(r"monthly(?:\s*price)?", text)
    if not vendor or monthly is None:
        raise ValueError("could not find vendor / monthly price in document")

    term_m = re.search(r"billing\s*term\s*:?\s*([A-Za-z]+)", text, re.I)
    return VendorTerms(
        vendor=vendor,
        currency=currency,
        monthly_price=monthly,
        annual_price=_num_after(r"annual(?:\s*price)?", text),
        merchant_category_code=_mcc(text),
        cancellation=_cancellation(text),
        billing_term=term_m.group(1).upper() if term_m else None,
        amount_due=_num_after(r"(?:amount|balance|total)\s*due", text),
    )


# --------------------------------------------------------------------------- #
# CLI: extract from files and show the deterministic verifier's verdict
# --------------------------------------------------------------------------- #

def _demo_intent():
    """Build the demo's approved intent so the CLI can show PASS/FAIL end to end."""
    from .agent import PassGate
    from .airwallex.mock import MockIssuingClient
    from .demo import QUOTE
    from .intent import parse_terms_text
    pg = PassGate(MockIssuingClient({"USD": 64000}))
    intent, _ = pg.propose(parse_terms_text(QUOTE), 350, 50000)
    return intent


def main(argv: list[str]) -> int:
    from .verifier import verify  # imported here so the module stays import-light
    if not argv:
        print("usage: python3 -m passgate.extract <invoice.txt|invoice.pdf> ...")
        return 2
    intent = _demo_intent()
    print(f"approved intent: {json.dumps(intent.bound_fields())}\n")
    for path in argv:
        text = read_document(path)
        ex = extract(text)  # LLM if ANTHROPIC_API_KEY is set, else heuristic
        v = verify(intent, ex.terms, text)  # the model never decides this
        tag = ex.source + (f":{ex.model}" if ex.model else "")
        print(f"== {path}  (extracted via {tag})")
        print("   terms:", json.dumps(ex.terms.to_dict()))
        if v.ok:
            print(f"   VERDICT: PASS  terms_hash={v.terms_hash[:16]}...")
        else:
            print(f"   VERDICT: FAIL  diff={json.dumps(v.diff)}")
        print()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))

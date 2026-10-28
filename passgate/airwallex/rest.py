"""Real Airwallex sandbox client (stdlib only).

Auth: POST /api/v1/authentication/login with x-client-id / x-api-key headers ->
Bearer token (~30 min). Keys are read from env/.env and never logged or returned.
Paths from the Kit 2 builder guide + Airwallex Docs MCP (2026-10-28). Items marked
VERIFY are unconfirmed against a live sandbox account.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .base import AirwallexError


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


class RestIssuingClient:
    name = "airwallex-sandbox"

    def __init__(self, client_id: str, api_key: str, base_url: str = "https://api.sandbox.airwallex.com"):
        if "sandbox" not in base_url:
            raise ValueError("PassGate is sandbox-only; refusing non-sandbox base URL")
        self._cid, self._key, self.base = client_id, api_key, base_url.rstrip("/")
        self._token, self._exp = None, 0.0

    @classmethod
    def from_env(cls) -> "RestIssuingClient":
        load_dotenv()
        cid, key = os.environ.get("AIRWALLEX_CLIENT_ID"), os.environ.get("AIRWALLEX_API_KEY")
        if not cid or not key:
            raise RuntimeError("AIRWALLEX_CLIENT_ID / AIRWALLEX_API_KEY not set (see .env.example)")
        return cls(cid, key, os.environ.get("AIRWALLEX_BASE_URL", "https://api.sandbox.airwallex.com"))

    def _http(self, method: str, path: str, body: dict | None = None, headers: dict | None = None, query=None):
        url = self.base + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read() or b"{}")
            except ValueError:
                err = {}
            raise AirwallexError(e.code, err.get("code", "http_error"), err.get("message", "")) from None

    def _auth(self) -> dict:
        if not self._token or time.time() > self._exp - 120:
            r = self._http("POST", "/api/v1/authentication/login",
                           headers={"x-client-id": self._cid, "x-api-key": self._key})
            self._token, self._exp = r["token"], time.time() + 25 * 60
        return {"Authorization": f"Bearer {self._token}"}

    def _call(self, method, path, body=None, query=None):
        return self._http(method, path, body, self._auth(), query)

    # --- reads
    def get_balance(self, currency: str) -> float:
        for b in self._call("GET", "/api/v1/balances/current"):
            if b.get("currency") == currency:
                return float(b.get("available_amount", 0))
        return 0.0

    def get_cardholder(self, cardholder_id):
        return self._call("GET", f"/api/v1/issuing/cardholders/{cardholder_id}")

    def get_card(self, card_id):
        return self._call("GET", f"/api/v1/issuing/cards/{card_id}")

    def get_card_limits(self, card_id):
        return self._call("GET", f"/api/v1/issuing/cards/{card_id}/limits")

    def list_transactions(self, card_id):
        r = self._call("GET", "/api/v1/issuing/transactions", query={"card_id": card_id})
        return r.get("items", r if isinstance(r, list) else [])

    # --- writes (fresh request_id on every create)
    def create_cardholder(self, email, request_id):
        return self._call("POST", "/api/v1/issuing/cardholders/create",
                          {"request_id": request_id, "type": "DELEGATE", "email": email})

    def create_card(self, payload):
        return self._call("POST", "/api/v1/issuing/cards/create", payload)

    def set_card_status(self, card_id, status, request_id):
        return self._call("POST", f"/api/v1/issuing/cards/{card_id}/update",
                          {"request_id": request_id, "card_status": status})

    # --- sandbox simulation
    def sim_authorize(self, card_id, amount, currency, mcc, merchant):
        return self._call("POST", "/api/v1/simulation/issuing/create", {
            "card_id": card_id, "transaction_amount": amount, "transaction_currency": currency,
            "merchant_category_code": mcc, "merchant_info": merchant})

    def sim_capture(self, lifecycle_id):
        # Docs MCP lists the lifecycle path; the builder guide shows /simulation/issuing/{id}/capture. VERIFY.
        try:
            return self._call("POST", f"/api/v1/simulation/issuing/card_transaction_lifecycles/{lifecycle_id}/capture", {})
        except AirwallexError as e:
            if e.status != 404:
                raise
            return self._call("POST", f"/api/v1/simulation/issuing/{lifecycle_id}/capture", {})


def new_request_id() -> str:
    return str(uuid.uuid4())

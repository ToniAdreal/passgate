# PassGate — no verified PASS, no spend

Airwallex Agentic Banking Hackathon, Starter Kit 2 (Intent-Bound Purchase Agent).
Status: build-phase scaffold (2026-10-28). Local/private only, no remote.

An agent picks how to pay for a SaaS renewal using live cash (Decide), checks every
quote/invoice against the approved intent with a deterministic verifier (Verify), and
only then arms an Airwallex virtual card whose controls equal the approved terms (Spend).

```
quote ─► Decide (balances + reserve forecast) ─► human approves intent
invoice ─► Verify (field-by-field, signed PASS bound to terms hash) ─► FAIL → freeze + escalate
PASS ─► Spend: cardholder + virtual card (PER_TRANSACTION = MONTHLY cap = approved amount,
        allowed_currencies, allowed_merchant_categories) ─► charge clears ─► re-freeze
renewal ─► Re-approve: small rise auto-PASS (updates card limits); rise > threshold → human approval
charge  ─► Webhook alert: over-amount / wrong category / wrong currency → freeze + exception
```

## Run (no credentials needed — mock backend)
```
python3 -m passgate.demo                      # full story: 3 declines, auto re-approval, human re-approval, webhook freeze
python3 -m passgate.extract samples/*.txt     # extract invoices -> VendorTerms -> verifier PASS/FAIL (offline)
python3 -m passgate.approval_server           # local approval screen on http://127.0.0.1:8777
python3 -m unittest discover -s tests -t .    # 30 tests
```
Python 3.11+, stdlib only.

## Run against the Airwallex sandbox
```
cp .env.example .env    # fill AIRWALLEX_CLIENT_ID / AIRWALLEX_API_KEY (sandbox only; .env is gitignored)
python3 scripts/day1_check.py                 # capability check, writes runs/day1_*.json
PASSGATE_BACKEND=rest python3 -m passgate.demo
```
`RestIssuingClient` refuses any base URL without "sandbox".

## Layout
| file | role |
|---|---|
| `passgate/intent.py` | typed `Intent` / `VendorTerms`, deterministic terms extractor |
| `passgate/extract.py` | LLM extraction of PDF/email invoices into `VendorTerms` (model proposes, verifier decides; offline regex fallback) |
| `passgate/decide.py` | monthly vs annual cash simulation against reserve floor |
| `passgate/verifier.py` | PASS/FAIL, terms hash, HMAC signature, `check_pass` |
| `passgate/policy.py` | intent → Airwallex `authorization_controls` / card-create payload |
| `passgate/agent.py` | orchestration, permissions, audit log, freeze kill-switch, re-approval + webhook handling |
| `passgate/webhook.py` | HMAC signature check + charge-vs-controls violation check (pure) |
| `passgate/approvals.py` | pending-approval store (human-in-the-loop queue, pure) |
| `passgate/approval_server.py` | local approval screen: one static HTML page + stdlib `http.server` |
| `passgate/airwallex/rest.py` | real sandbox REST client (token refresh, fresh `request_id`) |
| `passgate/airwallex/mock.py` | in-memory issuing emulator (documented decline reasons) |
| `scripts/day1_check.py` | live sandbox capability test |

## Re-approval on renewal (price change)
`PassGate.renew(new_terms, email)` reconsiders a renewal whose price moved:
- **Rise ≤ `reapproval_threshold_pct` (default 10%), or a price drop** → auto re-approval: mint a
  fresh single-use PASS, **update the card's per-charge and MONTHLY limits in place**
  (`/issuing/cards/{id}/update` → `authorization_controls`, logged as `card_limits_updated`), re-arm.
- **Rise above the threshold** → no auto-PASS: the card is frozen and a `renewal_price_rise` approval
  is queued for a human. `decide_approval(id, approve, by)` either applies the renewal (updates limits,
  re-arms) or leaves the card frozen. The threshold is a field on `PassGate`, so it is configurable.

## Webhook-style alert handler
`PassGate.handle_webhook(event, secret=…, timestamp=…, signature=…)` inspects an inbound
`issuing.transaction.*`-shaped event. If a `secret` is given, the Airwallex HMAC
(`x-timestamp` + raw body, SHA-256, replay-windowed) must verify first. A charge that **exceeds the
approved amount**, comes from a **different merchant category**, or uses a disallowed currency
**freezes the card** and records an entry in `pg.exceptions` plus a `webhook_exception` audit line.

## Approval screen
`python3 -m passgate.approval_server` serves one static HTML page (loopback only) listing pending
approvals with Approve/Reject buttons. Wire it to a live agent with
`make_server(pg.approvals, on_decide=pg.decide_approval)`; the decider name is recorded in the audit log.

## Controls (judging: "agentic behavior & controls")
- Agent may: read balances, compute options, run verifier, issue a card scoped to a PASS, freeze
  (kill-switch, always allowed), auto re-approve a renewal whose price moved within threshold, and
  react to a webhook alert by freezing + recording an exception.
- Needs human approval: the intent; any FAIL; unfreezing after a FAIL (requires new approval + PASS);
  a renewal price rise above `reapproval_threshold_pct` (queued to the approval screen).
- Never: see API keys (client holds them), set limits freehand (policy code builds them), reuse a PASS
  (single use), or auto-approve a renewal rise above the threshold.

## Invoice extraction (model proposes, verifier decides)
`passgate/extract.py` turns an unstructured quote/invoice (plain text, an email body, or a
PDF via `pdftotext` when available) into a typed `VendorTerms`. The **model only proposes** the
fields; the existing deterministic `verifier.verify` is still the only thing that issues PASS/FAIL.

- With `ANTHROPIC_API_KEY` set, one call to the Anthropic Messages API (`claude-haiku-4-5`, a
  small/cheap model) with a strict JSON schema; the reply is validated and coerced into the real
  dataclass on our side. The key is read from the environment and never logged or committed.
- With no key, a network failure, or a reply that doesn't validate, a deterministic regex/heuristic
  extractor runs instead — so the demo and the whole test suite run fully offline.

```
python3 -m passgate.extract samples/invoice_match.txt            # -> PASS
python3 -m passgate.extract samples/invoice_amount_changed.txt   # -> FAIL (amount)
python3 -m passgate.extract samples/invoice_term_changed.txt     # -> FAIL (billing term)
```
```python
from passgate.extract import extract_terms, read_document
from passgate.verifier import verify
terms = extract_terms(read_document("invoice.pdf"))   # LLM if key present, else heuristic
verdict = verify(approved_intent, terms, raw_text)     # code decides, not the model
```
`samples/` holds three clearly-marked **example** invoices: one that matches the approved intent
(PASS) and two that should FAIL (a changed amount, and a switch to annual billing).

## TODO (weeks 2–3, see ../IDEA.md)
- Wire the webhook handler to a live endpoint and confirm the real HMAC header names on sandbox events.
- <5-min video; repo for submission (Toni decides public/private).

Done (this build): LLM extraction of PDF/email invoices into `VendorTerms` (model proposes, verifier
decides; offline regex fallback + `samples/`); card-limit update on re-approval
(`/issuing/cards/{id}/update` authorization_controls); webhook-style alert handler
(HMAC x-timestamp + x-signature) → freeze + exception; local approval screen.

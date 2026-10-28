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
```

## Run (no credentials needed — mock backend)
```
python3 -m passgate.demo                      # full story incl. 3 declines and month-2 price rise
python3 -m unittest discover -s tests -t .    # 7 tests
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
| `passgate/decide.py` | monthly vs annual cash simulation against reserve floor |
| `passgate/verifier.py` | PASS/FAIL, terms hash, HMAC signature, `check_pass` |
| `passgate/policy.py` | intent → Airwallex `authorization_controls` / card-create payload |
| `passgate/agent.py` | orchestration, permissions, audit log, freeze kill-switch |
| `passgate/airwallex/rest.py` | real sandbox REST client (token refresh, fresh `request_id`) |
| `passgate/airwallex/mock.py` | in-memory issuing emulator (documented decline reasons) |
| `scripts/day1_check.py` | live sandbox capability test |

## Controls (judging: "agentic behavior & controls")
- Agent may: read balances, compute options, run verifier, issue a card scoped to a PASS, freeze.
- Needs human approval: the intent; any FAIL; unfreezing after a FAIL (requires new approval + PASS).
- Never: see API keys (client holds them), set limits freehand (policy code builds them), reuse a PASS (single use).

## TODO (weeks 2–3, see ../IDEA.md)
- Update card limits on re-approval (`/issuing/cards/{id}/update` authorization_controls).
- Webhooks `issuing.transaction.succeeded/failed` (HMAC x-timestamp + x-signature) → auto re-freeze.
- LLM extraction of PDF/email invoices into `VendorTerms` (model proposes, verifier decides).
- Approval UI; <5-min video; repo for submission (Toni decides public/private).

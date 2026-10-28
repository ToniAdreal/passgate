# What is blocked (2026-10-28, PT)

| item | blocked on | who |
|---|---|---|
| Any real API call | No sandbox Client ID / API key on the box | Toni |
| Sandbox login | Email not confirmed: confirm link in Gmail "[Sandbox] Welcome..." (Oct 27 2:07 AM PT, resent Oct 28 2:07 AM PT), expires 2 days after send — needs a browser click | Toni |
| API key | After confirming: sandbox web app → Account/Settings → Developer → API keys → create; put values in `passgate/.env` (gitignored) | Toni |
| Day-1 China-account issuing test | Above two. Then run `python3 scripts/day1_check.py` | Toni / agent |
| Slack (#announcements participant guide, #help, Kit 2 channel) | Joining is mandatory; invite in LINKS_AND_RULES.md; agent will not join/post | Toni |

## Assumptions in the mock that the live sandbox must confirm
1. DELEGATE cardholder via REST with just `type` + `email` reaches READY (docs show this example).
2. Card create with `is_personalized=false`, `program.purpose=COMMERCIAL`, `form_factor=VIRTUAL` is accepted for a CN-registered sandbox; CN default currency is CNY, so controls set `transaction_limits.currency=USD` and `allowed_currencies=["USD"]` explicitly.
3. Decline order / reasons: CURRENCY_NOT_ALLOWED, MERCHANT_CATEGORY_NOT_ALLOWED, LIMIT_EXCEEDED, CARD_INACTIVE appear as `failure_reason` on FAILED transactions.
4. Simulation capture path: Docs MCP lists `/simulation/issuing/card_transaction_lifecycles/{id}/capture`; builder guide shows `/simulation/issuing/{id}/capture`. Client tries the first, falls back on 404.
5. Sandbox USD balance exists (mock assumes 64,000 USD); the Decide step needs a funded USD balance or a demo forecast.

Evidence so far: Oct 27 2:19 AM PT email "[Sandbox] Sandbox Business created an Airwallex Card for you!" — a virtual card already exists in the account (created in the web app / auto-provisioned). That shows issuing is enabled at web-app level; API issuing is not yet tested.

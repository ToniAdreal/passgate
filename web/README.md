# PassGate

**An intent-bound purchase agent: it can only spend after a verified PASS.**

PassGate is a demo for the Airwallex Agentic Banking Hackathon (Track 2).
An AI purchasing agent proposes a purchase; before any money moves, every
intent is checked against a spend policy. If verification fails, the spend is
blocked before it happens. Anomalies freeze the card. Every decision is sealed
into a tamper-evident SHA-256 hash-chain evidence log that a third party can
replay and verify.

Shortlisted idea (HackerEarth portal, Track 2): *"PassGate: a purchase agent
that can only spend after a verified PASS"*.

## Sandbox replay mode — read this first

This demo has **no live Airwallex connection**. It runs in **sandbox replay
mode**: the spend step replays outcomes actually observed on the Airwallex
sandbox, and the UI carries a visible "SANDBOX REPLAY MODE" label. The
verification engine, approval flow, freeze logic and evidence chain are fully
real and run live in this app.

The code is structured so a real Airwallex API client can be dropped in
behind the same `spend()` interface later: `src/airwallex.ts` defines an
`AirwallexClient` interface, implemented today by `SandboxReplayClient`.

## What it is

- **Dashboard** (`/`): card status (Active/Frozen), policy summary, spend
  total, the demo arc, an intent creation form, the intent queue, the six
  observed sandbox cases, and the evidence log with a Verify chain button and
  JSON export.
- **REST API** for the same actions, so the demo can be driven by `curl` too.

## Architecture

```
src/
  gate.ts       PassGate: intent lifecycle, policy evaluation, approvals,
                re-pricing, spend, freeze/unfreeze, receipts. The PASS gate.
  chain.ts      EvidenceChain: SHA-256 hash chain, canonical JSON, verify,
                export. hash = sha256(prevHash + canonical JSON of record).
  policy.ts     Spend policy and allowlists (defaults from the sandbox card).
  money.ts      Integer minor-unit money handling (no float drift).
  airwallex.ts  AirwallexClient interface + SandboxReplayClient; the six
                observed sandbox cases (OBSERVED_SANDBOX_CASES).
  seed.ts       Seeds the six observed cases through the live gate, plus the
                demo arc (goal -> plan -> approval -> price change).
  server.ts     HTTP server (Node stdlib only): static UI + REST API.
public/
  index.html    Dashboard UI.
  app.js        Dashboard logic (plain fetch, no dependencies).
  style.css     Dashboard styling.
test/
  gate.test.ts  PASS gate behaviour (node:test).
  chain.test.ts Evidence chain behaviour (node:test).
```

Everything is in-memory; there is no database and no config. State resets
when the server restarts (or via `POST /api/reset`).

## How the PASS gate works

A purchase intent has merchant, amount, currency, category, purpose and a
quoted price (the price when the intent was created). A human approves the
intent; the gate then runs six checks, and **all** must pass for PASS:

1. `amount_within_limit` — amount <= per-transaction limit (USD 1,200).
2. `currency_allowed` — currency in the allowlist ([USD]).
3. `merchant_allowed` — merchant in the allowlist ([Acme Analytics]).
4. `category_allowed` — category in the allowlist
   ([software, analytics, cloud, saas]).
5. `price_integrity` — amount equals the quoted price. If the price changed
   since the quote, the intent returns to `PENDING_REAPPROVAL` and cannot
   spend until a human re-approves the new price.
6. `card_not_frozen` — the card is not frozen.

On PASS the spend is authorised (sandbox replay) and a receipt is issued. On
FAIL the intent is BLOCKED, the reason is recorded, and no money moves.

**Anomaly rule:** 3 failed attempts for the same card within a session freeze
the card automatically. A frozen card blocks even a valid intent. A named
person can unfreeze it from the UI (`POST /api/card/unfreeze`).

**Evidence chain:** every step (intent created, each check result, decision,
approval, spend, freeze) is appended to a SHA-256 hash chain. Each record
stores `prevHash` and `hash = sha256(prevHash + canonical JSON of the
record)`. `GET /api/evidence/verify` recomputes the chain and reports
`INTACT` or the index of the first broken record. `GET /api/evidence/export`
downloads the full log as JSON.

## Mapping to the real sandbox evidence

The Airwallex sandbox account ran a card ("PassGate Acme Analytics") against
merchant Acme Analytics, observed 2026-10-28 to 2026-11-02. These exact cases
are the seed data and the test fixtures:

| Case | Attempt | Sandbox outcome |
| ---- | ------- | --------------- |
| obs-1 | USD 1,200.00 at Acme Analytics | Settled (card tail 2105) |
| obs-2 | USD 1,200.00 at Acme Analytics | Settled (card tail 3425) |
| obs-3 | USD 1,200.01 | Declined: over spend limit (limit USD 1,200) |
| obs-4 | EUR 1,200.00 | Declined: currency restricted (USD only) |
| obs-5 | USD 10.00 | Declined: merchant category not allowed |
| obs-6 | USD 1,248.01 | Declined: over spend limit |

Policy defaults follow directly: per-transaction limit USD 1,200, currency
allowlist [USD], merchant allowlist [Acme Analytics], category allowlist
[software, analytics, cloud, saas]. On startup the app replays all six cases
through the live gate; the dashboard shows whether the gate agrees with the
observed outcome for each one (it does for all six).

## Setup

Requires Node 20+ and pnpm.

```sh
pnpm install
pnpm test     # builds with tsc, then runs the node:test suite
pnpm start    # serves the dashboard on http://localhost:8080 (PORT env overrides)
```

No secrets, no API keys, no credentials anywhere. Nothing to configure.

## Demo walkthrough (about 3 minutes)

1. **Goal.** Open the dashboard. The card is Active, the policy panel shows
   the USD 1,200 limit, USD-only currency, Acme Analytics only. The demo arc
   shows the goal: buy one Acme Analytics annual team seat.
2. **Clean PASS.** Create an intent: Acme Analytics, USD 1,200.00, category
   analytics. Approve it as "finance approver". Every check passes: PASS.
   Click Spend. A receipt is issued (replayed observed case obs-1 or obs-2)
   and sealed into the evidence log.
3. **Price change.** The seeded demo intent was quoted at USD 1,150.00 and
   approved, then the checkout price moved to USD 1,200.00. It now sits in
   REAPPROVAL: spend is refused until a human re-approves the new price.
   Approve it, then Spend.
4. **Attacks on the policy.** Create three blocked intents in a row:
   USD 1,200.01 (over limit), EUR 1,200.00 (currency), USD 10.00 category
   entertainment (category). After the third failure the card freezes and the
   dashboard shows FROZEN. Even a valid USD 1,200 intent is now blocked.
   Unfreeze the card as a named person to continue.
5. **Evidence.** Open the evidence log, click **Verify chain**: INTACT.
   Export the JSON. Click **Tamper demo** to mutate one stored record and
   watch verification report BROKEN at that index, then Reset the demo to
   restore the chain.

## REST API

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/api/health` | Liveness + mode |
| GET | `/api/state` | Card, policy, spend totals, intents, narrative, observed cases |
| GET | `/api/intents` | List intents |
| POST | `/api/intents` | Create intent `{merchant, amount, currency, category, purpose, quotedPrice?}` |
| POST | `/api/intents/:id/approve` | Approve `{by}`; optional `{spend: true}` to spend right after PASS |
| POST | `/api/intents/:id/reject` | Reject `{by, reason?}` |
| POST | `/api/intents/:id/reprice` | Price change `{amount, source?}` |
| POST | `/api/intents/:id/spend` | Execute the spend `{by?}` (PASS intents only) |
| POST | `/api/card/freeze` | Manual freeze `{by?, reason?}` |
| POST | `/api/card/unfreeze` | Unfreeze `{by}` |
| GET | `/api/evidence` | Evidence records |
| GET/POST | `/api/evidence/verify` | Chain verification (`INTACT` / `BROKEN` + index) |
| GET | `/api/evidence/export` | Full evidence JSON download |
| POST | `/api/demo/tamper` | Demo only: mutate one stored record to show detection |
| POST | `/api/reset` | Reset state, re-seed `{seed?}` |

Example (from a shell, with the server running on port 8080):

```sh
# create + approve + spend a valid USD 1,200 intent
curl -s -X POST localhost:8080/api/intents -d '{"merchant":"Acme Analytics","amount":1200,"currency":"USD","category":"analytics","purpose":"annual team seat"}'
curl -s -X POST localhost:8080/api/intents/int_0008/approve -d '{"by":"finance approver"}'
curl -s -X POST localhost:8080/api/intents/int_0008/spend -d '{"by":"agent"}'
# verify the evidence chain
curl -s localhost:8080/api/evidence/verify
```

(Intent IDs depend on the seeded state; list them with `GET /api/intents`.)

## Tests

`pnpm test` builds the TypeScript and runs the `node:test` suite
(`test/gate.test.ts`, `test/chain.test.ts`), **16 tests, all passing**.
Coverage of the spec's list: PASS at exactly USD 1,200; over-limit blocks at
1,200.01 and 1,248.01; EUR currency block; disallowed category block;
non-allowlisted merchant block; price-change re-approval (spend refused until
re-approved, then succeeds); 3 failures freeze the card and a frozen card
blocks even a valid intent (and unfreezing restores it); hash chain INTACT;
tampering detected at the right index. Plus: seeded observed cases replay and
agree with the sandbox, reject blocks a pending intent, canonical-JSON hash
stability, evidence export shape.

## Unfinished / known limits

- **Demo video not recorded.** The demo script is in `../DEMO_SCRIPT.md`
  (2–3 minutes, hard cap under 5). The app is ready to record; the video
  itself has not been made.
- **No public deployment.** The app runs locally; no hosted demo URL exists
  yet (a Render deploy like the other hackathon demos would be the next step).
- **No live Airwallex client.** Spend is sandbox replay by design (see above);
  a real `AirwallexClient` still needs to be written and wired in.
- **No submission.** Nothing has been submitted to the HackerEarth portal.
  Final submission (demo, public repo, deploy notes, video) is a separate
  step, due 2026-11-15 23:59 PST.

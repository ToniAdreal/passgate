# PassGate — BUILD_LOG

## 2026-11-08 — Demo app completed (subagent run, finishing the Claude Code build)

**What was done.** The TypeScript engine from the previous builder was already
complete (984 lines: `src/gate.ts`, `server.ts`, `chain.ts`, `airwallex.ts`,
`policy.ts`, `money.ts`, `seed.ts`). This run added everything that was still
missing:

- `public/index.html`, `public/app.js`, `public/style.css` — dashboard UI
  (plain HTML/CSS/JS, no build step, no external dependencies): card status
  (Active/Frozen) with freeze/unfreeze, policy summary, spend total, demo
  arc, intent creation form, intent list with statuses (PENDING, PASS,
  BLOCKED, REAPPROVAL, SPENT) and Approve/Reject/Spend/Reprice actions,
  observed sandbox cases table, evidence log with a Verify chain button,
  JSON export link, a Tamper demo button and a Reset button. A visible
  "SANDBOX REPLAY MODE" badge and disclosure banner are on every page load.
- `test/gate.test.ts` (10 tests) and `test/chain.test.ts` (6 tests), using
  `node:test`, covering the full spec list: PASS at exactly USD 1,200 with an
  observed-case receipt; over-limit blocks at 1,200.01 and 1,248.01; EUR
  currency block; disallowed category block; non-allowlisted merchant block;
  price-change forces re-approval and blocks spend until re-approved (then
  succeeds); 3 failures freeze the card and a frozen card blocks even a valid
  intent (unfreezing restores it); hash chain INTACT; tampering detected at
  the right index (also index 0 and a broken prevHash link); plus seeded
  observed cases replaying through the live gate and agreeing with the
  sandbox, reject blocking a pending intent, canonical-JSON hash stability,
  and evidence export shape.
- `README.md` — what it is, architecture, PASS gate, sandbox replay
  disclosure, setup commands, demo walkthrough, mapping table to the six real
  sandbox cases, full REST API table, and an "Unfinished / known limits"
  section.

**Test result.** `pnpm install` (already up to date) then `pnpm test`:
**16 tests, 16 pass, 0 fail.** No test was skipped or changed to make it
pass; the engine code was not modified.

**Server verification (live, port 8080, `pnpm start`).**
- `GET /` 200 (5,050 bytes), `/app.js` 200, `/style.css` 200.
- `GET /api/health` → `{ok: true, mode: "sandbox-replay"}`.
- Seeded state on boot: card ACTIVE, policy limit USD 1,200.00, intents
  int_0001–0002 SPENT, int_0003–0006 BLOCKED, int_0007 REAPPROVAL (demo arc).
- End-to-end via curl: created intent int_0008 (USD 1,200, Acme Analytics,
  analytics) → approve → PASS (6/6 checks) → spend → SPENT, receipt
  rcpt_0003, USD 1,200.00, source `observed`, case obs-1, tx replay_tx_0003.
- Blocked case via curl: EUR 1,200 intent int_0009 → approve → BLOCKED,
  reasons "limit is defined in USD; EUR 1,200.00 cannot be evaluated without
  FX (fail closed)" and "currency restricted: EUR not in allowlist [USD]".
- `GET /api/evidence/verify` → `INTACT`, 130 records, head hash
  `aa58125d…`; `GET /api/evidence/export` → same 130 records, verification
  INTACT. Spend totals after the curl run: USD 360,000 minor ($3,600 = 3 ×
  $1,200: two seeded settlements plus the curl spend).
- Server stopped after verification. No secrets, keys or credentials anywhere
  in the repo (grep-checked by construction: nothing was added beyond UI,
  tests, README and this log).

**Commit.** Everything committed to the local git repo in `app/`
(user ToniAdreal, `184365500+ToniAdreal@users.noreply.github.com`). This is
the repo's first commit; nothing was pushed to any remote, nothing was
submitted to any hackathon portal.

**Unfinished, stated plainly.**
- Demo video not recorded (script exists at `../DEMO_SCRIPT.md`, 2–3 min,
  hard cap under 5). The app is ready to record; the video itself has not
  been made.
- No public deployment yet (no hosted demo URL; a Render deploy like the
  other hackathon demos would be the next step).
- No live Airwallex client — spend is sandbox replay by design; a real
  `AirwallexClient` still needs to be written and wired in.
- Nothing has been submitted to the HackerEarth portal. Final submission
  (demo, public repo, deploy notes, video) is a separate step, due
  2026-11-15 23:59 PST.

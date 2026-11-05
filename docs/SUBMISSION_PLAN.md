# PassGate final submission plan (deadline Sun Nov 15, 2026 11:59 PM PST = Mon Nov 16 15:59 Beijing)

Required: working demo, video walkthrough < 5 min, repository link with setup instructions.

## Status (2026-11-04 2:20 PM PT)
- [x] Live Airwallex sandbox demo runs end-to-end (runs/rest_demo_1101_1558c.log)
- [x] README with mock + sandbox setup steps
- [ ] Repo ToniAdreal/passgate is PRIVATE -> needs Toni's approval to make public (or confirm judges accept private)
- [x] README status line fixed and pushed (2026-11-04 5:25 PM PT)
- [ ] Demo video (< 5 min) not recorded yet
- [ ] HackerEarth submission form not filled

## Video script (~4 min, terminal recording of `PASSGATE_BACKEND=rest python3 -m passgate.demo` + approval screen)
0:00-0:30 Problem: agents paying SaaS renewals can overpay or pay the wrong vendor. PassGate rule: no verified PASS, no spend.
0:30-1:00 Decide: live sandbox balances, monthly vs annual against reserve floor; human approves the intent.
1:00-1:40 Verify: invoice extracted (LLM proposes, deterministic verifier decides), signed PASS bound to terms hash.
1:40-2:30 Spend: virtual card with controls equal to approved terms. Show 4 live charges: exact amount clears; $0.01 over -> LIMIT_EXCEEDED; EUR -> CURRENCY_NOT_ALLOWED; MCC 5812 -> MERCHANT_CATEGORY_NOT_ALLOWED. Card re-frozen.
2:30-3:20 Renewals: +4% auto re-approval updates card limits in place; +15% freezes card and queues human approval on the local approval screen.
3:20-3:50 Webhook: charge above approved amount -> card frozen + exception recorded.
3:50-4:20 Controls table (what the agent may / needs human / never) + audit log steps. Close with repo link.

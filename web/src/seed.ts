import { OBSERVED_SANDBOX_CASES } from "./airwallex.js";
import { PassGate, Intent } from "./gate.js";
import { formatMinor } from "./money.js";

export interface NarrativeBeat {
  beat: "user-goal" | "agent-plan" | "human-approval" | "new-information" | "revised-decision" | "financial-action" | "escalation";
  text: string;
  intentId?: string;
  done: boolean;
}

export const DEMO_GOAL =
  "Buy one Acme Analytics annual team seat for Q1 reporting. Budget: up to USD 1,200 per transaction, USD only, approved vendors only.";

/**
 * Replays the six observed Airwallex sandbox cases through the live PASS gate.
 * Each observed case was a separate sandbox attempt (different days / cards), so each runs
 * in its own session: they do not count toward the live session's 3-failure freeze rule.
 */
export async function seedObservedCases(gate: PassGate): Promise<Intent[]> {
  const out: Intent[] = [];
  for (const c of OBSERVED_SANDBOX_CASES) {
    const intent = gate.createIntent(
      {
        merchant: c.merchant,
        amount: c.amountMinor / 100,
        currency: c.currency,
        category: c.category,
        purpose: `Replay of observed sandbox case ${c.id}: ${c.description}`,
      },
      { sessionId: `sandbox-${c.id}`, origin: "seed-observed", actor: "seed" },
    );
    gate.approve(intent.id, "seed (replay of observed sandbox case)");
    if (intent.status === "PASS") await gate.spend(intent.id, "seed");
    const gateOutcome = intent.status === "SPENT" ? "SETTLED" : "DECLINED";
    intent.observed = {
      caseId: c.id,
      outcome: c.outcome,
      reason: c.declineReason,
      description: c.description,
      gateAgrees: gateOutcome === c.outcome,
    };
    gate.note("SEED", intent.id, { observedCaseId: c.id, observedOutcome: c.outcome, gateStatus: intent.status, gateAgrees: intent.observed.gateAgrees });
    out.push(intent);
  }
  return out;
}

/**
 * Demo arc (Builder Guide): user goal -> agent initial plan -> new information -> revised decision
 * -> financial action or escalation. Seeding stops at the point where a person must act
 * (the price change forced re-approval); the presenter finishes the arc live.
 *
 * The "agent" here is a deterministic scripted planner (no LLM call); the gate is what is being demonstrated.
 */
export function seedDemoArc(gate: PassGate): { intent: Intent } {
  gate.note("GOAL_SET", null, { by: "user", goal: DEMO_GOAL });
  const plan = {
    merchant: "Acme Analytics",
    amount: "1150.00",
    quotedPrice: "1150.00",
    currency: "USD",
    category: "analytics",
    purpose: "Acme Analytics annual team seat (Q1 reporting)",
    goal: DEMO_GOAL,
  };
  gate.note("AGENT_PLAN", null, {
    by: "purchase-agent (scripted planner)",
    plan: "Acme Analytics is on the merchant allowlist and quotes USD 1,150.00 for an annual seat, under the USD 1,200 limit. Create an intent at the quoted price and request approval.",
    proposed: plan,
  });
  const intent = gate.createIntent(plan, { origin: "demo", actor: "purchase-agent" });
  gate.approve(intent.id, "finance approver");
  gate.note("NEW_INFORMATION", intent.id, {
    source: "Acme Analytics checkout",
    text: "Checkout now shows USD 1,200.00 for the annual seat (was USD 1,150.00 when quoted). The earlier PASS no longer covers this price.",
  });
  gate.reprice(intent.id, "1200.00", "Acme Analytics checkout price update");
  return { intent };
}

export function narrative(gate: PassGate): NarrativeBeat[] {
  const demo = gate.listIntents().find((i) => i.origin === "demo");
  if (!demo) return [];
  const beats: NarrativeBeat[] = [
    { beat: "user-goal", text: DEMO_GOAL, done: true },
    { beat: "agent-plan", text: `Agent proposes ${demo.merchant} at the quoted ${formatMinor(demo.priceHistory[0].amountMinor, demo.currency)} and requests approval.`, intentId: demo.id, done: true },
    { beat: "human-approval", text: "Finance approver approves; the gate verifies all six checks and returns PASS.", intentId: demo.id, done: demo.approvals.length > 0 },
    {
      beat: "new-information",
      text: `Before any money moves, the checkout price changes to ${formatMinor(demo.priceHistory[demo.priceHistory.length - 1].amountMinor, demo.currency)}. Price integrity fails: the PASS is withdrawn and the intent waits for re-approval.`,
      intentId: demo.id,
      done: demo.priceHistory.length > 1,
    },
  ];
  const reapproved = demo.approvals.some((a) => a.kind === "re-approval");
  beats.push({
    beat: "revised-decision",
    text: reapproved
      ? `A person re-approved the new price; the gate re-ran every check: ${demo.status === "BLOCKED" ? "BLOCKED" : "PASS"}.`
      : demo.status === "BLOCKED"
        ? "A person rejected the new price."
        : "Waiting: a person must re-approve (Approve) or reject the new price.",
    intentId: demo.id,
    done: reapproved || demo.status === "BLOCKED",
  });
  if (demo.status === "BLOCKED") {
    beats.push({ beat: "escalation", text: demo.escalation ?? demo.blockReasons.join("; "), intentId: demo.id, done: true });
  } else {
    beats.push({
      beat: "financial-action",
      text: demo.receipt
        ? `Spent ${demo.receipt.amount} at ${demo.merchant} (sandbox replay, ${demo.receipt.source} outcome${demo.receipt.observedCaseId ? ` ${demo.receipt.observedCaseId}` : ""}); receipt ${demo.receipt.receiptId}.`
        : "After re-approval and PASS, the agent executes the spend and a receipt is issued.",
      intentId: demo.id,
      done: demo.status === "SPENT",
    });
  }
  return beats;
}

export async function seedAll(gate: PassGate): Promise<void> {
  await seedObservedCases(gate);
  seedDemoArc(gate);
}

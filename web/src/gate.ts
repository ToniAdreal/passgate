import { EvidenceChain } from "./chain.js";
import { AirwallexClient, SandboxReplayClient, SpendResult } from "./airwallex.js";
import { Policy, defaultPolicy, inAllowlist } from "./policy.js";
import { formatMinor, toMinor } from "./money.js";

/** Intent lifecycle. REAPPROVAL is the spec's PENDING_REAPPROVAL state. */
export type IntentStatus = "PENDING" | "PASS" | "BLOCKED" | "REAPPROVAL" | "SPENT";

export type CheckName =
  | "amount_within_limit"
  | "currency_allowed"
  | "merchant_allowed"
  | "category_allowed"
  | "price_integrity"
  | "card_not_frozen";

export interface CheckResult {
  check: CheckName;
  passed: boolean;
  detail: string;
}

export interface IntentInput {
  merchant: string;
  amount: number | string;
  currency: string;
  category: string;
  purpose: string;
  /** Price when the intent was created. Defaults to amount. */
  quotedPrice?: number | string;
  goal?: string;
}

export interface Receipt {
  receiptId: string;
  intentId: string;
  merchant: string;
  amountMinor: number;
  amount: string;
  currency: string;
  transactionId: string;
  mode: string;
  source: SpendResult["source"];
  observedCaseId?: string;
  cardTail?: string;
  note: string;
  settledAt: string;
  passDecisionHash: string;
  spendEvidenceIndex: number;
}

export interface Intent {
  id: string;
  sessionId: string;
  origin: "live" | "seed-observed" | "demo";
  merchant: string;
  amountMinor: number;
  quotedPriceMinor: number;
  currency: string;
  category: string;
  purpose: string;
  goal?: string;
  status: IntentStatus;
  createdAt: string;
  updatedAt: string;
  checks: CheckResult[];
  blockReasons: string[];
  escalation?: string;
  approvals: { by: string; at: string; kind: "initial" | "re-approval"; acknowledgedPriceMinor: number }[];
  priceHistory: { amountMinor: number; at: string; source: string }[];
  passDecisionHash?: string;
  receipt?: Receipt;
  observed?: { caseId: string; outcome: string; reason?: string; gateAgrees: boolean; description: string };
}

export interface CardState {
  id: string;
  name: string;
  status: "ACTIVE" | "FROZEN";
  frozenReason?: string;
  frozenAt?: string;
  failuresBySession: Record<string, number>;
}

export class GateError extends Error {
  constructor(message: string, public readonly httpStatus = 400) {
    super(message);
  }
}

export interface SpendOutcome {
  ok: boolean;
  intent: Intent;
  receipt?: Receipt;
  reason?: string;
}

export interface GateOptions {
  policy?: Policy;
  client?: AirwallexClient;
  clock?: () => Date;
  liveSessionId?: string;
}

const HARD_CHECKS: CheckName[] = ["amount_within_limit", "currency_allowed", "merchant_allowed", "category_allowed", "card_not_frozen"];

export class PassGate {
  readonly policy: Policy;
  readonly chain: EvidenceChain;
  readonly client: AirwallexClient;
  readonly card: CardState;
  liveSessionId: string;
  private intents = new Map<string, Intent>();
  private seq = 0;
  private receiptSeq = 0;
  private inFlight = new Set<string>();
  private clock: () => Date;

  constructor(opts: GateOptions = {}) {
    this.policy = opts.policy ?? defaultPolicy();
    this.clock = opts.clock ?? (() => new Date());
    this.chain = new EvidenceChain(this.clock);
    this.client = opts.client ?? new SandboxReplayClient();
    this.liveSessionId = opts.liveSessionId ?? "live";
    this.card = { id: "card_passgate_acme", name: "PassGate Acme Analytics", status: "ACTIVE", failuresBySession: {} };
  }

  private now(): string {
    return this.clock().toISOString();
  }

  // ---------- queries ----------

  getIntent(id: string): Intent {
    const it = this.intents.get(id);
    if (!it) throw new GateError(`intent not found: ${id}`, 404);
    return it;
  }

  listIntents(): Intent[] {
    return [...this.intents.values()];
  }

  spendTotals(): Record<string, number> {
    const totals: Record<string, number> = {};
    for (const it of this.intents.values()) {
      if (it.status === "SPENT") totals[it.currency] = (totals[it.currency] ?? 0) + it.amountMinor;
    }
    return totals;
  }

  // ---------- intent lifecycle ----------

  createIntent(input: IntentInput, opts: { sessionId?: string; origin?: Intent["origin"]; actor?: string } = {}): Intent {
    const str = (v: unknown, name: string): string => {
      if (typeof v !== "string" || !v.trim()) throw new GateError(`${name} is required`);
      return v.trim();
    };
    const merchant = str(input.merchant, "merchant");
    const currency = str(input.currency, "currency").toUpperCase();
    const category = str(input.category, "category").toLowerCase();
    const purpose = str(input.purpose, "purpose");
    if (input.amount === undefined || input.amount === null || input.amount === "") throw new GateError("amount is required");
    let amountMinor: number, quotedPriceMinor: number;
    try {
      amountMinor = toMinor(input.amount);
      quotedPriceMinor = input.quotedPrice === undefined || input.quotedPrice === null || input.quotedPrice === "" ? amountMinor : toMinor(input.quotedPrice);
    } catch (e) {
      throw new GateError((e as Error).message);
    }
    const id = `int_${String(++this.seq).padStart(4, "0")}`;
    const at = this.now();
    const intent: Intent = {
      id,
      sessionId: opts.sessionId ?? this.liveSessionId,
      origin: opts.origin ?? "live",
      merchant,
      amountMinor,
      quotedPriceMinor,
      currency,
      category,
      purpose,
      goal: typeof input.goal === "string" && input.goal.trim() ? input.goal.trim() : undefined,
      status: "PENDING",
      createdAt: at,
      updatedAt: at,
      checks: [],
      blockReasons: [],
      approvals: [],
      priceHistory: [{ amountMinor: quotedPriceMinor, at, source: "quote at intent creation" }],
    };
    if (amountMinor !== quotedPriceMinor) intent.priceHistory.push({ amountMinor, at, source: "amount at intent creation" });
    this.intents.set(id, intent);
    this.chain.append("INTENT_CREATED", id, {
      actor: opts.actor ?? "agent",
      sessionId: intent.sessionId,
      origin: intent.origin,
      merchant,
      amountMinor,
      quotedPriceMinor,
      currency,
      category,
      purpose,
      goal: intent.goal ?? null,
    });
    return intent;
  }

  /** Pure policy evaluation (no side effects). */
  runChecks(intent: Intent): CheckResult[] {
    const p = this.policy;
    const limitStr = formatMinor(p.perTransactionLimitMinor, p.limitCurrency);
    const amountStr = formatMinor(intent.amountMinor, intent.currency);
    const amountCheck: CheckResult =
      intent.currency !== p.limitCurrency
        ? { check: "amount_within_limit", passed: false, detail: `limit is defined in ${p.limitCurrency}; ${amountStr} cannot be evaluated without FX (fail closed)` }
        : intent.amountMinor <= p.perTransactionLimitMinor
          ? { check: "amount_within_limit", passed: true, detail: `${amountStr} <= limit ${limitStr}` }
          : { check: "amount_within_limit", passed: false, detail: `over spend limit: ${amountStr} > limit ${limitStr}` };
    return [
      amountCheck,
      inAllowlist(intent.currency, p.currencyAllowlist)
        ? { check: "currency_allowed", passed: true, detail: `${intent.currency} in allowlist [${p.currencyAllowlist.join(", ")}]` }
        : { check: "currency_allowed", passed: false, detail: `currency restricted: ${intent.currency} not in allowlist [${p.currencyAllowlist.join(", ")}]` },
      inAllowlist(intent.merchant, p.merchantAllowlist)
        ? { check: "merchant_allowed", passed: true, detail: `merchant "${intent.merchant}" in allowlist` }
        : { check: "merchant_allowed", passed: false, detail: `merchant not allowed: "${intent.merchant}" not in allowlist [${p.merchantAllowlist.join(", ")}]` },
      inAllowlist(intent.category, p.categoryAllowlist)
        ? { check: "category_allowed", passed: true, detail: `category "${intent.category}" in allowlist` }
        : { check: "category_allowed", passed: false, detail: `merchant category not allowed: "${intent.category}" not in allowlist [${p.categoryAllowlist.join(", ")}]` },
      intent.amountMinor === intent.quotedPriceMinor
        ? { check: "price_integrity", passed: true, detail: `amount equals approved quote ${formatMinor(intent.quotedPriceMinor, intent.currency)}` }
        : {
            check: "price_integrity",
            passed: false,
            detail: `price changed since quote: quoted ${formatMinor(intent.quotedPriceMinor, intent.currency)}, now ${amountStr}; human re-approval required`,
          },
      this.card.status === "FROZEN"
        ? { check: "card_not_frozen", passed: false, detail: `card frozen${this.card.frozenReason ? `: ${this.card.frozenReason}` : ""}` }
        : { check: "card_not_frozen", passed: true, detail: "card active" },
    ];
  }

  /** Run all checks, seal each result, and set the intent's status. */
  private evaluate(intent: Intent, trigger: string): IntentStatus {
    const checks = this.runChecks(intent);
    intent.checks = checks;
    for (const c of checks) this.chain.append("CHECK", intent.id, { trigger, ...c });
    const hardFails = checks.filter((c) => HARD_CHECKS.includes(c.check) && !c.passed);
    const priceFail = checks.find((c) => c.check === "price_integrity" && !c.passed);
    intent.updatedAt = this.now();
    if (hardFails.length) {
      intent.status = "BLOCKED";
      intent.blockReasons = hardFails.map((c) => c.detail);
      intent.escalation = `Blocked before any money moved. Escalated to a person (finance approver): ${intent.blockReasons.join("; ")}`;
      intent.passDecisionHash = undefined;
      this.chain.append("DECISION", intent.id, { trigger, decision: "BLOCKED", reasons: intent.blockReasons, escalatedTo: "finance approver", spend: "none" });
      const cardFrozenOnly = hardFails.every((c) => c.check === "card_not_frozen");
      if (!cardFrozenOnly) this.registerFailure(intent);
      return intent.status;
    }
    if (priceFail) {
      intent.status = "REAPPROVAL";
      intent.blockReasons = [priceFail.detail];
      intent.escalation = "Price changed after approval. Spend is held until a person re-approves the new price.";
      intent.passDecisionHash = undefined;
      this.chain.append("DECISION", intent.id, { trigger, decision: "PENDING_REAPPROVAL", reasons: intent.blockReasons, spend: "held" });
      return intent.status;
    }
    intent.status = "PASS";
    intent.blockReasons = [];
    intent.escalation = undefined;
    const rec = this.chain.append("DECISION", intent.id, { trigger, decision: "PASS", amountMinor: intent.amountMinor, currency: intent.currency, spend: "authorised" });
    intent.passDecisionHash = rec.hash;
    return intent.status;
  }

  private registerFailure(intent: Intent): void {
    const s = intent.sessionId;
    const n = (this.card.failuresBySession[s] ?? 0) + 1;
    this.card.failuresBySession[s] = n;
    this.chain.append("FAILURE_COUNTED", intent.id, { cardId: this.card.id, sessionId: s, failures: n, threshold: this.policy.freezeAfterFailures });
    if (n >= this.policy.freezeAfterFailures && this.card.status !== "FROZEN") {
      this.freezeCard("anomaly-rule", `anomaly: ${n} failed attempts in session "${s}"`);
    }
  }

  approve(id: string, by: string): Intent {
    const intent = this.getIntent(id);
    if (!by || !by.trim()) throw new GateError("approver is required");
    if (intent.status !== "PENDING" && intent.status !== "REAPPROVAL") {
      throw new GateError(`cannot approve intent in status ${intent.status}`, 409);
    }
    const kind = intent.status === "REAPPROVAL" ? "re-approval" : "initial";
    const at = this.now();
    if (kind === "re-approval") {
      // The person explicitly accepts the new price: it becomes the approved quote.
      intent.quotedPriceMinor = intent.amountMinor;
    }
    intent.approvals.push({ by: by.trim(), at, kind, acknowledgedPriceMinor: intent.quotedPriceMinor });
    this.chain.append("APPROVAL", id, { by: by.trim(), kind, acknowledgedPriceMinor: intent.quotedPriceMinor, currency: intent.currency });
    this.evaluate(intent, kind === "re-approval" ? "re-approval" : "approval");
    return intent;
  }

  reject(id: string, by: string, reason?: string): Intent {
    const intent = this.getIntent(id);
    if (!by || !by.trim()) throw new GateError("rejecter is required");
    if (!["PENDING", "REAPPROVAL", "PASS"].includes(intent.status)) {
      throw new GateError(`cannot reject intent in status ${intent.status}`, 409);
    }
    intent.status = "BLOCKED";
    intent.blockReasons = [`rejected by ${by.trim()}${reason ? `: ${reason}` : ""}`];
    intent.escalation = undefined;
    intent.passDecisionHash = undefined;
    intent.updatedAt = this.now();
    this.chain.append("REJECTION", id, { by: by.trim(), reason: reason ?? null });
    this.chain.append("DECISION", id, { trigger: "rejection", decision: "BLOCKED", reasons: intent.blockReasons, spend: "none" });
    return intent;
  }

  /** New information: the merchant's price changed. Unspent intents lose their PASS until re-approved. */
  reprice(id: string, newAmount: number | string, source = "merchant price update"): Intent {
    const intent = this.getIntent(id);
    if (!["PENDING", "PASS", "REAPPROVAL"].includes(intent.status)) {
      throw new GateError(`cannot reprice intent in status ${intent.status}`, 409);
    }
    let minor: number;
    try {
      minor = toMinor(newAmount);
    } catch (e) {
      throw new GateError((e as Error).message);
    }
    const from = intent.amountMinor;
    intent.amountMinor = minor;
    intent.updatedAt = this.now();
    intent.priceHistory.push({ amountMinor: minor, at: intent.updatedAt, source });
    this.chain.append("PRICE_CHANGED", id, { fromMinor: from, toMinor: minor, currency: intent.currency, source, quotedPriceMinor: intent.quotedPriceMinor });
    if (intent.status !== "PENDING") this.evaluate(intent, "price-change");
    return intent;
  }

  /** Execute the spend. Only a PASS intent can spend, and the gate re-verifies at spend time. */
  async spend(id: string, by = "agent"): Promise<SpendOutcome> {
    const intent = this.getIntent(id);
    if (this.inFlight.has(id)) throw new GateError("spend already in progress", 409);
    if (intent.status !== "PASS") {
      const reason = `spend refused: intent status is ${intent.status}, not PASS`;
      this.chain.append("SPEND_REFUSED", id, { by, status: intent.status, reason });
      return { ok: false, intent, reason };
    }
    // Time-of-use re-verification: policy, price and card state are checked again right before money moves.
    if (this.evaluate(intent, "pre-spend re-verification") !== "PASS") {
      const reason = `spend refused at re-verification: ${intent.blockReasons.join("; ")}`;
      this.chain.append("SPEND_REFUSED", id, { by, status: intent.status, reason });
      return { ok: false, intent, reason };
    }
    this.inFlight.add(id);
    try {
      const passHash = intent.passDecisionHash!;
      const result = await this.client.spend({
        intentId: id,
        merchant: intent.merchant,
        amountMinor: intent.amountMinor,
        currency: intent.currency,
        category: intent.category,
        purpose: intent.purpose,
      });
      intent.updatedAt = this.now();
      if (result.status !== "SETTLED") {
        intent.status = "BLOCKED";
        intent.blockReasons = [`declined by card network (${result.mode}): ${result.declineReason ?? "unknown"}`];
        intent.passDecisionHash = undefined;
        this.chain.append("SPEND_DECLINED", id, { by, result });
        this.registerFailure(intent);
        return { ok: false, intent, reason: intent.blockReasons[0] };
      }
      const rec = this.chain.append("SPEND", id, { by, passDecisionHash: passHash, result });
      const receipt: Receipt = {
        receiptId: `rcpt_${String(++this.receiptSeq).padStart(4, "0")}`,
        intentId: id,
        merchant: intent.merchant,
        amountMinor: intent.amountMinor,
        amount: formatMinor(intent.amountMinor, intent.currency),
        currency: intent.currency,
        transactionId: result.transactionId,
        mode: result.mode,
        source: result.source,
        observedCaseId: result.observedCaseId,
        cardTail: result.cardTail,
        note: result.note,
        settledAt: intent.updatedAt,
        passDecisionHash: passHash,
        spendEvidenceIndex: rec.index,
      };
      intent.receipt = receipt;
      intent.status = "SPENT";
      this.chain.append("RECEIPT_ISSUED", id, receipt);
      return { ok: true, intent, receipt };
    } finally {
      this.inFlight.delete(id);
    }
  }

  // ---------- card ----------

  freezeCard(by: string, reason: string): CardState {
    if (this.card.status === "FROZEN") return this.card;
    this.card.status = "FROZEN";
    this.card.frozenReason = reason;
    this.card.frozenAt = this.now();
    this.chain.append("CARD_FROZEN", null, { cardId: this.card.id, by, reason });
    return this.card;
  }

  unfreezeCard(by: string): CardState {
    if (!by || !by.trim()) throw new GateError("unfreezing requires a named person");
    if (this.card.status !== "FROZEN") throw new GateError("card is not frozen", 409);
    this.card.status = "ACTIVE";
    const prevReason = this.card.frozenReason;
    this.card.frozenReason = undefined;
    this.card.frozenAt = undefined;
    this.card.failuresBySession = {};
    this.chain.append("CARD_UNFROZEN", null, { cardId: this.card.id, by: by.trim(), previousReason: prevReason ?? null, failureCountersReset: true });
    return this.card;
  }

  /** Append a free-form narrative/context record (goal, plan, new information) to the evidence chain. */
  note(type: "GOAL_SET" | "AGENT_PLAN" | "NEW_INFORMATION" | "SEED", intentId: string | null, data: unknown): void {
    this.chain.append(type, intentId, data);
  }

  state() {
    return {
      mode: this.client.mode,
      card: this.card,
      policy: {
        ...this.policy,
        perTransactionLimit: formatMinor(this.policy.perTransactionLimitMinor, this.policy.limitCurrency),
      },
      liveSessionId: this.liveSessionId,
      spendTotalsMinor: this.spendTotals(),
      intents: this.listIntents(),
      evidenceLength: this.chain.length,
    };
  }
}

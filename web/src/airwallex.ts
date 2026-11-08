/**
 * Spend execution boundary. PassGate only calls `spend()` after a verified PASS.
 *
 * HONESTY NOTE: this build has NO live Airwallex connection. `SandboxReplayClient`
 * replays outcomes actually observed on the Airwallex sandbox (card
 * "PassGate Acme Analytics", 2026-10-28 .. 2026-11-02). A real client implementing
 * `AirwallexClient` can be dropped in behind the same interface later.
 */

export interface SpendRequest {
  intentId: string;
  merchant: string;
  amountMinor: number;
  currency: string;
  category: string;
  purpose: string;
}

export interface SpendResult {
  status: "SETTLED" | "DECLINED";
  transactionId: string;
  mode: "sandbox-replay";
  /** "observed" = exact match with a real recorded sandbox outcome; "simulated" = no recorded case matched. */
  source: "observed" | "simulated";
  observedCaseId?: string;
  cardTail?: string;
  declineReason?: string;
  note: string;
}

export interface AirwallexClient {
  readonly mode: string;
  spend(req: SpendRequest): Promise<SpendResult>;
}

export interface ObservedCase {
  id: string;
  merchant: string;
  amountMinor: number;
  currency: string;
  /** Category label used by PassGate's replay. The sandbox reports did not carry these labels;
   *  "entertainment" is a stand-in for the disallowed merchant category of obs-5. */
  category: string;
  outcome: "SETTLED" | "DECLINED";
  declineReason?: string;
  cardTail?: string;
  description: string;
}

/** The six outcomes observed on the Airwallex sandbox account (2026-10-28 .. 2026-11-02). */
export const OBSERVED_SANDBOX_CASES: readonly ObservedCase[] = Object.freeze([
  { id: "obs-1", merchant: "Acme Analytics", amountMinor: 120000, currency: "USD", category: "analytics", outcome: "SETTLED", cardTail: "2105", description: "USD 1,200.00 at Acme Analytics, settled (card tail 2105)" },
  { id: "obs-2", merchant: "Acme Analytics", amountMinor: 120000, currency: "USD", category: "analytics", outcome: "SETTLED", cardTail: "3425", description: "USD 1,200.00 at Acme Analytics, settled (card tail 3425)" },
  { id: "obs-3", merchant: "Acme Analytics", amountMinor: 120001, currency: "USD", category: "analytics", outcome: "DECLINED", declineReason: "over spend limit", description: "USD 1,200.01 declined: over spend limit (limit USD 1,200)" },
  { id: "obs-4", merchant: "Acme Analytics", amountMinor: 120000, currency: "EUR", category: "analytics", outcome: "DECLINED", declineReason: "currency restricted", description: "EUR 1,200.00 declined: currency restricted (card allows USD only)" },
  { id: "obs-5", merchant: "Acme Analytics", amountMinor: 1000, currency: "USD", category: "entertainment", outcome: "DECLINED", declineReason: "merchant category not allowed", description: "USD 10.00 declined: merchant category not allowed" },
  { id: "obs-6", merchant: "Acme Analytics", amountMinor: 124801, currency: "USD", category: "analytics", outcome: "DECLINED", declineReason: "over spend limit", description: "USD 1,248.01 declined: over spend limit" },
]);

export class SandboxReplayClient implements AirwallexClient {
  readonly mode = "sandbox-replay";
  private seq = 0;
  private settledUse = new Map<string, number>();

  async spend(req: SpendRequest): Promise<SpendResult> {
    this.seq++;
    const txId = `replay_tx_${String(this.seq).padStart(4, "0")}`;
    const matches = OBSERVED_SANDBOX_CASES.filter(
      (c) =>
        c.merchant.toLowerCase() === req.merchant.trim().toLowerCase() &&
        c.amountMinor === req.amountMinor &&
        c.currency === req.currency.toUpperCase() &&
        // The category-decline case only applies when the category itself matches.
        (c.declineReason !== "merchant category not allowed" || c.category === req.category.trim().toLowerCase()),
    );
    if (matches.length) {
      // Rotate between equivalent observed cases (e.g. the two USD 1,200 settlements on tails 2105 and 3425).
      const key = `${req.amountMinor}:${req.currency}`;
      const n = this.settledUse.get(key) ?? 0;
      this.settledUse.set(key, n + 1);
      const c = matches[n % matches.length];
      return {
        status: c.outcome,
        transactionId: txId,
        mode: "sandbox-replay",
        source: "observed",
        observedCaseId: c.id,
        cardTail: c.cardTail,
        declineReason: c.declineReason,
        note: `Replayed observed Airwallex sandbox outcome ${c.id}: ${c.description}. Not a live API call.`,
      };
    }
    return {
      status: "SETTLED",
      transactionId: txId,
      mode: "sandbox-replay",
      source: "simulated",
      note: "No observed sandbox case matches this exact amount; settlement is SIMULATED in replay mode. Not a live API call.",
    };
  }
}

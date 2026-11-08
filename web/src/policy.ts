/**
 * Spend policy. Defaults are derived from the Airwallex sandbox card
 * "PassGate Acme Analytics" observed 2026-10-28 .. 2026-11-02:
 * per-transaction limit USD 1,200, USD only, merchant Acme Analytics only.
 */
export interface Policy {
  perTransactionLimitMinor: number; // in minor units of limitCurrency
  limitCurrency: string;
  currencyAllowlist: string[];
  merchantAllowlist: string[];
  categoryAllowlist: string[];
  freezeAfterFailures: number;
}

export function defaultPolicy(): Policy {
  return {
    perTransactionLimitMinor: 120000,
    limitCurrency: "USD",
    currencyAllowlist: ["USD"],
    merchantAllowlist: ["Acme Analytics"],
    categoryAllowlist: ["software", "analytics", "cloud", "saas"],
    freezeAfterFailures: 3,
  };
}

/** Case-insensitive, whitespace-normalised comparison for allowlist membership. */
export function norm(s: string): string {
  return s.trim().replace(/\s+/g, " ").toLowerCase();
}

export function inAllowlist(value: string, list: string[]): boolean {
  const v = norm(value);
  return list.some((x) => norm(x) === v);
}

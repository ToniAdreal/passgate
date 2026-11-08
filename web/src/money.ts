/** Money helpers. All amounts are handled internally as integer minor units (cents). */

export function toMinor(amount: number | string): number {
  const n = typeof amount === "string" ? Number(amount.trim()) : amount;
  if (typeof n !== "number" || !Number.isFinite(n) || n < 0) {
    throw new Error(`invalid amount: ${String(amount)}`);
  }
  const minor = Math.round(n * 100);
  if (Math.abs(minor - n * 100) > 1e-6 * Math.max(1, n * 100)) {
    throw new Error(`amount has more than 2 decimal places: ${String(amount)}`);
  }
  return minor;
}

export function formatMinor(minor: number, currency: string): string {
  const sign = minor < 0 ? "-" : "";
  const abs = Math.abs(minor);
  const whole = Math.floor(abs / 100).toLocaleString("en-US");
  const frac = String(abs % 100).padStart(2, "0");
  return `${sign}${currency} ${whole}.${frac}`;
}

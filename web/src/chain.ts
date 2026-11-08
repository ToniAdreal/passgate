import { createHash } from "node:crypto";

export const GENESIS_HASH = "0".repeat(64);

export interface EvidenceRecord {
  index: number;
  ts: string;
  type: string;
  intentId: string | null;
  data: unknown;
  prevHash: string;
  hash: string;
}

export type VerifyResult =
  | { status: "INTACT"; length: number; headHash: string }
  | { status: "BROKEN"; length: number; brokenAt: number; reason: string };

/** Deterministic JSON: object keys sorted recursively, no whitespace. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    if (typeof value === "number" && !Number.isFinite(value)) throw new Error("non-finite number in evidence");
    if (value === undefined) return "null";
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map((v) => canonicalJson(v)).join(",")}]`;
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).filter((k) => obj[k] !== undefined).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`).join(",")}}`;
}

export function sha256Hex(s: string): string {
  return createHash("sha256").update(s, "utf8").digest("hex");
}

/** hash = sha256(prevHash + canonical JSON of the record without its own hash field). */
export function computeHash(rec: Omit<EvidenceRecord, "hash">): string {
  const body = { index: rec.index, ts: rec.ts, type: rec.type, intentId: rec.intentId, data: rec.data, prevHash: rec.prevHash };
  return sha256Hex(rec.prevHash + canonicalJson(body));
}

export function verifyRecords(records: readonly EvidenceRecord[]): VerifyResult {
  let prev = GENESIS_HASH;
  for (let i = 0; i < records.length; i++) {
    const r = records[i];
    if (r.index !== i) return { status: "BROKEN", length: records.length, brokenAt: i, reason: "index mismatch" };
    if (r.prevHash !== prev) return { status: "BROKEN", length: records.length, brokenAt: i, reason: "prevHash does not link to previous record" };
    const expected = computeHash(r);
    if (expected !== r.hash) return { status: "BROKEN", length: records.length, brokenAt: i, reason: "hash does not match record contents" };
    prev = r.hash;
  }
  return { status: "INTACT", length: records.length, headHash: prev };
}

export class EvidenceChain {
  private records: EvidenceRecord[] = [];
  constructor(private clock: () => Date = () => new Date()) {}

  append(type: string, intentId: string | null, data: unknown): EvidenceRecord {
    const prevHash = this.records.length ? this.records[this.records.length - 1].hash : GENESIS_HASH;
    // Deep-copy through JSON so later mutation of caller objects cannot alter sealed evidence.
    const sealedData = data === undefined ? null : JSON.parse(JSON.stringify(data));
    const base = { index: this.records.length, ts: this.clock().toISOString(), type, intentId, data: sealedData, prevHash };
    const rec: EvidenceRecord = { ...base, hash: computeHash(base) };
    this.records.push(rec);
    return rec;
  }

  /** Read-only snapshot (deep copy). */
  list(): EvidenceRecord[] {
    return JSON.parse(JSON.stringify(this.records));
  }

  get length(): number {
    return this.records.length;
  }

  verify(): VerifyResult {
    return verifyRecords(this.records);
  }

  export(): { format: string; algorithm: string; genesisPrevHash: string; records: EvidenceRecord[]; verification: VerifyResult } {
    return {
      format: "passgate-evidence-v1",
      algorithm: "hash = sha256(prevHash + canonicalJson({index,ts,type,intentId,data,prevHash}))",
      genesisPrevHash: GENESIS_HASH,
      records: this.list(),
      verification: this.verify(),
    };
  }

  /** Test/demo hook: direct mutable access to stored records (used to demonstrate tamper detection). */
  unsafeRecords(): EvidenceRecord[] {
    return this.records;
  }
}

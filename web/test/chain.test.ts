import test from "node:test";
import assert from "node:assert/strict";
import { EvidenceChain, canonicalJson, computeHash, verifyRecords } from "../src/chain.js";

test("hash chain verifies INTACT after appends", () => {
  const chain = new EvidenceChain();
  chain.append("INTENT_CREATED", "int_0001", { merchant: "Acme Analytics", amountMinor: 120000 });
  chain.append("DECISION", "int_0001", { decision: "PASS" });
  chain.append("SPEND", "int_0001", { status: "SETTLED" });
  const v = chain.verify();
  assert.equal(v.status, "INTACT");
  assert.equal(v.length, 3);
});

test("tampering with a record is detected at the right index", () => {
  const chain = new EvidenceChain();
  chain.append("INTENT_CREATED", "int_0001", { amountMinor: 120000 });
  chain.append("DECISION", "int_0001", { decision: "PASS" });
  chain.append("SPEND", "int_0001", { status: "SETTLED" });
  assert.equal(chain.verify().status, "INTACT");

  const recs = chain.unsafeRecords();
  recs[1].data = { decision: "BLOCKED" };
  const v = chain.verify();
  assert.equal(v.status, "BROKEN");
  assert.equal(v.brokenAt, 1);
});

test("tampering with the first record breaks the chain at index 0", () => {
  const chain = new EvidenceChain();
  chain.append("INTENT_CREATED", "int_0001", { amountMinor: 120000 });
  chain.append("DECISION", "int_0001", { decision: "PASS" });
  chain.unsafeRecords()[0].data = { amountMinor: 999999 };
  const v = chain.verify();
  assert.equal(v.status, "BROKEN");
  assert.equal(v.brokenAt, 0);
});

test("verifyRecords rejects a broken prevHash link", () => {
  const chain = new EvidenceChain();
  chain.append("INTENT_CREATED", "int_0001", { amountMinor: 120000 });
  const records = chain.list();
  records[0].prevHash = "f".repeat(64);
  const v = verifyRecords(records);
  assert.equal(v.status, "BROKEN");
  assert.equal(v.brokenAt, 0);
});

test("canonical JSON is key-order independent, so hashes are stable", () => {
  const a = canonicalJson({ b: 2, a: { d: 4, c: 3 } });
  const b = canonicalJson({ a: { c: 3, d: 4 }, b: 2 });
  assert.equal(a, b);
  const rec = { index: 0, ts: "2026-11-08T00:00:00.000Z", type: "X", intentId: null, data: { b: 2, a: 1 }, prevHash: "0".repeat(64) };
  assert.equal(computeHash(rec), computeHash({ ...rec, data: { a: 1, b: 2 } }));
});

test("evidence export includes records and a passing verification", () => {
  const chain = new EvidenceChain();
  chain.append("INTENT_CREATED", "int_0001", { amountMinor: 120000 });
  const out = chain.export();
  assert.equal(out.format, "passgate-evidence-v1");
  assert.equal(out.records.length, 1);
  assert.equal(out.verification.status, "INTACT");
});

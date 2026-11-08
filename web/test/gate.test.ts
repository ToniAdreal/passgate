import test from "node:test";
import assert from "node:assert/strict";
import { PassGate } from "../src/gate.js";
import { seedAll } from "../src/seed.js";

function validIntent(gate: PassGate, amount: number | string = 1200) {
  return gate.createIntent({
    merchant: "Acme Analytics",
    amount,
    currency: "USD",
    category: "analytics",
    purpose: "Acme Analytics annual team seat",
  });
}

test("PASS at exactly USD 1,200 and spend settles with an observed-case receipt", async () => {
  const gate = new PassGate();
  const intent = validIntent(gate, 1200);
  gate.approve(intent.id, "finance approver");
  assert.equal(intent.status, "PASS");
  assert.ok(intent.checks.every((c) => c.passed), "every check must pass at the limit");
  const out = await gate.spend(intent.id);
  assert.equal(out.ok, true);
  assert.equal(intent.status, "SPENT");
  assert.ok(out.receipt);
  assert.equal(out.receipt.source, "observed");
  assert.ok(out.receipt.observedCaseId === "obs-1" || out.receipt.observedCaseId === "obs-2");
  assert.equal(out.receipt.amount, "USD 1,200.00");
});

test("over-limit block at USD 1,200.01", () => {
  const gate = new PassGate();
  const intent = validIntent(gate, 1200.01);
  gate.approve(intent.id, "finance approver");
  assert.equal(intent.status, "BLOCKED");
  const amountCheck = intent.checks.find((c) => c.check === "amount_within_limit");
  assert.equal(amountCheck?.passed, false);
  assert.match(intent.blockReasons.join(" "), /over spend limit/);
});

test("over-limit block at USD 1,248.01", () => {
  const gate = new PassGate();
  const intent = validIntent(gate, 1248.01);
  gate.approve(intent.id, "finance approver");
  assert.equal(intent.status, "BLOCKED");
  assert.equal(intent.checks.find((c) => c.check === "amount_within_limit")?.passed, false);
});

test("EUR currency block", () => {
  const gate = new PassGate();
  const intent = gate.createIntent({
    merchant: "Acme Analytics",
    amount: 1200,
    currency: "EUR",
    category: "analytics",
    purpose: "EUR purchase",
  });
  gate.approve(intent.id, "finance approver");
  assert.equal(intent.status, "BLOCKED");
  assert.equal(intent.checks.find((c) => c.check === "currency_allowed")?.passed, false);
  assert.match(intent.blockReasons.join(" "), /currency restricted/);
});

test("disallowed category block", () => {
  const gate = new PassGate();
  const intent = gate.createIntent({
    merchant: "Acme Analytics",
    amount: 10,
    currency: "USD",
    category: "entertainment",
    purpose: "entertainment purchase",
  });
  gate.approve(intent.id, "finance approver");
  assert.equal(intent.status, "BLOCKED");
  assert.equal(intent.checks.find((c) => c.check === "category_allowed")?.passed, false);
  assert.match(intent.blockReasons.join(" "), /category not allowed/);
});

test("non-allowlisted merchant block", () => {
  const gate = new PassGate();
  const intent = gate.createIntent({
    merchant: "Other Vendor",
    amount: 100,
    currency: "USD",
    category: "software",
    purpose: "software from an unapproved vendor",
  });
  gate.approve(intent.id, "finance approver");
  assert.equal(intent.status, "BLOCKED");
  assert.equal(intent.checks.find((c) => c.check === "merchant_allowed")?.passed, false);
  assert.match(intent.blockReasons.join(" "), /merchant not allowed/);
});

test("price change forces re-approval and blocks spend until re-approved", async () => {
  const gate = new PassGate();
  const intent = gate.createIntent({
    merchant: "Acme Analytics",
    amount: 1150,
    currency: "USD",
    category: "analytics",
    purpose: "annual team seat",
    quotedPrice: 1150,
  });
  gate.approve(intent.id, "finance approver");
  assert.equal(intent.status, "PASS");

  gate.reprice(intent.id, 1200, "Acme Analytics checkout price update");
  assert.equal(intent.status, "REAPPROVAL");
  assert.equal(intent.checks.find((c) => c.check === "price_integrity")?.passed, false);

  const refused = await gate.spend(intent.id);
  assert.equal(refused.ok, false);
  assert.equal(intent.status, "REAPPROVAL");

  gate.approve(intent.id, "finance approver");
  assert.equal(intent.status, "PASS");
  const out = await gate.spend(intent.id);
  assert.equal(out.ok, true);
  assert.equal(intent.status, "SPENT");
  assert.equal(out.receipt?.amount, "USD 1,200.00");
});

test("3 failures freeze the card and a frozen card blocks even a valid intent", async () => {
  const gate = new PassGate();
  for (let i = 0; i < 3; i++) {
    const bad = validIntent(gate, 1200.01);
    gate.approve(bad.id, "finance approver");
    assert.equal(bad.status, "BLOCKED");
  }
  assert.equal(gate.card.status, "FROZEN");

  const valid = validIntent(gate, 1200);
  gate.approve(valid.id, "finance approver");
  assert.equal(valid.status, "BLOCKED");
  assert.equal(valid.checks.find((c) => c.check === "card_not_frozen")?.passed, false);

  gate.unfreezeCard("finance approver");
  assert.equal(gate.card.status, "ACTIVE");
  const again = validIntent(gate, 1200);
  gate.approve(again.id, "finance approver");
  assert.equal(again.status, "PASS");
  const out = await gate.spend(again.id);
  assert.equal(out.ok, true);
  assert.equal(again.status, "SPENT");
});

test("reject blocks a pending intent", () => {
  const gate = new PassGate();
  const intent = validIntent(gate, 500);
  gate.reject(intent.id, "finance approver", "not needed this quarter");
  assert.equal(intent.status, "BLOCKED");
  assert.match(intent.blockReasons.join(" "), /rejected by finance approver/);
});

test("seeded observed sandbox cases replay through the live gate and agree with the sandbox", async () => {
  const gate = new PassGate();
  await seedAll(gate);
  const seeded = gate.listIntents().filter((i) => i.origin === "seed-observed");
  assert.equal(seeded.length, 6);
  for (const intent of seeded) {
    assert.ok(intent.observed, `seeded intent ${intent.id} should carry its observed case`);
    assert.equal(intent.observed.gateAgrees, true, `gate disagrees with observed case ${intent.observed.caseId}`);
  }
  const settled = seeded.filter((i) => i.status === "SPENT");
  const blocked = seeded.filter((i) => i.status === "BLOCKED");
  assert.equal(settled.length, 2, "the two USD 1,200 settlements should spend");
  assert.equal(blocked.length, 4, "the four declined cases should block");

  const demo = gate.listIntents().find((i) => i.origin === "demo");
  assert.ok(demo, "demo arc intent should be seeded");
  assert.equal(demo.status, "REAPPROVAL", "demo arc stops at the price-change re-approval step");
});

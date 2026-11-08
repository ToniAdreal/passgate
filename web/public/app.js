/* PassGate dashboard — plain fetch against the server's REST API. No dependencies. */
"use strict";

const $ = (id) => document.getElementById(id);

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function money(minor, currency) {
  const n = (minor ?? 0) / 100;
  return `${currency} ${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

async function api(path, method, body) {
  const res = await fetch(path, {
    method: method || "GET",
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

function statusBadge(status) {
  return `<span class="status status-${esc(status)}">${esc(status)}</span>`;
}

function renderCard(state) {
  const card = state.card;
  $("card-status").innerHTML = `
    <dl>
      <dt>Name</dt><dd>${esc(card.name)}</dd>
      <dt>Status</dt><dd>${statusBadge(card.status)}</dd>
      ${card.frozenReason ? `<dt>Frozen because</dt><dd>${esc(card.frozenReason)}</dd>` : ""}
      ${card.frozenAt ? `<dt>Frozen at</dt><dd>${esc(card.frozenAt)}</dd>` : ""}
      <dt>Failed attempts (live session)</dt><dd>${card.failuresBySession[state.liveSessionId] ?? 0} of ${state.policy.freezeAfterFailures} before freeze</dd>
    </dl>`;
  $("card-actions").innerHTML = card.status === "FROZEN"
    ? `<p><button id="unfreeze-btn" type="button">Unfreeze card</button> (a named person must unfreeze it)</p>`
    : `<p><button id="freeze-btn" type="button">Freeze card (manual)</button></p>`;
  const unfreeze = $("unfreeze-btn");
  if (unfreeze) unfreeze.addEventListener("click", async () => {
    const by = prompt("Unfreeze as (your name):", "finance approver");
    if (!by) return;
    try { await api("/api/card/unfreeze", "POST", { by }); await refresh(); }
    catch (e) { alert(e.message); }
  });
  const freeze = $("freeze-btn");
  if (freeze) freeze.addEventListener("click", async () => {
    try { await api("/api/card/freeze", "POST", { by: "operator", reason: "manual freeze from dashboard" }); await refresh(); }
    catch (e) { alert(e.message); }
  });
}

function renderPolicy(state) {
  const p = state.policy;
  $("policy-summary").innerHTML = `
    <dl>
      <dt>Per-transaction limit</dt><dd>${esc(p.perTransactionLimit)}</dd>
      <dt>Currencies</dt><dd>${p.currencyAllowlist.map(esc).join(", ")}</dd>
      <dt>Merchants</dt><dd>${p.merchantAllowlist.map(esc).join(", ")}</dd>
      <dt>Categories</dt><dd>${p.categoryAllowlist.map(esc).join(", ")}</dd>
      <dt>Freeze rule</dt><dd>${p.freezeAfterFailures} failed attempts in a session freeze the card</dd>
      <dt>Mode</dt><dd>${esc(state.mode)} — spend replays observed sandbox outcomes; no live Airwallex connection</dd>
    </dl>`;
  const totals = state.spendTotalsMinor || {};
  const parts = Object.entries(totals).map(([cur, minor]) => money(minor, cur));
  $("spend-total").textContent = parts.length ? parts.join(", ") : "none yet";
}

function renderNarrative(state) {
  const list = $("narrative");
  const beats = state.narrative || [];
  if (!beats.length) { list.innerHTML = "<li>No demo arc seeded.</li>"; return; }
  list.innerHTML = beats.map((b) => `<li class="${b.done ? "done" : ""}">${esc(b.text)}</li>`).join("");
}

function checksHtml(intent) {
  if (!intent.checks || !intent.checks.length) return "<em>not evaluated yet</em>";
  const items = intent.checks.map((c) =>
    `<li class="${c.passed ? "pass" : "fail"}">${c.passed ? "✓" : "✗"} ${esc(c.check)} — ${esc(c.detail)}</li>`).join("");
  return `<ul class="checks">${items}</ul>`;
}

function renderIntents(state) {
  const rows = $("intent-rows");
  const intents = state.intents || [];
  if (!intents.length) { rows.innerHTML = `<tr><td colspan="7">No intents yet.</td></tr>`; return; }
  rows.innerHTML = intents.map((it) => {
    const reason = (it.blockReasons && it.blockReasons.length)
      ? `<br><small>${it.blockReasons.map(esc).join("<br>")}</small>` : "";
    const receipt = it.receipt
      ? `<br><small>Receipt ${esc(it.receipt.receiptId)} · ${esc(it.receipt.transactionId)} · ${esc(it.receipt.source)}${it.receipt.observedCaseId ? ` · ${esc(it.receipt.observedCaseId)}` : ""}</small>` : "";
    const observed = it.observed
      ? `<br><small>Observed ${esc(it.observed.caseId)}: ${esc(it.observed.outcome)} — gate agrees: ${it.observed.gateAgrees ? "yes" : "NO"}</small>` : "";
    let actions = "";
    if (it.status === "PENDING" || it.status === "REAPPROVAL") {
      actions += `<button type="button" data-act="approve" data-id="${esc(it.id)}">Approve${it.status === "REAPPROVAL" ? " new price" : ""}</button>
        <button type="button" data-act="reject" data-id="${esc(it.id)}">Reject</button>`;
    }
    if (it.status === "PASS") {
      actions += `<button type="button" data-act="spend" data-id="${esc(it.id)}">Spend</button>`;
    }
    if (["PENDING", "PASS", "REAPPROVAL"].includes(it.status)) {
      actions += `<span><input type="number" step="0.01" min="0" placeholder="new price" data-reprice="${esc(it.id)}">
        <button type="button" data-act="reprice" data-id="${esc(it.id)}">Reprice</button></span>`;
    }
    return `<tr>
      <td>${esc(it.id)}${it.origin !== "live" ? `<br><small>${esc(it.origin)}</small>` : ""}</td>
      <td>${esc(it.merchant)}</td>
      <td>${money(it.amountMinor, it.currency)}<br><small>quoted ${money(it.quotedPriceMinor, it.currency)}</small></td>
      <td>${esc(it.category)}</td>
      <td>${statusBadge(it.status)}${reason}${receipt}${observed}</td>
      <td>${checksHtml(it)}</td>
      <td><div class="row-actions">${actions}</div></td>
    </tr>`;
  }).join("");
}

function renderObserved(state) {
  const rows = $("observed-rows");
  const cases = state.observedCases || [];
  const intents = state.intents || [];
  rows.innerHTML = cases.map((c) => {
    const match = intents.find((i) => i.observed && i.observed.caseId === c.id);
    return `<tr>
      <td>${esc(c.id)}</td>
      <td>${esc(c.outcome)}</td>
      <td>${match ? (match.observed.gateAgrees ? "yes" : "NO") : "—"}</td>
      <td>${esc(c.description)}</td>
    </tr>`;
  }).join("");
}

async function renderEvidence() {
  const records = await api("/api/evidence");
  const rows = $("evidence-rows");
  if (!records.length) { rows.innerHTML = `<tr><td colspan="6">No evidence yet.</td></tr>`; return; }
  rows.innerHTML = records.map((r) => `<tr>
    <td>${r.index}</td>
    <td>${esc(r.ts)}</td>
    <td>${esc(r.type)}</td>
    <td>${r.intentId ? esc(r.intentId) : "—"}</td>
    <td class="hash">${esc(r.hash.slice(0, 16))}…</td>
    <td><code>${esc(JSON.stringify(r.data))}</code></td>
  </tr>`).join("");
}

async function refresh() {
  const state = await api("/api/state");
  renderCard(state);
  renderPolicy(state);
  renderNarrative(state);
  renderIntents(state);
  renderObserved(state);
  await renderEvidence();
}

document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  const act = btn.dataset.act;
  try {
    if (act === "approve") {
      const by = prompt("Approve as (your name):", "finance approver");
      if (!by) return;
      await api(`/api/intents/${id}/approve`, "POST", { by });
    } else if (act === "reject") {
      const by = prompt("Reject as (your name):", "finance approver");
      if (!by) return;
      const reason = prompt("Reason (optional):", "");
      await api(`/api/intents/${id}/reject`, "POST", { by, reason: reason || undefined });
    } else if (act === "spend") {
      const out = await api(`/api/intents/${id}/spend`, "POST", { by: "agent" });
      if (!out.ok) alert(`Spend refused: ${out.reason || "not PASS"}`);
    } else if (act === "reprice") {
      const input = document.querySelector(`input[data-reprice="${id}"]`);
      const amount = input && input.value;
      if (!amount) { alert("Enter a new price first."); return; }
      await api(`/api/intents/${id}/reprice`, "POST", { amount, source: "merchant price update (dashboard)" });
    }
    await refresh();
  } catch (e) {
    alert(e.message);
  }
});

$("intent-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const data = Object.fromEntries(new FormData(form).entries());
  const msg = $("form-message");
  msg.className = "";
  msg.textContent = "";
  const body = {
    merchant: data.merchant,
    amount: data.amount,
    currency: data.currency,
    category: data.category,
    purpose: data.purpose,
  };
  if (data.quotedPrice) body.quotedPrice = data.quotedPrice;
  try {
    const intent = await api("/api/intents", "POST", body);
    msg.className = "ok";
    msg.textContent = `Intent ${intent.id} created (PENDING). Approve it to run the PASS gate.`;
    await refresh();
  } catch (e) {
    msg.className = "error";
    msg.textContent = e.message;
  }
});

$("verify-btn").addEventListener("click", async () => {
  const out = $("verify-result");
  try {
    const v = await api("/api/evidence/verify");
    if (v.status === "INTACT") {
      out.className = "intact";
      out.textContent = `Chain INTACT — ${v.length} records, head ${v.headHash.slice(0, 16)}…`;
    } else {
      out.className = "broken";
      out.textContent = `Chain BROKEN at record ${v.brokenAt}: ${v.reason}`;
    }
  } catch (e) {
    out.className = "broken";
    out.textContent = e.message;
  }
});

$("tamper-btn").addEventListener("click", async () => {
  const out = $("verify-result");
  try {
    const r = await api("/api/demo/tamper", "POST", {});
    out.className = "broken";
    out.textContent = `Tampered record ${r.tamperedIndex} (demo only). Verification now: ${r.verification.status}` +
      (r.verification.status === "BROKEN" ? ` — broken at ${r.verification.brokenAt}: ${r.verification.reason}` : "") +
      `. Reset the demo to restore the chain.`;
    await renderEvidence();
  } catch (e) {
    out.className = "broken";
    out.textContent = e.message;
  }
});

$("reset-btn").addEventListener("click", async () => {
  if (!confirm("Reset the demo? This re-seeds the six observed cases and the demo arc.")) return;
  try {
    await api("/api/reset", "POST", { seed: true });
    $("verify-result").textContent = "";
    await refresh();
  } catch (e) { alert(e.message); }
});

refresh().catch((e) => {
  document.querySelector("main").insertAdjacentHTML("afterbegin",
    `<p style="grid-column:1/-1;color:#a50e0e">Failed to load state: ${esc(e.message)}</p>`);
});

"""Tiny local approval screen (stdlib only).

A single static HTML page plus an http.server that reads an ApprovalStore and routes
approve/reject back into the agent. Local, loopback-only, no auth — it is a demo
console for the human-in-the-loop step, not a production endpoint.

  python3 -m passgate.approval_server          # seeds one demo approval, opens on :8777
  # then visit http://127.0.0.1:8777

Routes:
  GET  /                      -> the HTML page
  GET  /api/approvals         -> {"pending": [...], "all": [...]}
  POST /api/decide            -> {"id": ..., "decision": "approve"|"reject", "by": ...}
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from .approvals import ApprovalStore

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PassGate approvals</title>
<style>
  body { font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 820px; color: #1b1b1b; }
  h1 { font-size: 1.4rem; }
  .sub { color: #666; margin-top: -0.6rem; }
  .card { border: 1px solid #ddd; border-radius: 10px; padding: 1rem 1.2rem; margin: 1rem 0; }
  .card.PENDING { border-left: 5px solid #e0a800; }
  .card.APPROVED { border-left: 5px solid #2e7d32; opacity: 0.75; }
  .card.REJECTED { border-left: 5px solid #c62828; opacity: 0.75; }
  .kind { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; color: #777; }
  .vendor { font-weight: 600; font-size: 1.05rem; }
  .amt { font-variant-numeric: tabular-nums; }
  .rise { color: #c62828; font-weight: 600; }
  button { font: inherit; padding: 0.45rem 1rem; border-radius: 8px; border: 0; cursor: pointer; margin-right: 0.5rem; }
  .approve { background: #2e7d32; color: #fff; }
  .reject { background: #c62828; color: #fff; }
  .status { font-size: 0.85rem; color: #555; }
  .empty { color: #777; font-style: italic; }
</style>
</head>
<body>
  <h1>PassGate &mdash; pending approvals</h1>
  <p class="sub">No verified PASS, no spend. Approve to update the card limits and re-arm; reject to keep it frozen.</p>
  <div id="list"></div>
<script>
async function load() {
  const r = await fetch('/api/approvals');
  const data = await r.json();
  const items = data.all;
  const el = document.getElementById('list');
  if (!items.length) { el.innerHTML = '<p class="empty">Nothing waiting. The agent is auto-handling renewals within threshold.</p>'; return; }
  el.innerHTML = items.map(a => {
    const rise = a.rise_pct != null ? `<span class="rise">${a.rise_pct}%</span>` : '';
    const money = (a.old_amount != null)
      ? `<div class="amt">${a.old_amount} &rarr; ${a.new_amount} ${a.currency} ${rise}</div>` : '';
    const actions = a.status === 'PENDING'
      ? `<div><button class="approve" onclick="decide('${a.id}','approve')">Approve</button>`
        + `<button class="reject" onclick="decide('${a.id}','reject')">Reject</button></div>`
      : `<div class="status">${a.status}${a.decided_by ? ' by ' + a.decided_by : ''}</div>`;
    return `<div class="card ${a.status}">
      <div class="kind">${a.kind}</div>
      <div class="vendor">${a.vendor}</div>
      <div>${a.summary}</div>
      ${money}
      ${actions}
    </div>`;
  }).join('');
}
async function decide(id, decision) {
  const by = prompt('Your name (recorded in the audit log):', 'Finance approver');
  if (by === null) return;
  await fetch('/api/decide', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ id, decision, by })
  });
  load();
}
load();
</script>
</body>
</html>
"""


def make_server(store: ApprovalStore, on_decide=None, host: str = "127.0.0.1", port: int = 8777) -> HTTPServer:
    """Build (but do not start) an HTTPServer over `store`.

    on_decide(approval_id, approve: bool, by: str) -> dict applies the decision (wire
    it to PassGate.decide_approval). If omitted, the store is updated directly.
    """
    def _default_decide(approval_id, approve, by):
        (store.approve if approve else store.reject)(approval_id, by)
        return {"status": "APPROVED" if approve else "REJECTED", "approval_id": approval_id}

    decide = on_decide or _default_decide

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            payload = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if self.path == "/api/approvals":
                return self._send(200, json.dumps({
                    "pending": [a.to_dict() for a in store.pending()],
                    "all": [a.to_dict() for a in store.all()],
                }))
            return self._send(404, json.dumps({"error": "not_found"}))

        def do_POST(self):  # noqa: N802
            if self.path != "/api/decide":
                return self._send(404, json.dumps({"error": "not_found"}))
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                aid, decision = body["id"], body["decision"]
                by = body.get("by") or "Finance approver"
            except (ValueError, KeyError) as e:
                return self._send(400, json.dumps({"error": "bad_request", "detail": str(e)}))
            if decision not in ("approve", "reject"):
                return self._send(400, json.dumps({"error": "decision must be approve|reject"}))
            try:
                result = decide(aid, decision == "approve", by)
            except (KeyError, ValueError) as e:
                return self._send(409, json.dumps({"error": str(e)}))
            return self._send(200, json.dumps(result, default=str))

        def log_message(self, *args):  # silence default stderr spam
            pass

    return HTTPServer((host, port), Handler)


def _demo_store() -> ApprovalStore:
    store = ApprovalStore()
    store.add(kind="renewal_price_rise", vendor="Acme Analytics",
              summary="Acme Analytics renewal up 15.0% (1200.00 -> 1380.00 USD); approve to update the card.",
              currency="USD", old_amount="1200.00", new_amount="1380.00", rise_pct="15.0",
              detail={"threshold_pct": "10", "billing_term": "MONTHLY"})
    return store


def main() -> int:
    store = _demo_store()
    srv = make_server(store)
    host, port = srv.server_address
    print(f"PassGate approval screen on http://{host}:{port}  (Ctrl-C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

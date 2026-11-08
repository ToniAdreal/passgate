import { createServer, IncomingMessage, Server, ServerResponse } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { GateError, PassGate } from "./gate.js";
import { narrative, seedAll } from "./seed.js";
import { OBSERVED_SANDBOX_CASES } from "./airwallex.js";

const PUBLIC_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../public");
const STATIC: Record<string, string> = {
  "/": "index.html",
  "/index.html": "index.html",
  "/app.js": "app.js",
  "/style.css": "style.css",
};
const MIME: Record<string, string> = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8" };

export interface AppHandle {
  server: Server;
  gate(): PassGate;
  reset(seed: boolean): Promise<PassGate>;
}

function send(res: ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}): void {
  const json = JSON.stringify(body, null, 2);
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers });
  res.end(json);
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > 64 * 1024) throw new GateError("request body too large", 413);
    chunks.push(chunk as Buffer);
  }
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) return {};
  try {
    const v = JSON.parse(raw);
    if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error();
    return v as Record<string, unknown>;
  } catch {
    throw new GateError("body must be a JSON object");
  }
}

function str(v: unknown, fallback: string): string {
  return typeof v === "string" && v.trim() ? v.trim() : fallback;
}

export async function createApp(opts: { seed?: boolean } = {}): Promise<AppHandle> {
  let gate = new PassGate();
  const reset = async (seed: boolean) => {
    gate = new PassGate();
    if (seed) await seedAll(gate);
    return gate;
  };
  await reset(opts.seed ?? true);

  const handler = async (req: IncomingMessage, res: ServerResponse) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    const p = url.pathname.replace(/\/+$/, "") || "/";
    const m = req.method ?? "GET";
    try {
      if (m === "GET" && STATIC[p]) {
        const file = path.join(PUBLIC_DIR, STATIC[p]);
        const body = await readFile(file);
        res.writeHead(200, { "content-type": MIME[path.extname(file)] ?? "application/octet-stream", "cache-control": "no-store" });
        res.end(body);
        return;
      }
      if (m === "GET" && p === "/api/health") return send(res, 200, { ok: true, mode: gate.client.mode });
      if (m === "GET" && p === "/api/state") return send(res, 200, { ...gate.state(), narrative: narrative(gate), observedCases: OBSERVED_SANDBOX_CASES });
      if (m === "GET" && p === "/api/narrative") return send(res, 200, narrative(gate));
      if (m === "GET" && p === "/api/intents") return send(res, 200, gate.listIntents());
      if (m === "POST" && p === "/api/intents") {
        const b = await readJson(req);
        const intent = gate.createIntent(b as never, { actor: str(b.actor, "agent") });
        return send(res, 201, intent);
      }
      const im = p.match(/^\/api\/intents\/([A-Za-z0-9_]+)(?:\/(approve|reject|spend|reprice))?$/);
      if (im) {
        const [, id, action] = im;
        if (!action && m === "GET") return send(res, 200, gate.getIntent(id));
        if (action && m === "POST") {
          const b = await readJson(req);
          if (action === "approve") {
            const intent = gate.approve(id, str(b.by, "approver"));
            if (b.spend === true && intent.status === "PASS") {
              const out = await gate.spend(id, str(b.by, "approver"));
              return send(res, out.ok ? 200 : 409, out.intent);
            }
            return send(res, 200, intent);
          }
          if (action === "reject") return send(res, 200, gate.reject(id, str(b.by, "approver"), typeof b.reason === "string" ? b.reason : undefined));
          if (action === "reprice") {
            if (b.amount === undefined) throw new GateError("amount is required");
            return send(res, 200, gate.reprice(id, b.amount as number | string, str(b.source, "merchant price update")));
          }
          if (action === "spend") {
            const out = await gate.spend(id, str(b.by, "agent"));
            return send(res, out.ok ? 200 : 409, out);
          }
        }
      }
      if (m === "POST" && p === "/api/card/unfreeze") {
        const b = await readJson(req);
        return send(res, 200, gate.unfreezeCard(str(b.by, "")));
      }
      if (m === "POST" && p === "/api/card/freeze") {
        const b = await readJson(req);
        return send(res, 200, gate.freezeCard(str(b.by, "operator"), str(b.reason, "manual freeze")));
      }
      if (m === "GET" && p === "/api/evidence") return send(res, 200, gate.chain.list());
      if ((m === "GET" || m === "POST") && p === "/api/evidence/verify") return send(res, 200, gate.chain.verify());
      if (m === "GET" && p === "/api/evidence/export") {
        return send(res, 200, gate.chain.export(), { "content-disposition": 'attachment; filename="passgate-evidence.json"' });
      }
      if (m === "POST" && p === "/api/demo/tamper") {
        // Demo-only: mutate one stored record in memory to show that verification detects it.
        const b = await readJson(req);
        const recs = gate.chain.unsafeRecords();
        if (!recs.length) throw new GateError("evidence chain is empty", 409);
        const idx = typeof b.index === "number" ? b.index : Math.floor(recs.length / 2);
        if (!Number.isInteger(idx) || idx < 0 || idx >= recs.length) throw new GateError("index out of range");
        recs[idx].data = { ...(recs[idx].data as object), tampered: true };
        return send(res, 200, { tamperedIndex: idx, verification: gate.chain.verify() });
      }
      if (m === "POST" && p === "/api/reset") {
        const b = await readJson(req);
        await reset(b.seed !== false);
        return send(res, 200, { ok: true, seeded: b.seed !== false, intents: gate.listIntents().length });
      }
      return send(res, 404, { error: "not found" });
    } catch (e) {
      if (e instanceof GateError) return send(res, e.httpStatus, { error: e.message });
      console.error(e);
      return send(res, 500, { error: "internal error" });
    }
  };

  const server = createServer((req, res) => {
    handler(req, res).catch((e) => {
      console.error(e);
      if (!res.headersSent) send(res, 500, { error: "internal error" });
    });
  });
  return { server, gate: () => gate, reset };
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const port = Number(process.env.PORT ?? 8080);
  const app = await createApp({ seed: process.env.SEED !== "0" });
  app.server.listen(port, () => {
    console.log(`PassGate listening on http://localhost:${port} (mode: sandbox-replay; no live Airwallex connection)`);
  });
}

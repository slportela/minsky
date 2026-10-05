import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../lib/api.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function client(fetch) {
  const context = { exports: {}, fetch };
  vm.runInNewContext(compiled, context);
  return context.exports;
}

test("credential stays in Authorization and out of customer messages", async () => {
  let request;
  const api = client(async (url, init) => {
    request = { url, ...init };
    return { ok: true, json: async () => ({ conversation_id: "conversation", messages: [{ agent: "ok" }] }) };
  });
  await api.postChatTurn({ credential: "test-credential", messages: [{ user: "cargo" }] });
  assert.equal(request.url, "/api/chat/turn");
  assert.equal(request.headers.Authorization, "Bearer test-credential");
  assert.equal(request.headers["X-Minsky-Customer-Id"], undefined);
  assert.deepEqual(JSON.parse(request.body), { messages: [{ user: "cargo" }] });
  assert.ok(!request.body.includes("test-credential"));
});

test("expired-session error preserves backend code and status", async () => {
  const api = client(async () => ({
    ok: false,
    status: 401,
    json: async () => ({ code: "session_expired", message: "Session expired", request_id: "request" }),
  }));
  await assert.rejects(api.postChatTurn({ credential: "expired", messages: [{ user: "cargo" }] }), (error) => {
    assert.equal(error.status, 401);
    assert.equal(error.code, "session_expired");
    assert.equal(error.requestId, "request");
    return true;
  });
});

test("the flow travels only on the first turn of a conversation", async () => {
  const requests = [];
  const api = client(async (url, init) => {
    requests.push({ url, body: JSON.parse(init.body) });
    return { ok: true, json: async () => ({ conversation_id: "c", messages: [{ agent: "ok" }], mode: "agentic" }) };
  });
  await api.postChatTurn({ credential: "t", messages: [{ user: "hola" }], mode: "agentic" });
  await api.postChatTurn({ credential: "t", conversationId: "c", messages: [{ user: "hola" }], mode: "agentic" });
  await api.postChatTurn({ credential: "t", messages: [{ user: "hola" }] });
  assert.equal(requests[0].body.mode, "agentic");
  assert.equal("mode" in requests[1].body, false); // a running conversation keeps the flow it started with
  assert.equal("mode" in requests[2].body, false); // nothing chosen: the server decides
  assert.equal(requests[1].body.conversation_id, "c");
});

test("the chat options are read from the server, not from the bundle", async () => {
  let request;
  const api = client(async (url, init) => {
    request = { url, init };
    return { ok: true, json: async () => ({ mode_switch: true, mode: "workflow" }) };
  });
  assert.deepEqual(await api.getChatOptions(), { mode_switch: true, mode: "workflow" });
  assert.equal(request.url, "/api/chat/options");
});

test("the operator credential goes in Authorization and the customer id only in the body", async () => {
  const requests = [];
  const api = client(async (url, init) => {
    requests.push({ url, ...init });
    return {
      ok: true,
      json: async () => ({ credential: "demo-s-x", customer_id: "CLI-A", first_name: "Ana", country: "Mexico", operator_id: "equipo", expires_at: "2099-01-01T00:00:00Z" }),
    };
  });
  await api.postDemoSession("op-credential", { customerId: "CLI-A" });
  await api.postDemoSession("op-credential", { random: true });
  assert.equal(requests[0].url, "/api/demo/session");
  assert.equal(requests[0].headers.Authorization, "Bearer op-credential");
  assert.deepEqual(JSON.parse(requests[0].body), { customer_id: "CLI-A" });
  assert.deepEqual(JSON.parse(requests[1].body), { random: true });
  assert.ok(!requests[0].body.includes("op-credential"));
});

test("a credential that is not an operator's is not an error: the page treats it as a customer's", async () => {
  for (const status of [404, 401]) {
    const api = client(async () => ({ ok: false, status, json: async () => ({ code: "x", message: "m", request_id: "r" }) }));
    assert.equal(await api.getDemoOperator("customer-credential"), null);
  }
  const ok = client(async () => ({ ok: true, json: async () => ({ operator_id: "jurado" }) }));
  assert.equal(await ok.getDemoOperator("op"), "jurado");
  const down = client(async () => ({ ok: false, status: 503, json: async () => ({ code: "service_unavailable", message: "m", request_id: "r" }) }));
  await assert.rejects(down.getDemoOperator("op"), (error) => error.status === 503);
});

test("the chosen customer's recent charges come back with the policy's reading, or null when they could not be read", async () => {
  const charge = {
    transaction_id: "T1", merchant: "Cafe", amount: "25.00", currency: "USD", amount_usd: "25.00", date: "2026-06-08",
    transaction_type: "Purchase", status: "Approved", route: "open_dispute", rule_id: "D09-eligible",
    hint: "el cargo cumple las condiciones para abrir el reclamo", existing_dispute_id: null,
    suggested_message: "Quiero reclamar un cargo de 25.00 USD en Cafe del 2026-06-08, el monto no es correcto.",
  };
  const session = { credential: "demo-s-x", customer_id: "CLI-A", first_name: "Ana", country: "Mexico", operator_id: "equipo", expires_at: "2099-01-01T00:00:00Z" };
  const withCharges = client(async () => ({ ok: true, json: async () => ({ ...session, recent_charges: [charge] }) }));
  const got = await withCharges.postDemoSession("op", { customerId: "CLI-A" });
  assert.equal(got.recent_charges[0].suggested_message, charge.suggested_message);
  const without = client(async () => ({ ok: true, json: async () => ({ ...session, recent_charges: null }) }));
  assert.equal((await without.postDemoSession("op", { random: true })).recent_charges, null);
});

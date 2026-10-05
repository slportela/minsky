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

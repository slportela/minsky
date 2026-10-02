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

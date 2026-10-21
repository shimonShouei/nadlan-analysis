import assert from "node:assert/strict";
import test from "node:test";
import { __resetRateLimit, handleRequest } from "../worker/src/index.js";

function post(body, ip = "203.0.113.10") {
  return new Request("https://search.example/parse", {
    method: "POST",
    headers: { "content-type": "application/json", "CF-Connecting-IP": ip },
    body: JSON.stringify(body),
  });
}

function gemini(text) {
  return async () => new Response(JSON.stringify({
    candidates: [{ content: { parts: [{ text }] } }],
  }), { status: 200, headers: { "content-type": "application/json" } });
}

test("worker returns a validated query and drops a hallucinated median", async () => {
  __resetRateLimit();
  const text = JSON.stringify({
    settlement: "באר שבע",
    apartments_only: true,
    rooms_min: 4,
    rooms_max: 4,
    year_min: 2023,
    ppsqm_max: 15000,
    median_price: 123456,
    answer: "the median is 123456",
  });
  const res = await handleRequest(post({
    q: "דירות 4 חדרים בבאר שבע",
    settlements: ["באר שבע", "חיפה"],
    deal_types: ["דירת גג"],
  }), { GEMINI_API_KEY: "test-key" }, gemini(text));
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.query.settlement, "באר שבע");
  assert.equal(data.query.ppsqm_max, 15000);
  assert.equal(data.query.rooms_min, 4);
  assert.equal(data.model, "gemini-3.8-flash");
  assert.ok(data.ignored.includes("median_price"));
  assert.ok(data.ignored.includes("answer"));
  assert.equal(JSON.stringify(data).includes("123456"), false);
  assert.equal(res.headers.get("access-control-allow-origin"), "*");
});

test("worker retries a schema 400 and still validates", async () => {
  __resetRateLimit();
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    if (calls === 1) return new Response("bad schema", { status: 400 });
    return new Response(JSON.stringify({
      candidates: [{ content: { parts: [{ text: '```json\n{"settlement":"חיפה","near":false}\n```' }] } }],
    }), { status: 200 });
  };
  const res = await handleRequest(post({ q: "חיפה", settlements: ["חיפה"] }, "203.0.113.11"), {
    GEMINI_API_KEY: "k",
  }, fetchImpl);
  assert.equal(calls, 2);
  const data = await res.json();
  assert.equal(data.query.settlement, "חיפה");
});

test("missing key, bad json, and rate limit", async () => {
  __resetRateLimit();
  const none = await handleRequest(post({ q: "חיפה" }, "203.0.113.12"), {});
  assert.equal(none.status, 500);
  const bad = await handleRequest(new Request("https://search.example/parse", {
    method: "POST",
    headers: { "CF-Connecting-IP": "203.0.113.13" },
    body: "{",
  }), { GEMINI_API_KEY: "k" }, gemini("{}"));
  assert.equal(bad.status, 400);

  __resetRateLimit();
  const ip = "203.0.113.14";
  for (let i = 0; i < 8; i++) {
    const ok = await handleRequest(post({ q: "חיפה", settlements: ["חיפה"] }, ip), { GEMINI_API_KEY: "k" }, gemini('{"settlement":"חיפה"}'));
    assert.equal(ok.status, 200, `request ${i}`);
  }
  const limited = await handleRequest(post({ q: "חיפה" }, ip), { GEMINI_API_KEY: "k" }, gemini("{}"));
  assert.equal(limited.status, 429);
  assert.ok(Number(limited.headers.get("retry-after")) > 0);
});

test("preflight and health do not expose the key", async () => {
  const opt = await handleRequest(new Request("https://search.example/parse", { method: "OPTIONS" }), {
    GEMINI_API_KEY: "super-secret",
  });
  assert.equal(opt.status, 204);
  const health = await handleRequest(new Request("https://search.example/health"), { GEMINI_API_KEY: "super-secret", AI: {} });
  const body = await health.json();
  assert.equal(body.gemini, true);
  assert.equal(body.workers_ai, true);
  assert.equal(JSON.stringify(body).includes("super-secret"), false);
});

test("Workers AI is preferred when the binding exists", async () => {
  __resetRateLimit();
  const env = {
    GEMINI_API_KEY: "should-not-be-used",
    AI: {
      async run(model, payload) {
        assert.equal(model, "@cf/meta/llama-3.2-3b-instruct");
        assert.match(payload.messages[0].content, /באר שבע/);
        assert.match(payload.messages[1].content, /דירות/);
        return { response: '{"settlement":"באר שבע","ppsqm_max":15000,"made_up":1}' };
      },
    },
  };
  const res = await handleRequest(post({
    q: "דירות בבאר שבע",
    settlements: ["באר שבע"],
  }, "203.0.113.15"), env, () => { throw new Error("gemini was called"); });
  const data = await res.json();
  assert.equal(data.model, "@cf/meta/llama-3.2-3b-instruct");
  assert.equal(data.query.settlement, "באר שבע");
  assert.equal(data.query.ppsqm_max, 15000);
  assert.ok(data.ignored.includes("made_up"));
});

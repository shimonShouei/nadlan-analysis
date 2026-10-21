// Cloudflare Worker: turn a free-text real-estate question into the shared
// query schema. The API key stays here. The response is only validated filter
// fields — never a price, a median, or a ranking. The page computes those
// from its own data and still works when this URL is not configured.

import { extractJson, queryJsonSchema, systemPrompt, validateQuery } from "../../site/query.js";

const WINDOW_MS = 60_000;
const MAX_PER_IP = 8;
const MAX_GLOBAL = 30;
const MAX_QUERY = 500;
const MAX_SETTLEMENTS = 4000;
const buckets = new Map();
let globalWindow = { start: 0, count: 0 };

export function __resetRateLimit() {
  buckets.clear();
  globalWindow = { start: 0, count: 0 };
}

export function allowRequest(ip, now = Date.now()) {
  if (now - globalWindow.start > WINDOW_MS) globalWindow = { start: now, count: 0 };
  globalWindow.count += 1;
  let bucket = buckets.get(ip);
  if (!bucket || now - bucket.start > WINDOW_MS) {
    bucket = { start: now, count: 0 };
    buckets.set(ip, bucket);
  }
  bucket.count += 1;
  if (buckets.size > 5000) {
    for (const [key, value] of buckets) {
      if (now - value.start > WINDOW_MS) buckets.delete(key);
    }
  }
  const ipOk = bucket.count <= MAX_PER_IP;
  const globalOk = globalWindow.count <= MAX_GLOBAL;
  const retryAfter = Math.max(1, Math.ceil((WINDOW_MS - (now - bucket.start)) / 1000));
  return { ok: ipOk && globalOk, retryAfter };
}

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "POST, OPTIONS, GET",
  "access-control-allow-headers": "content-type",
  "access-control-max-age": "86400",
  "cache-control": "no-store",
};

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...CORS, ...extra },
  });
}

function clientIp(request) {
  return request.headers.get("CF-Connecting-IP") || "unknown";
}

function clipList(value, maxItems, maxLen) {
  if (!Array.isArray(value)) return [];
  const out = [];
  for (const item of value) {
    if (typeof item !== "string") continue;
    const text = item.trim().slice(0, maxLen);
    if (!text || out.includes(text)) continue;
    out.push(text);
    if (out.length >= maxItems) break;
  }
  return out;
}

function modelText(data) {
  if (!data) return "";
  if (typeof data.response === "string") return data.response;
  if (data.response && typeof data.response === "object") return JSON.stringify(data.response);
  const parts = data.candidates?.[0]?.content?.parts || [];
  return parts.filter(part => !part.thought).map(part => part.text || "").join("");
}

async function runWorkersAi(env, prompt, question) {
  const model = env.WORKERS_AI_MODEL || "@cf/meta/llama-3.2-3b-instruct";
  const data = await env.AI.run(model, {
    messages: [
      { role: "system", content: prompt },
      { role: "user", content: question },
    ],
    max_tokens: 700,
  });
  return { text: modelText(data), model };
}

async function runGemini(env, prompt, question, fetchImpl, withSchema) {
  const model = env.GEMINI_MODEL || "gemini-3.8-flash";
  const generationConfig = { maxOutputTokens: 2048 };
  if (withSchema) {
    generationConfig.responseFormat = {
      text: { mimeType: "APPLICATION_JSON", schema: queryJsonSchema() },
    };
  }
  const res = await fetchImpl(
    `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
    {
      method: "POST",
      headers: { "content-type": "application/json", "x-goog-api-key": env.GEMINI_API_KEY },
      body: JSON.stringify({
        contents: [{ role: "user", parts: [{ text: `${prompt}\n\nQuestion:\n${question}` }] }],
        generationConfig,
      }),
    },
  );
  if (!res.ok && withSchema && res.status === 400) return runGemini(env, prompt, question, fetchImpl, false);
  if (!res.ok) {
    const err = new Error("gemini_failed");
    err.status = res.status;
    throw err;
  }
  return { text: modelText(await res.json()), model };
}

export async function handleRequest(request, env = {}, fetchImpl = globalThis.fetch) {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  const url = new URL(request.url);
  if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
    return json({
      ok: true,
      gemini: Boolean(env.GEMINI_API_KEY),
      workers_ai: Boolean(env.AI),
    });
  }
  if (request.method !== "POST" || (url.pathname !== "/" && url.pathname !== "/parse")) {
    return json({ error: "not_found" }, 404);
  }
  const limit = allowRequest(clientIp(request));
  if (!limit.ok) return json({ error: "rate_limited" }, 429, { "retry-after": String(limit.retryAfter) });

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "bad_json" }, 400);
  }
  const question = typeof body.q === "string" ? body.q.trim().slice(0, MAX_QUERY) : "";
  if (!question) return json({ error: "empty_query" }, 400);
  const settlements = clipList(body.settlements, MAX_SETTLEMENTS, 80);
  const dealTypes = clipList(body.deal_types, 40, 40);
  const prompt = systemPrompt({
    settlements,
    dealTypes,
    truncated: settlements.length >= MAX_SETTLEMENTS,
  });

  let ran;
  try {
    if (env.AI) ran = await runWorkersAi(env, prompt, question);
    else if (env.GEMINI_API_KEY) ran = await runGemini(env, prompt, question, fetchImpl, true);
    else return json({ error: "no_model_configured" }, 500);
  } catch {
    return json({ error: "model_failed" }, 502);
  }
  const parsed = extractJson(ran.text);
  if (!parsed || typeof parsed !== "object") return json({ error: "model_failed" }, 502);
  const { query, ignored } = validateQuery(parsed, { dealTypes });
  return json({ query, ignored, model: ran.model });
}

export default {
  fetch(request, env) {
    return handleRequest(request, env);
  },
};

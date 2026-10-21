# nadlan-search worker

Tier 2 of the map's natural-language search. The static site calls `POST /parse` with the user's question and the settlement names it already loaded. This worker holds the LLM key and returns only the shared query JSON (`site/query.js`). It does not answer with prices or rankings; the page computes those from `site/data`.

The page works with `site/config.json` → `worker_url` left empty. Tier 1 is Chrome's on-device Prompt API. Tier 3 is the in-page text matcher.

## Free model

Default is the Gemini API free tier, model `gemini-3.8-flash` (override with the `GEMINI_MODEL` var). The key is a secret, not a var:

```
cd worker
npx wrangler secret put GEMINI_API_KEY
npx wrangler deploy
```

Put the deployed URL in `site/config.json`:

```json
{ "worker_url": "https://nadlan-search.<account>.workers.dev" }
```

Requests send the question, the settlement names, and the deal-type labels. They do not send deals. Google's free tier may use that text to improve its models; see the Gemini pricing page. Nothing is sent unless this URL is set and the on-device model is unavailable.

### Workers AI instead

Uncomment the `[ai]` binding in `wrangler.toml`. The script uses Workers AI when `env.AI` exists, otherwise Gemini. Set `WORKERS_AI_MODEL` to change the model (default `@cf/meta/llama-3.2-3b-instruct`). Workers AI is not included on every free Cloudflare account; if `AI.run` fails the client falls back to text matching.

## Limits

- 8 requests per IP per minute, and 30 per minute for the whole isolate. A 429 carries `Retry-After`.
- The counters live in the isolate's memory. A different isolate has its own counters, so this is a quota guard, not a global account limit.
- Questions are truncated to 500 characters. Settlement lists are capped at 4,000 names.
- The model output is parsed and passed through `validateQuery`. Unknown fields (for example a made-up median) are dropped and listed in `ignored`. A 400 from Gemini's JSON schema is retried once without the schema, then validated the same way.

## Local check

From the repo root, with no Cloudflare account:

```
node --test tests/worker.test.mjs
```

`npx wrangler dev` in this folder serves the worker locally. Put `http://127.0.0.1:8787` in `site/config.json` while you try it, and do not commit a non-empty URL that points at your own key.

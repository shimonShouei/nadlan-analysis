// Search box. Tier 1: Chrome Prompt API (Gemini Nano) when it is already
// downloaded. Tier 2: the Worker in ../worker, if site/config.json has a URL.
// Tier 3: parseHeuristic in query.js. Every number on screen comes from the
// map data via NadlanMap, not from the model text.

import {
  describeQuery, fillBlanks, finalizeQuery, parseHeuristic, queryJsonSchema,
  rankSettlements, resolveSettlement, resolveStreet, shortlistSettlements,
  systemPrompt, validateQuery, extractJson,
} from "./query.js";

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const money = new Intl.NumberFormat("he-IL", { style: "currency", currency: "ILS", maximumFractionDigits: 0 });

const EXAMPLES = [
  ["4 חדרים בבאר שבע", "דירות 4 חדרים בבאר שבע מתחת ל-15 אלף למ\"ר מאז 2023"],
  ["הכי זול ליד תל אביב", "איפה הכי זול לקנות דירה ליד תל אביב"],
];

let workerUrl = "";
let localSession = null;
let localMode = null;
let last = null;

function mapApi() {
  return window.NadlanMap || null;
}

async function loadConfig() {
  try {
    const res = await fetch("config.json", { cache: "no-store" });
    if (!res.ok) return;
    const cfg = await res.json();
    workerUrl = typeof cfg.worker_url === "string" ? cfg.worker_url.trim().replace(/\/$/, "") : "";
  } catch { /* the map works with no config file */ }
}

async function detectLocal() {
  const LM = globalThis.LanguageModel;
  if (LM?.availability) {
    // Hebrew is not in the Prompt API language list (builds that list
    // de/en/es/fr/ja abort the call if "he" is requested). The question text
    // is still sent as-is; only the declared language is English, and the
    // JSON values are resolved against the Hebrew settlement list afterwards.
    const opts = {
      expectedInputs: [{ type: "text", languages: ["en"] }],
      expectedOutputs: [{ type: "text", languages: ["en"] }],
    };
    try {
      const status = await LM.availability(opts);
      return { LM, opts, legacy: false, status };
    } catch { /* older Chrome builds throw instead of returning unavailable */ }
  }
  const legacy = globalThis.ai?.languageModel;
  if (!legacy?.capabilities) return null;
  try {
    const caps = await legacy.capabilities();
    const status = caps.available === "readily" ? "available"
      : caps.available === "after-download" ? "downloadable" : "unavailable";
    return { LM: legacy, opts: null, legacy: true, status };
  } catch {
    return null;
  }
}

function statusLine(kind) {
  const el = $("search-status");
  if (!el) return;
  if (kind === "device") el.textContent = "חיפוש חכם זמין במכשיר (Gemini Nano). המודל רק מפרש את השאלה.";
  else if (kind === "downloadable") el.textContent = "מודל Chrome זמין להורדה. בינתיים החיפוש הוא לפי טקסט, או דרך השרת אם הוגדר.";
  else if (kind === "worker") el.textContent = "חיפוש חכם דרך השרת כשאין מודל במכשיר. המודל רק מפרש את השאלה.";
  else el.textContent = "חיפוש לפי שם יישוב ומספרים בטקסט. בלי מודל, המשפט צריך להכיל את שם היישוב.";
}

async function ensureSession(detected) {
  if (localSession) return localSession;
  const prompt = systemPrompt({ listsFollow: true });
  const createOpts = { signal: AbortSignal.timeout(8000) };
  if (detected.legacy) {
    localSession = await detected.LM.create({ ...createOpts, systemPrompt: prompt });
  } else {
    localSession = await detected.LM.create({
      ...detected.opts,
      ...createOpts,
      initialPrompts: [{ role: "system", content: prompt }],
    });
  }
  return localSession;
}

async function promptFits(session, text) {
  try {
    if (typeof session.measureInputUsage === "function") {
      const used = await session.measureInputUsage(text);
      const quota = session.inputQuota ?? Infinity;
      const soFar = session.inputUsage ?? 0;
      return used + soFar < quota * 0.9;
    }
  } catch { /* measure is optional */ }
  return text.length < 8000;
}

async function promptLocal(question, names, dealTypes, detected) {
  if (!detected || detected.status !== "available") return null;
  const session = await ensureSession(detected);
  const schema = queryJsonSchema();
  let list = names;
  let truncated = false;
  let user = buildUser(question, list, dealTypes, truncated);
  if (!(await promptFits(session, user))) {
    list = shortlistSettlements(question, names, 60);
    truncated = true;
    user = buildUser(question, list, dealTypes, truncated);
  }
  let text;
  try {
    text = await session.prompt(user, { responseConstraint: schema });
  } catch {
    text = await session.prompt(user + "\nJSON only.");
  }
  return extractJson(text);
}

function buildUser(question, names, dealTypes, truncated) {
  const note = truncated
    ? "The settlement list is a shortlist. If the place is missing, set settlement null and settlement_raw to the user's spelling."
    : "Copy settlement from this list only.";
  return `${note}\nDeal types:\n${dealTypes.join("\n") || "(none)"}\n\nSettlements:\n${names.join("\n")}\n\nQuestion:\n${question}`;
}

async function promptWorker(question, names, dealTypes) {
  if (!workerUrl) return null;
  const res = await fetch(`${workerUrl}/parse`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ q: question, settlements: names, deal_types: dealTypes }),
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.query ? { query: data.query, ignored: data.ignored || [] } : null;
}

async function interpret(question, settlements, dealTypes, detected) {
  const names = settlements.map(s => s.name);
  const heuristic = parseHeuristic(question, settlements, { dealTypes });
  let source = "text";
  let ignored = [];
  let model = null;
  try {
    model = await promptLocal(question, names, dealTypes, detected);
    if (model) source = "device";
  } catch { model = null; }
  if (!model) {
    try {
      const remote = await promptWorker(question, names, dealTypes);
      if (remote?.query) {
        model = remote.query;
        ignored = remote.ignored || [];
        source = "worker";
      }
    } catch { /* text fallback */ }
  }
  if (!model) return { query: heuristic, source, ignored };
  const validated = validateQuery(model, { dealTypes });
  ignored = ignored.concat(validated.ignored);
  let query = fillBlanks(validated.query, heuristic);
  const resolved = resolveSettlement(query.settlement, names)
    || resolveSettlement(query.settlement_raw, names)
    || heuristic.settlement
    || resolveSettlement(question, names);
  query = finalizeQuery({ ...query, settlement: resolved });
  if (!query.street) query.street = heuristic.street;
  return { query, source, ignored };
}

function render(state) {
  const out = $("search-out");
  if (!out) return;
  const { query, source, ignored, described, ranked, basis } = state;
  const sourceText = source === "device"
    ? "פוענח במכשיר. המספרים להלן מחושבים מהנתונים באתר."
    : source === "worker"
      ? "פוענח בשרת. המספרים להלן מחושבים מהנתונים באתר."
      : "פוענח בלי מודל, לפי שמות ומספרים בטקסט.";
  const chips = described.applied.map(label => `<span class="chip">${esc(label)}</span>`).join("");
  const skipped = described.skipped.map(label => `<p class="search-warn">${esc(label)}</p>`).join("");
  const notes = described.notes.map(label => `<p class="search-note">${esc(label)}</p>`).join("");
  const dropped = ignored.length ? `<p class="search-note">המודל החזיר שדות שלא בסכימה והם הושמטו.</p>` : "";
  const rankCaption = basis === "ppsqm_hist" || basis === "price_hist"
    ? "חציון מחושב מההיסטוגרמה של היישוב לשנים שביקשת."
    : basis === "price_hist"
      ? "חציון מחיר דירה מההיסטוגרמה."
      : "חציון למ\"ר כפי שנשמר ליישוב (דירות מאז שנת הצבע).";
  const ranks = ranked.length
    ? `<p class="search-note">${esc(rankCaption)}</p>` + ranked.map(row => {
      const km = row.km == null ? "" : `${row.km.toFixed(1)} ק"מ · `;
      const unit = row.basis === "price_hist" ? "" : " למ\"ר";
      return `<button type="button" class="rank" data-lon="${row.lon}" data-lat="${row.lat}">${esc(row.name)} · ${km}${esc(money.format(Math.round(row.value)))}${unit}</button>`;
    }).join("")
    : "";
  out.innerHTML = `<p class="search-note">${sourceText}</p><div class="chips">${chips}</div>${skipped}${notes}${dropped}${ranks}`;
  out.querySelectorAll(".rank").forEach(btn => {
    btn.addEventListener("click", () => mapApi()?.flyTo(Number(btn.dataset.lon), Number(btn.dataset.lat), 13));
  });
}

function contextFor(streetStatus) {
  const api = mapApi();
  return {
    zoomedIn: api ? api.zoomedIn() : false,
    roomsInAreaStats: api ? api.roomsInAreaStats() : false,
    streetStatus,
  };
}

async function run(question) {
  const api = mapApi();
  const out = $("search-out");
  if (!api) return;
  $("search-examples").hidden = Boolean(question);
  if (!question) {
    api.clearQuery();
    last = null;
    if (out) out.innerHTML = "";
    $("search-clear").hidden = true;
    return;
  }
  if (out) out.innerHTML = `<p class="search-note">מפרש…</p>`;
  await api.whenReady();
  const settlements = api.getSettlements() || [];
  const meta = api.getMeta() || {};
  const dealTypes = meta.types || [];
  const detected = await detectLocal();
  const { query, source, ignored } = await interpret(question, settlements, dealTypes, detected);

  let streetStatus = query.street ? "need_settlement" : "none";
  let streetHit = null;
  if (query.street && query.settlement) {
    const loaded = await api.loadStreets(query.settlement);
    if (loaded.status === "missing") streetStatus = "no_index";
    else {
      streetHit = resolveStreet(query.street, loaded.streets);
      streetStatus = streetHit ? "resolved" : "not_in_index";
      if (streetHit) query.street = streetHit.name;
    }
  }

  const ranked = query.rank ? rankSettlements(query, settlements, {
    ppsqmEdges: meta.ppsqm_edges,
    priceEdges: meta.price_edges,
    colorFrom: meta.color_from,
  }) : [];
  const described = describeQuery(query, contextFor(streetStatus));
  api.setQuery(query, { street: streetHit ? streetHit.name : "" });

  const target = streetHit
    ? { lon: streetHit.lon, lat: streetHit.lat, zoom: 16 }
    : ranked[0]
      ? { lon: ranked[0].lon, lat: ranked[0].lat, zoom: 13 }
      : (query.settlement && settlements.find(s => s.name === query.settlement));
  if (target && target.lon != null) api.flyTo(target.lon, target.lat, target.zoom || 13);

  last = { query, source, ignored, described, ranked, basis: ranked[0]?.basis || null, streetStatus };
  render(last);
  $("search-clear").hidden = false;
}

function refreshCoverage() {
  if (!last) return;
  last.described = describeQuery(last.query, contextFor(last.streetStatus));
  render(last);
}

function wire() {
  const form = $("search-form");
  const input = $("search-q");
  if (!form || !input) return;
  form.addEventListener("submit", ev => {
    ev.preventDefault();
    run(input.value.trim()).catch(err => {
      const out = $("search-out");
      if (out) out.innerHTML = `<p class="search-warn">החיפוש נכשל. ${esc(err.message || "")}</p>`;
    });
  });
  $("search-clear").addEventListener("click", () => {
    input.value = "";
    run("");
  });
  input.addEventListener("keydown", ev => {
    if (ev.key === "Escape") { input.value = ""; run(""); }
  });
  const examples = $("search-examples");
  EXAMPLES.forEach(([label, text]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "example";
    btn.textContent = label;
    btn.addEventListener("click", () => { input.value = text; run(text); });
    examples.appendChild(btn);
  });
  mapApi()?.onView(() => refreshCoverage());
}

await loadConfig();
wire();
const detected = await detectLocal();
if (detected?.status === "available") statusLine("device");
else if (detected?.status === "downloadable") statusLine(workerUrl ? "worker" : "downloadable");
else statusLine(workerUrl ? "worker" : "text");

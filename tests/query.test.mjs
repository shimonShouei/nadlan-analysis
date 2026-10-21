import assert from "node:assert/strict";
import test from "node:test";
import {
  addressOnStreet, dealPasses, describeQuery, extractJson, fillBlanks, flatPasses,
  medianFromHist, medianOfBins, parseHeuristic, queryJsonSchema, rankSettlements,
  resolveSettlement, resolveStreet, systemPrompt, validateQuery,
} from "../site/query.js";

const PLACES = [
  { name: "תל אביב-יפו", lon: 34.78, lat: 32.085, n: 1000, ppsqm: 60000 },
  { name: "חולון", lon: 34.77, lat: 32.015, n: 400, ppsqm: 28000 },
  { name: "הרצלייה", lon: 34.84, lat: 32.16, n: 200, ppsqm: 45000 },
  { name: "באר שבע", lon: 34.79, lat: 31.25, n: 800, ppsqm: 14000 },
  { name: "חיפה", lon: 34.99, lat: 32.79, n: 700, ppsqm: 25000 },
  { name: "פתח תקווה", lon: 34.89, lat: 32.09, n: 300, ppsqm: 32000 },
];

const APT = ["דירה בבית קומות", "דירת גן", "דירת גג", "ד. מגורים"];

test("validateQuery drops invented facts and unknown fields", () => {
  const { query, ignored, ok } = validateQuery({
    settlement: "באר שבע",
    ppsqm_max: 15000,
    median_price: 999999,
    answer: "חולון is cheapest",
    rank: "cheapest_ppsqm",
  });
  assert.equal(ok, true);
  assert.deepEqual(ignored.sort(), ["answer", "median_price"]);
  assert.equal(query.settlement, "באר שבע");
  assert.equal(query.ppsqm_max, 15000);
  assert.equal(query.rank, "cheapest_ppsqm");
  assert.equal("median_price" in query, false);
  assert.equal(query.answer, undefined);
});

test("validateQuery swaps inverted ranges and rejects a bad rank", () => {
  const { query } = validateQuery({
    year_min: 2024, year_max: 2020, rooms_min: 5, rooms_max: 2, rank: "lowest",
    ppsqm_max: 15,
  });
  assert.equal(query.year_min, 2020);
  assert.equal(query.year_max, 2024);
  assert.equal(query.rooms_min, 2);
  assert.equal(query.rooms_max, 5);
  assert.equal(query.rank, null);
  assert.equal(query.ppsqm_max, 15000);
});

test("validateQuery nulls an impossible year and keeps deal types on the list", () => {
  const { query } = validateQuery({
    year_min: 1800,
    deal_types: ["דירת גג", "לא סוג", "דירת גג"],
  }, { dealTypes: APT });
  assert.equal(query.year_min, null);
  assert.deepEqual(query.deal_types, ["דירת גג"]);
});

test("schema has no field for a computed price or a winning place", () => {
  const props = queryJsonSchema().properties;
  for (const banned of ["median", "median_price", "winner", "answer", "cheapest_settlement"]) {
    assert.equal(props[banned], undefined);
  }
  assert.equal(queryJsonSchema().additionalProperties, false);
});

test("extractJson reads a fenced object and ignores prose", () => {
  const parsed = extractJson('Sure.\n```json\n{"settlement":"חיפה","rank":null}\n```\nThanks');
  assert.equal(parsed.settlement, "חיפה");
  assert.equal(extractJson("no json here"), null);
});

test("Beer Sheva apartment question", () => {
  const q = parseHeuristic(
    "דירות 4 חדרים בבאר שבע מתחת ל-15 אלף למ\"ר מאז 2023",
    PLACES,
  );
  assert.equal(q.settlement, "באר שבע");
  assert.equal(q.apartments_only, true);
  assert.equal(q.rooms_min, 4);
  assert.equal(q.rooms_max, 4);
  assert.equal(q.ppsqm_max, 15000);
  assert.equal(q.year_min, 2023);
  assert.equal(q.year_max, null);
  assert.equal(q.near, false);
  assert.equal(q.rank, null);
  assert.equal(q.price_max, null);
});

test("cheapest apartment near Tel Aviv resolves the official name", () => {
  const q = parseHeuristic("איפה הכי זול לקנות דירה ליד תל אביב", PLACES);
  assert.equal(q.settlement, "תל אביב-יפו");
  assert.equal(q.near, true);
  assert.equal(q.radius_km, 20);
  assert.equal(q.apartments_only, true);
  assert.equal(q.rank, "cheapest_ppsqm");
});

test("English question: street, city alias, year, whole-unit price", () => {
  const q = parseHeuristic("apartments on Herzl street in Haifa since 2019 under 3 million", PLACES);
  assert.equal(q.settlement, "חיפה");
  assert.equal(q.street, "Herzl");
  assert.equal(q.year_min, 2019);
  assert.equal(q.price_max, 3_000_000);
  assert.equal(q.ppsqm_max, null);
  assert.equal(q.apartments_only, true);
});

test("Hebrew spelling variants and a street in a sentence", () => {
  assert.equal(resolveSettlement("דירות בהרצליה", PLACES), "הרצלייה");
  assert.equal(resolveSettlement("petah tikva flats", PLACES), "פתח תקווה");
  const q = parseHeuristic("דירות ברחוב הרצל בחיפה מעל 80 מ\"ר", PLACES);
  assert.equal(q.settlement, "חיפה");
  assert.equal(q.street, "הרצל");
  assert.equal(q.area_min, 80);
});

test("between thousands per metre, and at least N rooms", () => {
  const q = parseHeuristic("בין 10 ל-20 אלף למ\"ר, לפחות 3 חדרים, בבאר שבע", PLACES);
  assert.equal(q.ppsqm_min, 10000);
  assert.equal(q.ppsqm_max, 20000);
  assert.equal(q.rooms_min, 3);
  assert.equal(q.rooms_max, null);
  assert.equal(q.settlement, "באר שבע");
});

test("rank uses settlement medians from the data, not a model field", () => {
  const q = parseHeuristic("איפה הכי זול לקנות דירה ליד תל אביב", PLACES);
  const ranked = rankSettlements(q, PLACES.map(p => ({ ...p, planted: 1 })));
  assert.equal(ranked[0].name, "חולון");
  assert.equal(ranked[0].value, 28000);
  assert.equal(ranked[0].basis, "ppsqm_field");
  assert.ok(ranked.every(row => row.name !== "באר שבע"));
  assert.ok(ranked[0].km < 20);
});

test("year-limited rank reads the histogram", () => {
  const edges = [10000, 20000, 40000, 80000];
  const places = PLACES.map(p => ({ ...p, ph: { 2024: [[0, 10]] } }));
  const cheap = { ...places[1], ph: { 2024: [[1, 10]] } };
  places[1] = cheap;
  const q = { ...parseHeuristic("ליד תל אביב", PLACES), rank: "cheapest_ppsqm", year_min: 2024 };
  const ranked = rankSettlements(q, places, { ppsqmEdges: edges, colorFrom: 2020 });
  const holon = ranked.find(row => row.name === "חולון");
  assert.equal(holon.basis, "ppsqm_hist");
  assert.ok(holon.value > 20000 && holon.value < 40000);
});

test("medianOfBins interpolates in log space", () => {
  const value = medianOfBins(new Map([[0, 10]]), [1000, 10000]);
  assert.ok(Math.abs(value - Math.sqrt(10_000_000)) < 1e-6);
  const hist = medianFromHist({ 2022: [[0, 4]], 2024: [[0, 10]] }, [1000, 10000], 2023, null);
  assert.equal(hist.n, 10);
});

test("flat and deal filters, including the share price", () => {
  const q = parseHeuristic("דירות 4 חדרים מתחת ל-15 אלף למ\"ר מאז 2023", PLACES);
  assert.equal(flatPasses([2024, 1200, 80, 4], q, { rooms: true }), true);
  assert.equal(flatPasses([2024, 2000, 80, 4], q, { rooms: true }), false);
  assert.equal(flatPasses([2024, 1200, 80, 3], q, { rooms: true }), false);
  assert.equal(flatPasses([2024, 1200, 80], q, { rooms: false }), true);
  const apt = "דירה בבית קומות";
  assert.equal(dealPasses({ d: "2024-05-01", t: apt, r: 4, a: 80, paid: 1_000_000, p: 1 }, q, APT), true);
  assert.equal(dealPasses({ d: "2024-05-01", t: apt, r: 4, a: 80, paid: 1_000_000, p: 0.5 }, q, APT), false);
  assert.equal(dealPasses({ d: "2022-05-01", t: apt, r: 4, a: 80, paid: 800_000, p: 1 }, q, APT), false);
});

test("coverage says when rooms and streets were not applied", () => {
  const q = parseHeuristic("דירות 4 חדרים ברחוב הרצל בבאר שבע", PLACES);
  const far = describeQuery(q, { zoomedIn: false, roomsInAreaStats: false, streetStatus: "no_index" });
  assert.ok(far.applied.some(line => line.includes("ברשימת העסקאות")));
  assert.ok(far.skipped.some(line => line.includes("חדרים")));
  assert.ok(far.skipped.some(line => line.includes("אינדקס הרחובות")));
  const hit = describeQuery(q, { zoomedIn: true, roomsInAreaStats: true, streetStatus: "resolved" });
  assert.equal(hit.skipped.some(line => line.includes("חדרים")), false);
  assert.ok(hit.applied.some(line => line.includes("הרצל")));
});

test("fillBlanks keeps an explicit model filter and backfills a missed city", () => {
  const model = validateQuery({ ppsqm_max: 15000, apartments_only: true }).query;
  const heuristic = parseHeuristic("דירות בבאר שבע מתחת ל-15 אלף למ\"ר", PLACES);
  const query = fillBlanks(model, heuristic);
  assert.equal(query.settlement, "באר שבע");
  assert.equal(query.ppsqm_max, 15000);
  assert.equal(query.apartments_only, true);
});

test("street resolution prefers the indexed name", () => {
  const hit = resolveStreet("הרצל", [["הרצל", 34.8, 31.25, 40], ["המלך הרצל", 34.81, 31.26, 2]]);
  assert.equal(hit.name, "הרצל");
  assert.equal(addressOnStreet("הרצל 12", "הרצל"), true);
  assert.equal(addressOnStreet("המלך הרצל 3", "הרצל"), false);
});

test("system prompt carries the Hebrew examples and the live settlement list", () => {
  const prompt = systemPrompt({ settlements: ["באר שבע", "תל אביב-יפו"], dealTypes: ["דירת גג"] });
  assert.match(prompt, /דירות 4 חדרים בבאר שבע/);
  assert.match(prompt, /איפה הכי זול לקנות דירה ליד תל אביב/);
  assert.match(prompt, /do not compute, estimate, or invent prices/i);
  assert.match(prompt, /באר שבע/);
  assert.match(prompt, /דירת גג/);
  assert.match(prompt, /תל אביב-יפו/);
});

# nadlan-analysis

Basic exploratory analysis of Israel Tax Authority real-estate deals (~3.8M rows, 1998 to 2026).
Data comes from [over.org.il](https://www.over.org.il/versions/fd06f5ae-8a4f-4120-b275-8a514ad23499).

## Files
- `analysis.ipynb`: the notebook, already executed with outputs
- `build_notebook.py`: generates the notebook. Edit cells here, or edit the notebook directly.
- `data/`: cache of the parsed CSV, created on first run (git-ignored)

## Parcel addresses
The deals data has gush/chelka but no address. `scripts/fetch_parcel_addresses.py` looks up each
unique parcel (~381k) on GovMap: it finds the parcel, then gets its official address(es). It runs
at ~9 parcels/s (~11h total), saves progress in `data/parcel_addresses.sqlite`, and resumes when rerun.

```
uv run python scripts/fetch_parcel_addresses.py            # fetch (or --limit N to test)
uv run python scripts/fetch_parcel_addresses.py --export   # -> data/parcel_addresses.parquet
```

The export has one row per (gush, chelka, address), with the parcel's label point as `lon`/`lat`
(and `x`/`y` in EPSG:3857). `status` is `ok`, `no_address` (the parcel exists but has no address,
e.g. open land) or `parcel_not_found` (the parcel isn't in the current cadastre, usually because it
was re-parcelled). Join to deals on `gush, chelka`. `data/blocks.parquet` has one point per gush.

The October 2026 crawl is published as a release, so you don't need to re-crawl:
[data-2026-10](https://github.com/shimonShouei/nadlan-analysis/releases/tag/data-2026-10).

## Map site
A static map of all deals in Israel: click a building to see its deals, or open the stats tab for the area in view.
Rebuild the data after the crawl, then preview locally:

```
uv run python scripts/fetch_parcel_addresses.py --blocks    # fallback location per gush (~12k lookups)
uv run python -I scripts/match_buildings.py                 # needs data/osm/israel-and-palestine-latest.osm.pbf (Geofabrik)
uv run python -I scripts/build_site_data.py                 # writes site/data/
uv run python -m http.server --directory site               # open http://localhost:8000
```

## Natural-language search

The map has a search box. A question in Hebrew or English becomes a filter (settlement, street, rooms, apartment / deal type, years, whole-unit price, price per m², area) and, when the question asks where it is cheapest, a ranking. The model is only allowed to fill that filter. Medians, distances and rankings are computed in the browser from `settlements.json` and the cell files. Fields outside the schema are dropped and never shown.

Three tiers, first one that works:

1. **On device.** Chrome's Prompt API (`LanguageModel`, Gemini Nano) when `availability()` is `"available"`. The question and the settlement names stay in the browser. A model that still needs downloading is not started automatically.
2. **Worker.** If `site/config.json` has a non-empty `worker_url`, the page `POST`s the question and the settlement names to that Cloudflare Worker (`worker/`). The key lives in the worker. See `worker/README.md` for `wrangler secret put GEMINI_API_KEY` and deploy. Leave `worker_url` empty and this tier is skipped; the map still loads.
3. **Text.** Settlement and street names are matched against the loaded lists (including a few English aliases such as "Haifa"), and numbers are read from phrases like "4 חדרים", "מתחת ל-15 אלף למ\"ר" and "מאז 2023".

What the current files can filter:

- Deal list of a clicked building: year, type, rooms, area, whole-unit price (`paid / portion`), price per m². This already works on existing deal files.
- Zoomed-in area stats: year, type, and (from the per-point flat rows) price, price per m² and area. Rooms are included only after the data is rebuilt; until then the panel says the room filter was not applied to the area summary.
- Zoomed-out stats: year, type, and price bands via the existing histograms (about 4% bins). Area and rooms are not applied there, and the panel says so.
- Streets: `build_site_data.py` writes `street_index.json` plus one file per settlement under `streets/`. The page fetches a settlement's file only when a search names it. Until that rebuild, a street in the question is reported as not applied.

The coloured map squares stay the unfiltered median price per m². Rebuild site data with `uv run python -I scripts/build_site_data.py` to pick up rooms on the flat rows and the street index.

```
node --test tests/query.test.mjs tests/worker.test.mjs
uv run python -m unittest tests/test_street_index.py
```

## Run
The notebook reads `~/Downloads/taxes-nadlan-full-f41fb496_append.csv` by default. Set `NADLAN_CSV`
to use another path. The first run takes about 1 minute to parse; later runs load from the cache.

```
uv sync                              # or: pip install pandas matplotlib jupyter pyarrow python-bidi
uv run jupyter lab analysis.ipynb
```

`pyarrow` (Parquet cache) and `python-bidi` (Hebrew labels) are optional. The notebook falls back
to a pickle cache and a simple Hebrew string reversal if they're missing.

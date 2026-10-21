# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single exploratory-analysis notebook over Israel Tax Authority real-estate deals (~3.8M rows, 1998–2026,
from over.org.il). No package. Python 3.12, managed with `uv`. The map search has unit tests
(`node --test tests/query.test.mjs tests/worker.test.mjs` and `python -m unittest tests/test_street_index.py`).

## Commands

```
uv sync                                                    # install deps
uv run python build_notebook.py                            # regenerate analysis.ipynb from source (clears outputs)
uv run jupyter nbconvert --to notebook --execute --inplace analysis.ipynb   # re-execute to refresh outputs
uv run jupyter lab analysis.ipynb                          # interactive
```

## How the pieces fit

- **`build_notebook.py` is the source of truth** for `analysis.ipynb`. It builds every cell with `md(...)`/`code(...)`
  helpers via `nbformat` and overwrites the notebook. Running it drops all outputs and any edits made directly in the
  notebook, so make changes in `build_notebook.py`, then regenerate and re-execute. The committed notebook is expected
  to be executed, with outputs.
- **Data loading:** the raw CSV lives outside the repo (`~/Downloads/taxes-nadlan-full-f41fb496_append.csv`, override
  with `NADLAN_CSV`). The first run parses it with explicit dtypes (~1 min) and caches the *raw* frame to
  `data/deals.parquet` (or `data/deals.pkl` without pyarrow). `data/` is git-ignored. The cache holds pre-cleaning
  data, so cleaning changes take effect without clearing it. Delete the cache only when changing the `read_csv` call.
- **Notebook flow:** 1 Load -> 2 Data quality (profile; builds `df` by dedupe, date parse, settlement-name normalization,
  no values changed) -> 3 Cleaning -> 4 Deal volume -> 5 Apartments.
- **Apartment definition (3.2):** `APT_TYPES` = `דירה בבית קומות`, `דירת גן`, `דירת גג` plus `ד. מגורים`, the *old label for the
  same flats* (used until 2003-04; leaving it out loses most of 1998-2003). `מגורים` is land, not flats. Penthouse/duplex labels
  do not exist in the data. All years 1998+ are used.
- **Cleaning (section 3):** field-level first, row-level second. Placeholder zeros and impossible values
  (`year_built`, `asset_area`, `room_num`, `portion`) are set to NaN in `df` and the rows are kept. Rows are dropped only
  when building `apt`: `APT_TYPES`, known `portion`, settlement and area known, then **plausibility rather than fixed caps**
  (area 15-1,000 m2, price/m2 1K-150K, median/MAD local outlier on log price per m2 within each settlement-year), so real
  extreme deals stay. Each filter step is in a waterfall table; `cand` holds every candidate with its `drop_reason`.
- **Partial shares:** `deal_amount` is the price of the *share* sold (checked against whole-unit deals in 3.5), so
  `unit_price = deal_amount / portion` and `ppsqm = unit_price / asset_area`. Section 5 must use `unit_price`, not `deal_amount`.
  `partial` is the separate frame of `portion < 1` deals (with verdict and examples). Localities where under 20% of deals are
  whole units are excluded from the city ranking.
  Missing area/price is dropped, never imputed. Missing rooms/build year keep the row and are dropped per chart.
  `apt`, `RECENT` (apt, 2024+) and `APT_TYPES` feed section 5, so changing a cleaning rule changes every price chart.
- **The summary cell is hand-written** with hard-coded numbers. If an analysis change moves the results, update the
  summary text to match.

## Conventions

- Prices are nominal; 2026 is a partial year and is labeled or excluded in charts.
- Use medians, not means (outliers reach 10⁹ m² and ₪9B).
- Charts use the palette `C` and `INK`/`INK2`/`GRID` from the setup cell (based on the dataviz skill), left-aligned bold
  titles, and the `shekels`/`thousands` tick formatters. Two measures with different scales get two charts, not a dual
  axis.
- Wrap all Hebrew text passed to matplotlib in `heb()`. It is the identity on matplotlib >= 3.11, which reorders right-to-left
  text itself (reordering again reverses it), and uses python-bidi `get_display` on older versions.
  Font is Arial for Hebrew glyphs on Windows.

## Map site (all of Israel)

Goal: a public map where every building with deals is clickable and lists its deals, plus a stats tab for the area in view.
Static site, no backend; preview with `python -m http.server --directory site`, then open `http://localhost:8000`.

Pipeline (each step reads the previous one's output; `data/` and `site/data/` are git-ignored):
1. `scripts/fetch_parcel_addresses.py` (GovMap scrape, resumable) -> `data/parcel_addresses.sqlite`: one label point
   per parcel (EPSG:3857) plus addresses. `--blocks` -> `data/blocks.sqlite`, one point per gush (separate file because the
   parcel crawl holds its db's write lock for a whole batch).
2. `scripts/match_buildings.py` -> `data/parcel_building.parquet`, `data/buildings.parquet`. Reads the Geofabrik
   `data/osm/israel-and-palestine-latest.osm.pbf`, keeps only buildings near a parcel point, matches each point to the
   building it is inside, else the nearest within 15 m.
3. `scripts/build_site_data.py` -> `site/data/`: `meta.json`, `settlements.json`, `grid.geojson`, `cells/<cx>_<cy>.json` (0.02° squares:
   buildings, points), `stats/<cx>_<cy>.json` (per-point deal summaries), `deals/<gush>.json` (loaded on click). Peak memory ~1 GB by design
   (the user's machine is short on RAM): avoid full-table merges of the deals frame.

Deal locations: parcel point if GovMap knows the parcel; otherwise the block point (parcel re-parcelled since the deal),
shown as a hollow violet circle; otherwise the deal counts only at settlement level.

`site/index.html`: MapLibre GL + OpenFreeMap tiles, Hebrew RTL UI. Needs the `mapbox-gl-rtl-text` plugin, or map labels
render reversed. Below zoom 14 it shows price squares (`grid.geojson`: 1 km below z10, 500 m to z12, 250 m to z14,
each the median price per m² of flats since `COLOR_FROM`, drawn only with >= 3 such flats); from 14 it loads the grid
cells in view. `settlements.json` is no longer drawn; it feeds only the zoomed-out stats.
- Performance: the GeoJSON sources hold only the cells in view (each `setData` re-tiles everything it gets), source
  `maxzoom` is 15, stats summaries live in separate `stats/` files fetched only while the stats tab is open, and the
  basemap's `building-3d` layer is removed. Keep it that way; it was 2-6 s per pan before.
- Colour: median price per m² of flats sold since `COLOR_FROM` (2020); thresholds are national quantiles written to `meta.json`.
  The median uses `unit_price` (see Partial shares above).
- Stats tab: zoomed in, exact from the cells' per-point summaries inside the view; zoomed out, from settlements in view,
  with medians from summed log-spaced histograms (~4% resolution). "Recent" = `RECENT_FROM` (2024+).
- Deals attach to parcels, not buildings, so a click shows the parcel's deals; a building can hold several parcels, and a
  parcel several buildings. The panel says so and offers the whole block.
- The `suspect` flag in `build_site_data.py` duplicates the notebook's plausibility tests (minus the local-outlier test).
  Factor into a shared module before this grows.

### Natural-language search

`site/query.js` is the schema, the validator, the Hebrew/English text parser, and the ranking helper. `site/search.js` wires the box. `site/config.json` holds `worker_url` (empty in git). Tiers: Chrome Prompt API when `LanguageModel.availability()` is `"available"`; else `POST /parse` on the Worker in `worker/` if a URL is set; else the text parser. The Worker (see `worker/README.md`) keeps `GEMINI_API_KEY` as a secret, rate-limits per IP in the isolate (8/minute, plus a 30/minute isolate cap), and validates with the same `validateQuery`. Unknown JSON fields are dropped. The model must not supply prices or a winning place; `rankSettlements` reads `ppsqm` or the `ph`/`uh` histograms.

Filters land on the open stats tab and on the deal table of a selected parcel. Price filters use whole-unit price (`paid / portion`, or `price_k * 1000` in the cell flats), not the share paid. Map colours are not refiltered. Cells in view are still the only GeoJSON given to the map; a street match sets `qhit` on those features so other buildings fade. Street files are fetched per settlement on search, not during pan.

`build_site_data.py` adds two things, both inside the existing memory budget (the street pass is freed before building geometries):

- Flat rows in `stats/` are `[year, price_k, area_m2, rooms]`. `rooms` is null when unknown. Older files with 3-tuples still load; the room filter then applies only to the deal list, and the panel says the area summary ignored it.
- `street_index.json` maps a deal settlement name to `streets/<sha>.json` (`[street, lon, lat, n]`). Streets come from the address table's `street` column (not the concatenated "street house" label), located at the median parcel point, and named with the settlement string from the deals so it matches `settlements.json`. A published `site/data` built before this change has no index until `build_site_data.py` is run again.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single exploratory-analysis notebook over Israel Tax Authority real-estate deals (~3.8M rows, 1998–2026,
from over.org.il). No package code, no tests. Python 3.12, managed with `uv`.

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

## Map site prototype (Tel Aviv)

Goal: a public map where every building with deals is clickable and lists its deals (block-level fallback when a parcel is ambiguous).
Static site, no backend; preview with `python -m http.server --directory site`, then open `http://localhost:8000`.

- `scripts/fetch_parcel_addresses.py` (GovMap scrape, resumable, writes `data/parcel_addresses.sqlite`) gives one label point per parcel (EPSG:3857) plus addresses.
- `scripts/match_buildings.py` assigns each parcel point to an OpenStreetMap building (inside, else nearest within 15 m). Needs `data/osm/<city>_buildings.json` from an Overpass `way["building"]; out geom;` query (send a User-Agent or Overpass returns 406).
- `scripts/build_site_data.py` (run without `-I`, it imports `match_buildings`) writes `site/data/` (git-ignored): `buildings.geojson`, `parcels.geojson` (unmatched parcels), `deals/<gush>.json` (loaded on click).
- `site/index.html`: MapLibre GL + OpenFreeMap tiles, Hebrew RTL UI. Colours buildings by median price per m² of flats; the median uses `unit_price` (see Partial shares above).
- Deals attach to parcels, not buildings, so a click shows the parcel's deals; a building can hold several parcels, and a parcel several buildings. The panel says so and offers the whole block.
- The `suspect` flag in `build_site_data.py` duplicates the notebook's plausibility tests (minus the local-outlier test). Factor into a shared module before this grows.

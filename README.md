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

## Run
The notebook reads `~/Downloads/taxes-nadlan-full-f41fb496_append.csv` by default. Set `NADLAN_CSV`
to use another path. The first run takes about 1 minute to parse; later runs load from the cache.

```
uv sync                              # or: pip install pandas matplotlib jupyter pyarrow python-bidi
uv run jupyter lab analysis.ipynb
```

`pyarrow` (Parquet cache) and `python-bidi` (Hebrew labels) are optional. The notebook falls back
to a pickle cache and a simple Hebrew string reversal if they're missing.

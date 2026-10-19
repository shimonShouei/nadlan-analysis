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

The export has one row per (gush, chelka, address). `status` is `ok`, `no_address` (the parcel
exists but has no address, e.g. open land) or `parcel_not_found` (the parcel isn't in the current
cadastre, usually because it was re-parcelled). Join to deals on `gush, chelka`.

## Run
The notebook reads `~/Downloads/taxes-nadlan-full-f41fb496_append.csv` by default. Set `NADLAN_CSV`
to use another path. The first run takes about 1 minute to parse; later runs load from the cache.

```
uv sync                              # or: pip install pandas matplotlib jupyter pyarrow python-bidi
uv run jupyter lab analysis.ipynb
```

`pyarrow` (Parquet cache) and `python-bidi` (Hebrew labels) are optional. The notebook falls back
to a pickle cache and a simple Hebrew string reversal if they're missing.

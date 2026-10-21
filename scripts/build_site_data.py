"""Build the static data files for the map site (all of Israel).

Reads  data/deals.parquet, data/parcel_building.parquet + data/buildings.parquet (match_buildings.py),
       data/parcel_addresses.sqlite and data/blocks.sqlite (fetch_parcel_addresses.py)
Writes site/data/meta.json             deal types, colour thresholds, list of grid cells
       site/data/settlements.json      one point per settlement with yearly counts and price histograms (zoomed-out stats)
       site/data/grid.geojson          price squares of GRID_M metres (the zoomed-out map, like a price heat map)
       site/data/cells/<cx>_<cy>.json  one CELL x CELL degree square: buildings and parcel/block points (drawn when zoomed in)
       site/data/stats/<cx>_<cy>.json  the same square's per-point deal summaries, loaded only for the stats tab
       site/data/deals/<gush>.json     all deals of one block, loaded on click
       site/data/street_index.json settlement name -> streets/<id>.json
       site/data/streets/<id>.json [street, lon, lat, parcel count], fetched when a search names that settlement

Where a deal sits on the map: its parcel's point when GovMap knows the parcel, otherwise its block's point
(the parcel was re-parcelled since the deal). Deals on neither count only in the settlement totals.

Written to keep peak memory around 1 GB: no merges of the full deals table, small dtypes, grouped work vectorised.

Usage: python -I scripts/build_site_data.py
"""
import json
import shutil
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import shapely

from street_index import clean_street, group_streets, street_file_id

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data"
R = 6378137.0
CELL = 0.02                      # degrees; the page loads the cells in view from zoom 14
APT_TYPES = ["דירה בבית קומות", "דירת גן", "דירת גג", "ד. מגורים"]     # see notebook section 3.2
COLOR_FROM = 2020                # building and settlement colour: median price per m² of flats sold since this year
# colour classes for price per m² (shekels). Fixed rather than quantiles: national quantiles put almost all of central
# Tel Aviv in the top class; these steps keep every class populated and still separate areas within Tel Aviv
THRESHOLDS = [12_000, 18_000, 25_000, 35_000, 50_000, 70_000]
GRID_M = [1000, 500, 250]        # square sizes for the zoomed-out map, coarse to fine; the page picks one by zoom
GRID_MIN_FLATS = 3               # a square needs this many flats sold since COLOR_FROM to be drawn
RECENT_FROM = 2024               # "recent" figures in the stats tab (same as the notebook's RECENT)
# log-spaced histogram bins, so medians of any group of settlements can be combined (zoomed-out stats)
PPSQM_EDGES = np.geomspace(1_000, 150_000, 61)
PRICE_EDGES = np.geomspace(50_000, 50_000_000, 81)
T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:5.0f}s] {msg}", flush=True)


def to_lonlat(x, y):
    return np.degrees(x / R), np.degrees(2 * np.arctan(np.exp(y / R)) - np.pi / 2)


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def pkey(gush, chelka):
    return gush.astype("int64") * 100_000 + chelka.astype("int64")


def load_deals():
    d = pd.read_parquet(ROOT / "data/deals.parquet", columns=[
        "settlement", "gush", "chelka", "deal_date", "deal_amount", "deal_nature", "portion",
        "year_built", "asset_area", "room_num"])
    sett = d["settlement"].astype("category")
    norm = sett.cat.categories.str.replace(r"\s*-\s*", "-", regex=True).str.strip()
    names = pd.Index(norm.unique())
    codes = names.get_indexer(norm)[sett.cat.codes.values]
    d["settlement"] = pd.Categorical.from_codes(np.where(sett.cat.codes.values < 0, -1, codes), names)
    d["deal_nature"] = d["deal_nature"].fillna("לא ידוע").astype("category")
    d["deal_date"] = pd.to_datetime(d["deal_date"], format="%d/%m/%Y", errors="coerce")
    d = d[d["deal_date"].notna()].reset_index(drop=True)
    d["year"] = d["deal_date"].dt.year.astype("int16")
    d["gush"] = d["gush"].astype("int32")
    d["chelka"] = d["chelka"].astype("int32")
    for c in ["asset_area", "room_num", "portion"]:
        d[c] = d[c].astype("float32").mask(d[c] <= 0)
    d["year_built"] = d["year_built"].mask(d["year_built"] <= 0)
    unit = d["deal_amount"] / d["portion"]                          # price of the whole unit (see notebook, section 3.5)
    d["ppsqm"] = (unit / d["asset_area"]).astype("float32")
    # Same sanity tests as the notebook's cleaning, minus the local-outlier test. Flagged deals are shown, not hidden.
    d["suspect"] = (d["portion"].isna() | d["asset_area"].isna() | ~d["asset_area"].between(15, 1000)
                    | ~d["ppsqm"].between(1_000, 150_000))
    d["flat_ok"] = ~d["suspect"] & d["deal_nature"].isin(APT_TYPES)
    d["unit_k"] = (unit / 1000).round().where(d["flat_ok"]).astype("float32")   # only needed for plausible flats
    return d


def locate(deals):
    """One map location per parcel that has deals: its own point, else its block's point."""
    pb = pd.read_parquet(ROOT / "data/parcel_building.parquet")
    pb["key"] = pkey(pb["gush"], pb["chelka"])
    blocks = pd.read_sql("SELECT gush, x, y FROM blocks WHERE status = 'ok'", sqlite3.connect(ROOT / "data/blocks.sqlite"))

    parcels = deals[["gush", "chelka"]].drop_duplicates().reset_index(drop=True)
    parcels["key"] = pkey(parcels["gush"], parcels["chelka"])
    parcels = parcels.merge(pb.drop(columns=["gush", "chelka"]), on="key", how="left")
    parcels["loc_kind"] = np.where(parcels["x"].notna(), "parcel", "")
    nb = parcels["x"].isna()
    bxy = blocks.set_index("gush")
    parcels.loc[nb, "x"] = parcels.loc[nb, "gush"].map(bxy["x"])
    parcels.loc[nb, "y"] = parcels.loc[nb, "gush"].map(bxy["y"])
    parcels.loc[nb & parcels["x"].notna(), "loc_kind"] = "block"
    parcels = parcels[parcels["loc_kind"] != ""].copy()

    # parcel points are their own location; all re-parcelled parcels of a block share the block point
    parcels["loc_key"] = np.where(parcels["loc_kind"] == "parcel", parcels["key"], -parcels["gush"].astype("int64"))
    parcels["loc_id"] = pd.factorize(parcels["loc_key"])[0].astype("int32")
    parcels["lon"], parcels["lat"] = to_lonlat(parcels["x"].values, parcels["y"].values)
    parcels["cell"] = (np.floor(parcels["lon"] / CELL).astype(int).astype(str) + "_"
                       + np.floor(parcels["lat"] / CELL).astype(int).astype(str))
    return parcels


def addresses():
    con = sqlite3.connect(f"file:{ROOT / 'data/parcel_addresses.sqlite'}?mode=ro", uri=True)
    a = pd.read_sql("SELECT gush, chelka, street, house_num FROM addresses WHERE street IS NOT NULL", con)
    a["a"] = (a["street"] + " " + a["house_num"].astype("Int64").astype(str).replace("<NA>", "")).str.strip()
    a["key"] = pkey(a["gush"], a["chelka"])
    return a.drop_duplicates(["key", "a"]).sort_values(["key", "a"]).groupby("key")["a"].agg(lambda s: list(s)[:6])


def split_rows(df, by, cols):
    """{group value: [[col values], ...]} for a frame sorted by `by`, without a Python-level groupby."""
    df = df.sort_values(by)
    keys = df[by].values
    rows = df[cols].values.tolist()
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
    ends = np.r_[starts[1:], len(keys)]
    return {keys[s]: rows[s:e] for s, e in zip(starts, ends)}


def write_street_index(deals, parcels):
    """One small JSON per settlement: median parcel point of each street.

    Keyed by the deal's settlement name (the same strings as settlements.json),
    not the GovMap city, so search resolution and the index agree. Freed before
    the building geometries are built. Skipped, rather than failing the map, if
    the address table is missing.
    """
    db = ROOT / "data/parcel_addresses.sqlite"
    if not db.exists():
        log("street index skipped (no parcel_addresses.sqlite)")
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        raw = pd.read_sql("SELECT gush, chelka, street FROM addresses WHERE street IS NOT NULL AND street != ''", con)
    except sqlite3.Error as err:
        log(f"street index skipped: {err}")
        return None
    if raw.empty:
        return None
    raw["key"] = pkey(raw["gush"], raw["chelka"])
    raw["street"] = raw["street"].map(clean_street)
    raw = raw[raw["street"] != ""]
    loc = parcels.drop_duplicates("key").set_index("key")[["lon", "lat"]]
    raw = raw.join(loc, on="key").dropna(subset=["lon"])
    keys = pd.Index(raw["key"].unique())
    sub = deals.loc[deals["key"].isin(keys), ["key", "settlement"]].dropna()
    if sub.empty:
        return None
    mode = (sub.groupby(["key", "settlement"], observed=True).size().reset_index(name="n")
            .sort_values(["key", "n"], ascending=[True, False])
            .drop_duplicates("key"))
    raw["settlement"] = raw["key"].map(mode.set_index("key")["settlement"])
    raw = raw.dropna(subset=["settlement"])
    grouped = group_streets(zip(
        raw["settlement"].astype(str), raw["street"].astype(str),
        raw["lon"].tolist(), raw["lat"].tolist()))
    del raw, sub, mode
    if not grouped:
        return None
    (OUT / "streets").mkdir(parents=True, exist_ok=True)
    index = {}
    for name, rows in grouped.items():
        rel = f"streets/{street_file_id(name)}.json"
        dump(OUT / rel, rows)
        index[name] = rel
    dump(OUT / "street_index.json", index)
    log(f"{len(index):,} settlements with streets, {sum(len(v) for v in grouped.values()):,} streets")
    return "street_index.json"


def weighted_ppsqm(g):
    """Deal-weighted mean of parcel medians, per group (building or point)."""
    w = g["n_color"].fillna(0).where(g["ppsqm"].notna(), 0)
    num = (g["ppsqm"].fillna(0) * w).groupby(g["grp"]).sum()
    den = w.groupby(g["grp"]).sum()
    return (num / den.replace(0, np.nan)).round()


def main():
    deals = load_deals()
    types = list(deals["deal_nature"].cat.categories)
    deals["type_idx"] = deals["deal_nature"].cat.codes.astype("int16")
    log(f"{len(deals):,} deals, {len(types)} deal types")

    parcels = locate(deals)
    deals["key"] = pkey(deals["gush"], deals["chelka"])
    deals["loc_id"] = deals["key"].map(parcels.set_index("key")["loc_id"]).astype("Int32")
    log("deal location: " + str((parcels.set_index("key")["loc_kind"].reindex(deals["key"]).fillna("none")
                                 .value_counts(normalize=True) * 100).round(1).to_dict()))

    parcels["addresses"] = parcels["key"].map(addresses())
    parcels["n_deals"] = parcels["key"].map(deals["key"].value_counts())
    color = deals.loc[deals["flat_ok"] & (deals["year"] >= COLOR_FROM)].groupby("key")["ppsqm"]
    parcels["ppsqm"] = parcels["key"].map(color.median())
    parcels["n_color"] = parcels["key"].map(color.size())
    parcels["pid"] = parcels["gush"].astype(str) + "/" + parcels["chelka"].astype(str)
    log(f"{len(parcels):,} located parcels")

    if OUT.exists():
        shutil.rmtree(OUT)
    for sub in ["cells", "stats", "deals"]:
        (OUT / sub).mkdir(parents=True)
    street_index = write_street_index(deals, parcels)
    cells = defaultdict(lambda: {"b": [], "p": [], "s": []})

    def addr_lists(g):
        return g.groupby("grp")["addresses"].agg(lambda s: sorted({a for lst in s.dropna() for a in lst})[:8])

    # ---- buildings that received at least one parcel
    m = parcels[(parcels["loc_kind"] == "parcel") & parcels["building_id"].notna()].copy()
    m["grp"] = m["building_id"].astype("int64")
    props = pd.DataFrame({
        "parcels": m.groupby("grp")["pid"].agg(list),
        "n_deals": m.groupby("grp")["n_deals"].sum(),
        "ppsqm": weighted_ppsqm(m),
        "exact": m.assign(i=m["how"] == "inside").groupby("grp")["i"].all(),
        "addresses": addr_lists(m)})
    b = pd.read_parquet(ROOT / "data/buildings.parquet").set_index("building_id").loc[props.index]
    geoms = shapely.from_wkb(b["geometry"].values)
    xy, idx = shapely.get_coordinates(shapely.get_exterior_ring(geoms), return_index=True)
    lon, lat = to_lonlat(xy[:, 0], xy[:, 1])
    coords = np.round(np.c_[lon, lat], 6).tolist()
    starts = np.searchsorted(idx, np.arange(len(geoms) + 1))
    cxy = shapely.get_coordinates(shapely.centroid(geoms))
    clon, clat = to_lonlat(cxy[:, 0], cxy[:, 1])
    for i, (bid, r) in enumerate(props.iterrows()):
        cells[f"{int(np.floor(clon[i] / CELL))}_{int(np.floor(clat[i] / CELL))}"]["b"].append({
            "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coords[starts[i]:starts[i + 1]]]},
            "properties": {"id": int(bid), "parcels": r["parcels"], "n_deals": int(r["n_deals"]),
                           "ppsqm": None if pd.isna(r["ppsqm"]) else int(r["ppsqm"]), "exact": bool(r["exact"]),
                           "addresses": r["addresses"]}})
    log(f"{len(props):,} buildings")
    del m, props, b, geoms, xy, idx, coords

    # ---- parcel points with no building, and block points for re-parcelled parcels
    pts = parcels[(parcels["loc_kind"] == "block") | parcels["building_id"].isna()].copy()
    pts["grp"] = pts["loc_id"]
    first = pts.groupby("grp")[["lon", "lat", "cell", "loc_kind"]].first()
    pprops = first.join(pd.DataFrame({"parcels": pts.groupby("grp")["pid"].agg(list),
                                      "n_deals": pts.groupby("grp")["n_deals"].sum(),
                                      "ppsqm": weighted_ppsqm(pts), "addresses": addr_lists(pts)}))
    for r in pprops.itertuples():
        cells[r.cell]["p"].append({
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [round(r.lon, 6), round(r.lat, 6)]},
            "properties": {"parcels": r.parcels, "block": r.loc_kind == "block", "n_deals": int(r.n_deals),
                           "ppsqm": None if pd.isna(r.ppsqm) else int(r.ppsqm), "addresses": r.addresses}})
    log(f"{len(pprops):,} points ({(pprops['loc_kind'] == 'block').sum():,} block points)")
    del pts, pprops

    # ---- per-location deal summaries for the stats tab: counts by (year, type) and the plausible flats
    located = deals.loc[deals["loc_id"].notna(), ["loc_id", "year", "type_idx", "flat_ok", "unit_k", "asset_area", "room_num"]]
    counts = located.groupby(["loc_id", "year", "type_idx"]).size().rename("n").reset_index()
    by_loc_counts = split_rows(counts, "loc_id", ["year", "type_idx", "n"])
    fl = located[located["flat_ok"]].assign(a=lambda f: f["asset_area"].round().astype(int), k=lambda f: f["unit_k"].astype(int))
    fl["r"] = fl["room_num"].round(1)
    by_loc_flats = split_rows(fl, "loc_id", ["year", "k", "a", "r"])
    loc = parcels.drop_duplicates("loc_id").set_index("loc_id")
    for loc_id, rows in by_loc_counts.items():
        r = loc.loc[loc_id]
        flats = []
        for y, k, area, rooms in by_loc_flats.get(loc_id, []):
            if isinstance(rooms, float) and np.isnan(rooms):
                rooms = None
            elif rooms is not None:
                rooms = float(rooms)
            flats.append([int(y), int(k), int(area), rooms])
        cells[r["cell"]]["s"].append([round(r["lon"], 5), round(r["lat"], 5), rows, flats])
    for key, c in cells.items():
        if c["b"] or c["p"]:
            dump(OUT / "cells" / f"{key}.json", {"b": {"type": "FeatureCollection", "features": c["b"]},
                                                "p": {"type": "FeatureCollection", "features": c["p"]}})
        if c["s"]:
            dump(OUT / "stats" / f"{key}.json", c["s"])
    log(f"{len(cells):,} cells written")
    del located, counts, by_loc_counts, fl, by_loc_flats

    # ---- settlements (zoomed-out view and stats); position = median of their located deals
    pos = pd.DataFrame({"settlement": deals["settlement"], "lon": deals["loc_id"].map(loc["lon"]),
                        "lat": deals["loc_id"].map(loc["lat"])}).dropna()
    sett_pos = pos.groupby("settlement", observed=True)[["lon", "lat"]].median()
    del pos
    f = deals.loc[deals["flat_ok"], ["settlement", "year", "ppsqm", "unit_k"]]
    f = f.assign(pb=np.clip(np.searchsorted(PPSQM_EDGES, f["ppsqm"]) - 1, 0, len(PPSQM_EDGES) - 2),
                 ub=np.clip(np.searchsorted(PRICE_EDGES, f["unit_k"] * 1000) - 1, 0, len(PRICE_EDGES) - 2))
    color_s = f[f["year"] >= COLOR_FROM].groupby("settlement", observed=True)["ppsqm"].agg(["median", "size"])
    hists = {}
    for col, name in [("pb", "ph"), ("ub", "uh")]:
        h = f.groupby(["settlement", "year", col], observed=True).size()
        out = defaultdict(lambda: defaultdict(list))
        for (s, y, bin_), n in h.items():
            out[s][int(y)].append([int(bin_), int(n)])
        hists[name] = out
    sc = deals.groupby(["settlement", "year", "type_idx"], observed=True).size()
    sett_counts = defaultdict(list)
    for (s, y, t), n in sc.items():
        sett_counts[s].append([int(y), int(t), int(n)])
    settlements = [{
        "name": s, "lon": round(sett_pos.at[s, "lon"], 5), "lat": round(sett_pos.at[s, "lat"], 5),
        "n": sum(c[2] for c in sett_counts[s]),
        "ppsqm": round(color_s.at[s, "median"]) if s in color_s.index and color_s.at[s, "size"] >= 5 else None,
        "counts": sett_counts[s], "ph": hists["ph"].get(s, {}), "uh": hists["uh"].get(s, {})}
        for s in sett_pos.index]
    dump(OUT / "settlements.json", settlements)
    log(f"{len(settlements):,} settlements")
    del f

    # ---- price grid for the zoomed-out map: median price per m² of flats sold since COLOR_FROM, per square
    f = deals.loc[deals["flat_ok"] & (deals["year"] >= COLOR_FROM), ["loc_id", "ppsqm"]].dropna(subset=["loc_id"])
    f = f.assign(lon=f["loc_id"].map(loc["lon"]), lat=f["loc_id"].map(loc["lat"]))
    feats = []
    for m in GRID_M:
        dy = m / 111_320
        dx = dy / np.cos(np.radians(31.8))
        g = (f.assign(gx=np.floor(f["lon"] / dx).astype(int), gy=np.floor(f["lat"] / dy).astype(int))
             .groupby(["gx", "gy"])["ppsqm"].agg(["size", "median"]))
        g = g[g["size"] >= GRID_MIN_FLATS]
        for (gx, gy), r in g.iterrows():
            x0, y0 = round(gx * dx, 5), round(gy * dy, 5)
            x1, y1 = round((gx + 1) * dx, 5), round((gy + 1) * dy, 5)
            feats.append({"type": "Feature",
                          "geometry": {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]},
                          "properties": {"m": m, "ppsqm": int(round(r["median"])), "n": int(r["size"])}})
    dump(OUT / "grid.geojson", {"type": "FeatureCollection", "features": feats})
    log(f"{len(feats):,} grid squares")
    del f

    # ---- deals per block, loaded on click
    d = deals[["gush", "chelka", "deal_date", "deal_nature", "deal_amount", "portion", "asset_area", "room_num",
               "year_built", "suspect"]].sort_values(["gush", "deal_date"], ascending=[True, False])
    for gush, g in d.groupby("gush", sort=False):
        rows = pd.DataFrame({"c": g["chelka"], "d": g["deal_date"].dt.strftime("%Y-%m-%d"), "t": g["deal_nature"].astype(str),
                             "paid": g["deal_amount"].round(), "p": g["portion"].round(4), "a": g["asset_area"].round(),
                             "r": g["room_num"], "y": g["year_built"], "s": g["suspect"]})
        (OUT / "deals" / f"{gush}.json").write_text(rows.to_json(orient="records", force_ascii=False), encoding="utf-8")
    log(f"{d['gush'].nunique():,} block files")

    dump(OUT / "meta.json", {"types": types, "apt_types": APT_TYPES, "cell": CELL, "color_from": COLOR_FROM,
                             "recent_from": RECENT_FROM, "thresholds": THRESHOLDS, "grid_m": GRID_M, "grid_min_flats": GRID_MIN_FLATS, "cells": sorted(k for k, c in cells.items() if c["b"] or c["p"]),
                             "stat_cells": sorted(k for k, c in cells.items() if c["s"]),
                             "ppsqm_edges": np.round(PPSQM_EDGES).tolist(), "price_edges": np.round(PRICE_EDGES).tolist(),
                             "street_index": street_index,
                             "flat_tuple": ["year", "price_k", "area_m2", "rooms"]})
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()) / 1e6
    log(f"done: {size:.0f} MB in site/data")


if __name__ == "__main__":
    main()

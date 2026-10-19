"""Prototype: build the static data files for the map site (one city).

Reads  data/<city>_parcel_building.parquet (match_buildings.py), data/parcel_addresses.sqlite, data/deals.parquet,
       data/osm/<city>_buildings.json
Writes site/data/buildings.geojson   building polygons that received deals, with summary numbers for colouring
       site/data/parcels.geojson     scraped parcel points with no building match (so their deals are still reachable)
       site/data/deals/<gush>.json   all deals of one block, loaded on click

Usage: python scripts/build_site_data.py [--city tlv]      (not -I: it imports match_buildings from this folder)
"""
import argparse
import json
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from match_buildings import CITIES, R, load_buildings

ROOT = Path(__file__).resolve().parent.parent
APT_TYPES = ["דירה בבית קומות", "דירת גן", "דירת גג", "ד. מגורים"]     # see notebook section 3.2
OUT = ROOT / "site" / "data"


def to_lonlat(x, y):
    return np.degrees(x / R), np.degrees(2 * np.arctan(np.exp(y / R)) - np.pi / 2)


def ring_coords(ring):
    xs, ys = ring.xy
    lon, lat = to_lonlat(np.array(xs), np.array(ys))
    return [[round(a, 6), round(b, 6)] for a, b in zip(lon, lat)]


def load_deals(city):
    d = pd.read_parquet(ROOT / "data/deals.parquet")
    d["settlement"] = d["settlement"].str.replace(r"\s*-\s*", "-", regex=True).str.strip()
    d = d[d["settlement"] == CITIES[city]].copy()
    d["deal_date"] = pd.to_datetime(d["deal_date"], format="%d/%m/%Y")
    for c in ["asset_area", "room_num", "year_built", "portion"]:
        d[c] = d[c].mask(d[c] <= 0)
    d["unit_price"] = d["deal_amount"] / d["portion"]               # price of the whole unit (see notebook, section 3.5)
    d["ppsqm"] = d["unit_price"] / d["asset_area"]
    # Same sanity tests as the notebook's cleaning, minus the local-outlier test. Flagged deals are shown, not hidden.
    d["suspect"] = (d["portion"].isna() | d["asset_area"].isna() | ~d["asset_area"].between(15, 1000)
                    | ~d["ppsqm"].between(1_000, 150_000))
    return d


def main(city):
    parcels = pd.read_parquet(ROOT / f"data/{city}_parcel_building.parquet")
    deals = load_deals(city)
    con = sqlite3.connect(ROOT / "data/parcel_addresses.sqlite")
    addr = pd.read_sql("SELECT gush, chelka, street, house_num FROM addresses WHERE street IS NOT NULL", con)
    addr["a"] = addr["street"] + " " + addr["house_num"].astype("Int64").astype(str).replace("<NA>", "")
    addr = addr.groupby(["gush", "chelka"])["a"].apply(lambda s: sorted(set(s.str.strip()))[:6]).rename("addresses")
    parcels = parcels.join(addr, on=["gush", "chelka"])

    stats = deals[~deals["suspect"] & deals["deal_nature"].isin(APT_TYPES)].groupby(["gush", "chelka"])["ppsqm"].median()
    n_deals = deals.groupby(["gush", "chelka"]).size().rename("n_deals")
    parcels = parcels.join(n_deals, on=["gush", "chelka"]).join(stats, on=["gush", "chelka"])

    # ---- deals per block
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "deals").mkdir(parents=True)
    keep = deals.merge(parcels[["gush", "chelka"]], on=["gush", "chelka"])
    for gush, g in keep.groupby("gush"):
        g = g.sort_values("deal_date", ascending=False)
        rows = [{"c": int(r.chelka), "d": r.deal_date.strftime("%Y-%m-%d"), "t": r.deal_nature,
                 "paid": round(r.deal_amount), "p": None if pd.isna(r.portion) else round(float(r.portion), 4),
                 "a": None if pd.isna(r.asset_area) else round(float(r.asset_area)),
                 "r": None if pd.isna(r.room_num) else float(r.room_num),
                 "y": None if pd.isna(r.year_built) else int(r.year_built), "s": bool(r.suspect)}
                for r in g.itertuples()]
        (OUT / "deals" / f"{int(gush)}.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- buildings that received at least one parcel
    buildings = load_buildings(ROOT / f"data/osm/{city}_buildings.json").set_index("building_id")
    m = parcels[parcels["building_id"].notna()].copy()
    m["building_id"] = m["building_id"].astype("int64")
    feats = []
    for bid, g in m.groupby("building_id"):
        poly = buildings.loc[bid, "geometry"]
        coords = [ring_coords(poly.exterior)] + [ring_coords(i) for i in poly.interiors]
        w = g["n_deals"].fillna(0)
        props = {"id": int(bid),
                 "parcels": [f"{int(a)}/{int(b)}" for a, b in zip(g["gush"], g["chelka"])],
                 "n_deals": int(w.sum()),
                 "ppsqm": None if g["ppsqm"].isna().all() else round(float(np.average(g["ppsqm"].dropna(), weights=w[g["ppsqm"].notna()].clip(lower=1)))),
                 "exact": bool((g["how"] == "inside").all()),
                 "addresses": sorted({a for lst in g["addresses"].dropna() for a in lst})[:8]}
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": coords}, "properties": props})
    (OUT / "buildings.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- parcels with no building: a point each
    u = parcels[parcels["building_id"].isna()]
    lon, lat = to_lonlat(u["x"].values, u["y"].values)
    pf = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(a, 6), round(b, 6)]},
           "properties": {"parcels": [f"{int(g)}/{int(c)}"], "n_deals": int(n) if n == n else 0,
                          "ppsqm": None if p != p else round(float(p)),
                          "addresses": ad if isinstance(ad, list) else []}}
          for a, b, g, c, n, p, ad in zip(lon, lat, u["gush"], u["chelka"], u["n_deals"], u["ppsqm"], u["addresses"])]
    (OUT / "parcels.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": pf}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1e6
    print(f"{len(feats):,} buildings, {len(pf):,} unmatched parcel points, {keep['gush'].nunique()} block files, {len(keep):,} deals, {size:.1f} MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="tlv", choices=CITIES)
    main(ap.parse_args().city)

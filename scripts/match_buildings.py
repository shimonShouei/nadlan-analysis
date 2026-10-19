"""Prototype: match scraped parcel points to OpenStreetMap building outlines for one city.

Inputs
  data/parcel_addresses.sqlite   parcel label points (EPSG:3857), from fetch_parcel_addresses.py
  data/deals.parquet             deals (gush, chelka, settlement)
  data/osm/<city>_buildings.json Overpass `way["building"]; out geom;` result
Output
  data/<city>_parcel_building.parquet  one row per parcel: the building its point falls in (or the nearest one)

Usage: python -I scripts/match_buildings.py [--city tlv]
"""
import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parent.parent
R = 6378137.0
CITIES = {"tlv": "תל אביב-יפו"}
NEAREST_M = 15          # a parcel point not inside any building is matched to one this close (metres)
NEAR_RADIUS_M = 30      # buildings within this distance count as "other buildings that could be on the parcel"
MERC = 1 / np.cos(np.radians(32.07))    # metres -> EPSG:3857 units at Tel Aviv's latitude


def to_mercator(lon, lat):
    return np.radians(lon) * R, np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * R


def load_buildings(path):
    elements = json.loads(Path(path).read_text(encoding="utf-8"))["elements"]
    rows = []
    for el in elements:
        geom = el.get("geometry") or []
        if len(geom) < 4:
            continue
        x, y = to_mercator(np.array([g["lon"] for g in geom]), np.array([g["lat"] for g in geom]))
        poly = Polygon(zip(x, y))
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        tags = el.get("tags", {})
        rows.append({"building_id": el["id"], "geometry": poly, "btype": tags.get("building"),
                     "street": tags.get("addr:street"), "housenumber": tags.get("addr:housenumber"),
                     "levels": tags.get("building:levels")})
    return pd.DataFrame(rows)


def main(city):
    buildings = load_buildings(ROOT / f"data/osm/{city}_buildings.json")
    print(f"{len(buildings):,} buildings, {buildings['street'].notna().mean():.0%} with an OSM street address")
    tree = STRtree(buildings["geometry"].values)

    con = sqlite3.connect(ROOT / "data/parcel_addresses.sqlite")
    parcels = pd.read_sql("SELECT gush, chelka, status, x, y FROM parcels WHERE objectid IS NOT NULL", con)
    deals = pd.read_parquet(ROOT / "data/deals.parquet", columns=["gush", "chelka", "settlement"])
    deals["settlement"] = deals["settlement"].str.replace(r"\s*-\s*", "-", regex=True).str.strip()
    city_parcels = deals.loc[deals["settlement"] == CITIES[city], ["gush", "chelka"]].drop_duplicates()
    parcels = parcels.merge(city_parcels, on=["gush", "chelka"])
    n_deals = deals[deals["settlement"] == CITIES[city]].merge(parcels[["gush", "chelka"]]).shape[0]
    print(f"{len(parcels):,} scraped parcels with deals in {city} ({n_deals:,} deals)")

    pts = shapely.points(parcels["x"].values, parcels["y"].values)

    # 1. point inside a building
    p_idx, b_idx = tree.query(pts, predicate="within")
    inside = pd.DataFrame({"p": p_idx, "b": b_idx}).drop_duplicates("p")        # overlapping footprints: keep one
    parcels["building_idx"] = np.nan
    parcels["how"] = "none"
    parcels.loc[inside["p"].values, "building_idx"] = inside["b"].values
    parcels.loc[inside["p"].values, "how"] = "inside"
    parcels["dist_m"] = 0.0

    # 2. otherwise the nearest building within NEAREST_M
    left = np.flatnonzero(parcels["how"].values == "none")
    (p2, b2), d2 = tree.query_nearest(pts[left], max_distance=NEAREST_M * MERC, return_distance=True, all_matches=False)
    parcels.loc[left[p2], "building_idx"] = b2
    parcels.loc[left[p2], "how"] = "nearest"
    parcels.loc[left[p2], "dist_m"] = d2 / MERC

    # 3. how many buildings are close enough to be on the same parcel?
    pn, _ = tree.query(pts, predicate="dwithin", distance=NEAR_RADIUS_M * MERC)
    parcels["n_buildings_near"] = np.bincount(pn, minlength=len(parcels))

    matched = parcels["building_idx"].notna()
    parcels["building_id"] = np.where(matched, buildings["building_id"].reindex(parcels["building_idx"].fillna(0).astype(int)).values, 0)
    parcels.loc[~matched, "building_id"] = np.nan

    print("\nHow parcel points match buildings:")
    print((parcels["how"].value_counts(normalize=True) * 100).round(1).to_string())
    per_building = parcels[matched].groupby("building_id").size()
    print(f"\nParcels per matched building: {per_building.describe()[['mean', '50%', 'max']].round(1).to_dict()}; "
          f"{(per_building > 1).mean():.0%} of matched buildings receive deals from 2+ parcels")
    print("Other buildings within %d m of a parcel point (ambiguity proxy):" % NEAR_RADIUS_M)
    print(parcels["n_buildings_near"].describe(percentiles=[.25, .5, .75, .9]).round(1).to_string())
    print(f"\nDeal-weighted share matched: "
          f"{deals.merge(parcels.loc[matched, ['gush', 'chelka']]).shape[0] / n_deals:.1%}")

    out = parcels.drop(columns=["building_idx"])
    out.to_parquet(ROOT / f"data/{city}_parcel_building.parquet", index=False)
    buildings.drop(columns="geometry").to_parquet(ROOT / f"data/{city}_buildings_meta.parquet", index=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="tlv", choices=CITIES)
    main(ap.parse_args().city)

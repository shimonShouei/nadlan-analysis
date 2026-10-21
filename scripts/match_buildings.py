"""Match scraped parcel points to OpenStreetMap building outlines, for all of Israel.

Inputs
  data/parcel_addresses.sqlite                     parcel label points (EPSG:3857), from fetch_parcel_addresses.py
  data/osm/israel-and-palestine-latest.osm.pbf     https://download.geofabrik.de/asia/israel-and-palestine-latest.osm.pbf
Outputs
  data/parcel_building.parquet   one row per parcel: the building its point falls in (or the nearest one)
  data/buildings.parquet         the buildings that received a parcel, outline as WKB (EPSG:3857)

Only buildings near some parcel point are kept from the .pbf, so memory stays small.

Usage: python -I scripts/match_buildings.py
"""
import sqlite3
from pathlib import Path

import numpy as np
import osmium
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parent.parent
PBF = ROOT / "data/osm/israel-and-palestine-latest.osm.pbf"
R = 6378137.0
NEAREST_M = 15          # a parcel point not inside any building is matched to one this close (metres)
NEAR_RADIUS_M = 30      # buildings within this distance count as "other buildings that could be on the parcel"
MERC = 1 / np.cos(np.radians(31.8))     # metres -> EPSG:3857 units at Israel's latitude
CELL = 50 * MERC                        # pre-filter grid (EPSG:3857 units)


def to_mercator(lon, lat):
    return np.radians(lon) * R, np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * R


def load_parcels():
    con = sqlite3.connect(f"file:{ROOT / 'data/parcel_addresses.sqlite'}?mode=ro", uri=True)
    return pd.read_sql("SELECT gush, chelka, x, y FROM parcels WHERE objectid IS NOT NULL", con)


def load_buildings(parcels):
    """Building outlines from the .pbf whose bounding box comes within ~50 m of a parcel point."""
    cx, cy = np.floor(parcels["x"] / CELL).astype(int), np.floor(parcels["y"] / CELL).astype(int)
    wanted = {(a + i, b + j) for a, b in zip(cx, cy) for i in (-1, 0, 1) for j in (-1, 0, 1)}

    rows = []
    fp = osmium.FileProcessor(str(PBF)).with_areas().with_filter(osmium.filter.KeyFilter("building"))
    for obj in fp:
        if not obj.is_area():
            continue
        for ring in obj.outer_rings():          # multipolygon buildings: one row per outer ring
            lon = np.fromiter((n.lon for n in ring), float)
            lat = np.fromiter((n.lat for n in ring), float)
            if len(lon) < 4:
                continue
            x, y = to_mercator(lon, lat)
            x0, x1 = int(x.min() // CELL), int(x.max() // CELL)
            y0, y1 = int(y.min() // CELL), int(y.max() // CELL)
            if (x1 - x0 + 1) * (y1 - y0 + 1) > 400 or not any(
                    (i, j) in wanted for i in range(x0, x1 + 1) for j in range(y0, y1 + 1)):
                continue                        # >400 cells: 1 km+ footprints (bases, industrial zones)
            poly = Polygon(zip(x, y))
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
            rows.append({"building_id": obj.orig_id(), "geometry": poly, "btype": obj.tags.get("building")})
    return pd.DataFrame(rows).drop_duplicates("building_id").reset_index(drop=True)


def main():
    parcels = load_parcels()
    print(f"{len(parcels):,} located parcels")
    buildings = load_buildings(parcels)
    print(f"{len(buildings):,} OSM buildings near a parcel point")
    tree = STRtree(buildings["geometry"].values)
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
    parcels["building_id"] = pd.array([pd.NA] * len(parcels), dtype="Int64")
    parcels.loc[matched, "building_id"] = buildings["building_id"].values[parcels.loc[matched, "building_idx"].astype(int)]

    print("\nHow parcel points match buildings:")
    print((parcels["how"].value_counts(normalize=True) * 100).round(1).to_string())
    per_building = parcels[matched].groupby("building_id").size()
    print(f"\nParcels per matched building: {per_building.describe()[['mean', '50%', 'max']].round(1).to_dict()}; "
          f"{(per_building > 1).mean():.0%} of matched buildings receive deals from 2+ parcels")

    parcels.drop(columns=["building_idx"]).to_parquet(ROOT / "data/parcel_building.parquet", index=False)
    used = buildings[buildings["building_id"].isin(per_building.index)].copy()
    used["geometry"] = shapely.to_wkb(used["geometry"].values)
    used.to_parquet(ROOT / "data/buildings.parquet", index=False)
    print(f"{len(used):,} buildings with parcels -> data/buildings.parquet")


if __name__ == "__main__":
    main()

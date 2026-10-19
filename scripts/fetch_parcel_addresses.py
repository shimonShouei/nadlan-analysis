"""Map every (gush, chelka) in the deals data to its official address(es) via GovMap.

Per parcel, two GovMap calls:
  1. search-service/autocomplete "גוש G חלקה C"  -> parcel objectid + label point (EPSG:3857)
  2. layers-catalog/apps/address-search/parcel/{objectid} -> address(es) on that parcel

Progress is stored in data/parcel_addresses.sqlite, so the script can be stopped and rerun
at any time; finished parcels are skipped. Parcels are processed in order of deal count.

Usage:
  python scripts/fetch_parcel_addresses.py [--limit N] [--workers 4]
  python scripts/fetch_parcel_addresses.py --export   # write data/parcel_addresses.parquet
"""
import argparse
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DEALS = ROOT / "data/deals.parquet"
DB = ROOT / "data/parcel_addresses.sqlite"
OUT = ROOT / "data/parcel_addresses.parquet"
API = "https://www.govmap.gov.il/api"
BATCH = 2000
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.govmap.gov.il/"}

_local = threading.local()


def session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update(HEADERS)
    return _local.s


def call(method, url, **kw):
    for attempt in range(5):
        try:
            r = session().request(method, url, timeout=30, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(r.status_code)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def lookup(gush, chelka):
    """Return (status, objectid, x, y, addresses)."""
    label = f"גוש {gush} חלקה {chelka}"
    res = call("POST", f"{API}/search-service/autocomplete",
               json={"searchText": label, "language": "he", "isAccurate": False, "maxResults": 5})
    hit = next((r for r in res.get("results", []) if r["type"] == "parcel" and r["text"] == label), None)
    if hit is None:
        return "parcel_not_found", None, None, None, []
    objectid = int(hit["id"].split("|")[2])
    x, y = map(float, hit["shape"][len("POINT("):-1].split())
    feats = call("GET", f"{API}/layers-catalog/apps/address-search/parcel/{objectid}") or []
    addrs = [f["properties"] for f in feats if f.get("properties")]
    return ("ok" if addrs else "no_address"), objectid, x, y, addrs


def init_db(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS parcels (
            gush INTEGER, chelka INTEGER, status TEXT, objectid INTEGER, x REAL, y REAL,
            PRIMARY KEY (gush, chelka));
        CREATE TABLE IF NOT EXISTS addresses (
            gush INTEGER, chelka INTEGER, street TEXT, house_num INTEGER, city TEXT,
            settlement_code INTEGER, street_code INTEGER, address_objectid INTEGER);
    """)


def fetch(limit, workers):
    con = sqlite3.connect(DB)
    init_db(con)
    deals = pd.read_parquet(DEALS, columns=["gush", "chelka"])
    todo = deals.value_counts().reset_index(name="n_deals")[["gush", "chelka"]]
    done = pd.read_sql("SELECT gush, chelka FROM parcels", con)
    todo = todo.merge(done, how="left", indicator=True).query("_merge == 'left_only'")
    todo = list(todo[["gush", "chelka"]].itertuples(index=False, name=None))[:limit]
    print(f"{len(done):,} parcels already done, {len(todo):,} to fetch")

    del deals, done
    t0, n, failed = time.time(), 0, 0
    with ThreadPoolExecutor(workers) as pool:
        # Submit in batches so only BATCH lookups are queued in memory at a time
        for start in range(0, len(todo), BATCH):
            batch = todo[start:start + BATCH]
            futures = {pool.submit(lookup, int(g), int(c)): (int(g), int(c)) for g, c in batch}
            for fut in as_completed(futures):
                g, c = futures[fut]
                try:
                    status, oid, x, y, addrs = fut.result()
                except Exception as e:  # leave unrecorded so a rerun retries it
                    failed += 1
                    print(f"failed {g}/{c}: {e}")
                    continue
                con.execute("INSERT OR REPLACE INTO parcels VALUES (?,?,?,?,?,?)", (g, c, status, oid, x, y))
                con.execute("DELETE FROM addresses WHERE gush=? AND chelka=?", (g, c))
                con.executemany("INSERT INTO addresses VALUES (?,?,?,?,?,?,?,?)", [
                    (g, c, a.get("str_name"), a.get("house_num"), a.get("setl_name"),
                     a.get("settlementCode"), a.get("streetCode"), a.get("address_objectid"))
                    for a in addrs])
                n += 1
                if n % 500 == 0:
                    rate = n / (time.time() - t0)
                    print(f"{n:,}/{len(todo):,}  {rate:.1f}/s  eta {(len(todo) - n) / rate / 3600:.1f}h")
            con.commit()
            del futures, batch
    con.commit()
    print(f"done: {n:,} fetched, {failed:,} failed")
    print(pd.read_sql("SELECT status, COUNT(*) n FROM parcels GROUP BY status", con).to_string(index=False))


def export():
    con = sqlite3.connect(DB)
    df = pd.read_sql("""
        SELECT p.gush, p.chelka, p.status, p.x, p.y,
               a.street, a.house_num, a.city, a.settlement_code, a.street_code
        FROM parcels p LEFT JOIN addresses a USING (gush, chelka)
        ORDER BY p.gush, p.chelka, a.street, a.house_num""", con)
    df.to_parquet(OUT, index=False)
    print(f"{len(df):,} rows ({df[['gush', 'chelka']].drop_duplicates().shape[0]:,} parcels) -> {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--export", action="store_true")
    args = ap.parse_args()
    export() if args.export else fetch(args.limit, args.workers)

"""Compact per-settlement street index for the map search.

GovMap stores the street and the house number separately. `clean_street` is the
form written into the index. `street_name_from_address` is for the concatenated
"street house" strings the site already puts on buildings.

The index is small (tens of thousands of streets for the whole country) and is
split per settlement so the page fetches one file when a search names a place.
"""
import hashlib
from collections import defaultdict


def clean_street(value):
    """Strip a street column. Keep names that are themselves numeric ('רח' 1002')."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def street_name_from_address(address):
    """'הרצל 12א' -> 'הרצל'. One trailing house-number token only.

    A bare street that ends in a number ('רח' 1002') is left unchanged when it
    is the whole string and the first token is not a house number by itself...
    actually one trailing numeric token is stripped when another token remains,
    so 'רח' 1002 15' (street plus house) becomes 'רח' 1002', while 'הרצל 12'
    becomes 'הרצל'. Callers that already have the street column should use
    clean_street instead.
    """
    parts = clean_street(address).split(" ")
    if len(parts) >= 2 and _house_token(parts[-1]):
        parts = parts[:-1]
    return " ".join(parts)


def _house_token(token):
    """True for '12' and '12א' (a Hebrew letter suffix on a house number)."""
    if not token:
        return False
    body = token[:-1] if len(token) >= 2 and "א" <= token[-1] <= "ת" else token
    return body.isdigit()


def street_file_id(name):
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]


def group_streets(records):
    """(settlement, street, lon, lat) -> {settlement: [[street, lon, lat, n], ...]}.

    The point is the per-axis median of the parcel label points, so one outlier
    parcel does not drag the fly-to location. Rows are sorted by parcel count.
    """
    acc = defaultdict(list)
    for settlement, street, lon, lat in records:
        settlement = clean_street(settlement)
        street = clean_street(street)
        if not settlement or not street or lon is None or lat is None:
            continue
        acc[(settlement, street)].append((float(lon), float(lat)))
    out = {}
    for (settlement, street), pts in acc.items():
        lons = sorted(p[0] for p in pts)
        lats = sorted(p[1] for p in pts)
        mid = (len(pts) - 1) // 2
        out.setdefault(settlement, []).append([
            street, round(lons[mid], 5), round(lats[mid], 5), len(pts)])
    for rows in out.values():
        rows.sort(key=lambda r: (-r[3], r[0]))
    return out

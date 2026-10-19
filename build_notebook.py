"""Generates analysis.ipynb. Edit cells here, then: uv run python build_notebook.py"""
import nbformat as nbf

nb = nbf.v4.new_notebook()
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

md("""
# Israel Real Estate Deals: Basic Analysis

Source: Israel Tax Authority real-estate deals (רשות המסים – נדל"ן), via
[over.org.il](https://www.over.org.il/versions/fd06f5ae-8a4f-4120-b275-8a514ad23499).
About 3.8M deal records, 1998 to 2026.

**Contents**
1. Load and cache
2. Data quality: profile of duplicates, missing values, placeholder zeros and impossible values
3. Cleaning: what we fix, drop, or keep, and why (with a row-count log)
4. Deal volume over time and by property type
5. Residential apartments: prices, price per m², cities, rooms, building age
""")

code(r'''
import os
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

try:
    from bidi.algorithm import get_display
except ImportError:                      # fallback: names here are plain Hebrew, so reversing is enough
    get_display = lambda s: s[::-1] if any("֐" <= ch <= "׿" for ch in s) else s

pd.set_option("display.max_columns", 50)
pd.set_option("display.float_format", "{:,.2f}".format)

RAW_CSV = Path(os.environ.get("NADLAN_CSV",
               Path.home() / "Downloads" / "taxes-nadlan-full-f41fb496_append.csv"))
try:
    import pyarrow  # noqa: F401
    CACHE = Path("data") / "deals.parquet"
except ImportError:
    CACHE = Path("data") / "deals.pkl"

# Reference palette (dataviz skill), categorical slots in fixed order
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
plt.rcParams.update({
    "figure.figsize": (11, 4.5), "figure.dpi": 110,
    "font.family": ["Arial", "DejaVu Sans"],   # Arial has Hebrew glyphs on Windows
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": INK2, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.titleweight": "bold", "axes.titlecolor": INK, "axes.titlelocation": "left",
    "lines.linewidth": 2, "axes.prop_cycle": plt.cycler(color=C),
})

# matplotlib 3.11+ reorders right-to-left text itself. Older versions draw it left to right, so we reorder first.
MPL_HANDLES_BIDI = tuple(int(x) for x in matplotlib.__version__.split(".")[:2]) >= (3, 11)

def heb(s):
    """Hebrew labels for matplotlib: reorder only when matplotlib doesn't (reordering twice reverses the text)."""
    return str(s) if MPL_HANDLES_BIDI else get_display(str(s))

shekels = mtick.FuncFormatter(lambda v, _: f"₪{v/1e6:g}M" if v >= 1e6 else f"₪{v/1e3:g}K")
thousands = mtick.FuncFormatter(lambda v, _: f"{v/1e3:,.0f}K")
''')

md("## 1. Load\nThe first run parses the 600 MB CSV and caches it (Parquet if pyarrow is installed, else pickle); later runs load from the cache in seconds.")

code(r'''
if CACHE.exists():
    raw = pd.read_parquet(CACHE) if CACHE.suffix == ".parquet" else pd.read_pickle(CACHE)
else:
    raw = pd.read_csv(
        RAW_CSV, encoding="utf-8-sig",
        dtype={"settlement_code": "Int32", "settlement": "string", "gush": "Int64",
               "chelka": "Int32", "sub_chelka": "string", "deal_date": "string",
               "deal_amount": "float64", "declared_amount": "float64",
               "deal_nature": "string", "portion": "float32", "year_built": "Int16",
               "asset_area": "float32", "room_num": "float32",
               "scraped_at": "string", "first_seen": "string"},
    )
    CACHE.parent.mkdir(exist_ok=True)
    raw.to_parquet(CACHE, index=False) if CACHE.suffix == ".parquet" else raw.to_pickle(CACHE)
print(f"{len(raw):,} rows x {raw.shape[1]} columns")
raw.head()
''')

code("raw.info(memory_usage='deep')")

md("""
### Column reference
| column | meaning |
|---|---|
| settlement_code / settlement | locality code and name |
| gush / chelka / sub_chelka | land registry block / parcel / sub-parcel |
| deal_date | date of the deal (DD/MM/YYYY) |
| deal_amount / declared_amount | assessed vs. declared deal value (₪) |
| deal_nature | property type (Hebrew) |
| portion | share of the property sold (1.0 = whole) |
| year_built, asset_area (m²), room_num | property attributes; **0 means missing** |
| scraped_at / first_seen | scrape metadata, not deal data |
""")

md("## 2. Data quality: profile")

code(r'''
deal_cols = [c for c in raw.columns if c not in ("scraped_at", "first_seen")]
n_dup = raw.duplicated(subset=deal_cols).sum()
print(f"Exact duplicate deals (ignoring scrape metadata): {n_dup:,}")

# Parsing only: no values are altered yet
df = raw.drop_duplicates(subset=deal_cols).drop(columns=["scraped_at", "first_seen"]).copy()
df["deal_date"] = pd.to_datetime(df["deal_date"], format="%d/%m/%Y", errors="coerce")
df["year"] = df["deal_date"].dt.year
df["month"] = df["deal_date"].dt.to_period("M")
df["settlement"] = df["settlement"].str.replace(r"\s*-\s*", "-", regex=True).str.strip()  # "תל אביב -יפו" -> "תל אביב-יפו"

print(f"Unparseable dates: {df['deal_date'].isna().sum():,}")
print(f"Date range: {df['deal_date'].min():%Y-%m-%d} to {df['deal_date'].max():%Y-%m-%d}")
print(f"Localities: {df['settlement'].nunique():,} | property types: {df['deal_nature'].nunique():,}")
print(f"Declared differs from assessed amount: {(df['deal_amount'] != df['declared_amount']).mean():.1%} of deals")
''')

code(r'''
# Three kinds of "bad": real NaN, placeholder 0, and values that are impossible or implausible
num_cols = ["deal_amount", "declared_amount", "portion", "year_built", "asset_area", "room_num"]
profile = pd.DataFrame({"NaN %": df[num_cols].isna().mean() * 100,
                        "zero %": (df[num_cols] == 0).mean() * 100})
profile.loc["settlement", "NaN %"] = df["settlement"].isna().mean() * 100
print(profile.round(2).to_string())

ratio = df["declared_amount"] / df["deal_amount"]
checks = pd.Series({
    "year_built non-zero and < 1900":        ((df["year_built"] > 0) & (df["year_built"] < 1900)).sum(),
    "year_built > deal year + 3":            (df["year_built"] > df["year"] + 3).sum(),
    "room_num > 10":                         (df["room_num"] > 10).sum(),
    "asset_area > 1,000 m²":                 (df["asset_area"] > 1_000).sum(),
    "deal_amount < ₪10K":                    (df["deal_amount"] < 10_000).sum(),
    "deal_amount > ₪100M":                   (df["deal_amount"] > 100_000_000).sum(),
    "declared / assessed outside [0.5, 2]":  ((ratio < 0.5) | (ratio > 2)).sum(),
})
print("\nSuspicious values (rows):")
print(checks.map("{:,}".format).to_string())
''')

code(r'''
df[["deal_amount", "portion", "year_built", "asset_area", "room_num"]].describe(
    percentiles=[.01, .25, .5, .75, .99]).T
''')

md("""
**What the profile tells us**
- Real `NaN`s are negligible. The real missingness is hidden behind **0 placeholders** in `year_built`, `asset_area`,
  `room_num` and `portion`.
- Beyond the zeros there are **impossible values**: build years like 9999 or before 1900, 99 rooms, and areas up to a billion m².
- `deal_amount` has a long tail on both sides: symbolic amounts (family transfers, a few shekels) and multi-billion outliers.
- Land, commercial and agricultural deals legitimately have huge areas and prices, so **range limits must be
  property-type specific**. Field-level fixes below are generic; row-level filters are applied only to apartments.
""")

md("""
## 3. Cleaning

Principles:
1. **Fix at the field level, drop at the row level only when needed.** An impossible `room_num` shouldn't delete a deal
   from the volume statistics. We null the field and keep the row.
2. **Never fabricate the thing we measure.** Price and area are the inputs to price per m², so rows missing them are
   dropped from price analyses instead of imputed. Imputation would invent the signal (or pull it toward the mean).
3. **Log every step** so the effect of each decision is visible.

### 3.1 Placeholders and impossible values become NaN (field level)
""")

code(r'''
fix_log = []

def to_nan(col, bad, why):
    bad = bad.fillna(False).astype(bool)
    fix_log.append({"column": col, "rule": why, "rows set to NaN": int(bad.sum())})
    df[col] = df[col].mask(bad)

to_nan("year_built", df["year_built"] == 0, "0 is a placeholder")
to_nan("year_built", df["year_built"] < 1900, "before 1900 (typos / dummy values)")
to_nan("year_built", df["year_built"] > df["year"] + 3, "more than 3 years after the deal (9999 etc.; a few years early is a real off-plan sale)")
to_nan("asset_area", df["asset_area"] <= 0, "0 is a placeholder")
to_nan("room_num", df["room_num"] <= 0, "0 is a placeholder")
to_nan("room_num", df["room_num"] < 1, "less than 1 room (e.g. 0.5)")
to_nan("room_num", df["room_num"] > 10, "more than 10 rooms (e.g. 99)")
to_nan("portion", df["portion"] <= 0, "0 is a placeholder (share sold unknown)")
pd.DataFrame(fix_log)
''')

md("""
### 3.2 Which property types are apartments?
`deal_nature` has 47 labels, and which of them mean "an apartment" has to be read from the data, not guessed. Each candidate
is profiled below: what years it covers, how often area and rooms are recorded, and its typical size and price per m².
""")

code(r'''
APT_TYPES = ["דירה בבית קומות", "דירת גן", "דירת גג", "ד. מגורים"]
candidates = APT_TYPES + ["מגורים", "דירת נופש", "קוטג' דו משפחתי", "קוטג' חד משפחתי", "בית בודד"]

tmp = df.assign(ppsqm=df["deal_amount"] / df["asset_area"])
type_profile = (tmp[tmp["deal_nature"].isin(candidates)].groupby("deal_nature")
                .agg(deals=("year", "size"), first_year=("year", "min"), last_year=("year", "max"),
                     area_known=("asset_area", lambda s: s.notna().mean() * 100),
                     rooms_known=("room_num", lambda s: s.notna().mean() * 100),
                     median_area=("asset_area", "median"), median_ppsqm=("ppsqm", "median"))
                .loc[candidates])
type_profile["used as apartment"] = type_profile.index.isin(APT_TYPES)
display(type_profile.round(0))

# The two main "flat" labels replace each other over time
labels = ["ד. מגורים", "דירה בבית קומות"]
display(df[df["deal_nature"].isin(labels)].groupby(["year", "deal_nature"]).size().unstack()[labels].loc[1998:2006])
''')

md("""
- **`דירה בבית קומות`, `דירת גן`, `דירת גג`**: flats (about 94% have area and rooms; median about 95 m², 4 rooms).
- **`ד. מגורים`** ("residential apartment"): the **older label for the same thing**. Up to 2003 it holds most flat sales,
  and `דירה בבית קומות` takes over in 2003-04. It has the same size (80 m², 3 rooms) and price per m² as the new label in the same years,
  so leaving it out would silently drop most of 1998-2003.
- **`מגורים`** (408K rows): *not* flats. Rooms are recorded in 1% of rows, the median area is 495 m² and the median price
  is under ₪1K/m². It is residential **land**.
- `דירת נופש` (holiday flats), cottages and detached houses are residential but a different market, so they are left out.
- `פנטהאוז`, `דופלקס` and `מיני פנטהאוז` do not exist as labels in this data (penthouses appear as `דירת גג`).
""")

md("""
Left untouched on purpose at this stage: very large areas and very large or small amounts. They are real for
land and commercial deals, so they are handled per property type in 3.4.

### 3.3 Missing values: drop or fill?
Missingness is what matters for the **apartment** analysis. Check how much is missing there, whether it is random,
and whether it is concentrated in certain years.
""")

code(r'''
apt_all = df[df["deal_nature"].isin(APT_TYPES)]

miss = pd.DataFrame({"missing %": apt_all[["asset_area", "room_num", "year_built", "portion", "settlement"]].isna().mean() * 100})
print(f"Apartment-type rows: {len(apt_all):,}")
print(miss.round(1).to_string())

# Is "area missing" random? Compare the deals that have it with those that don't.
cmp = (apt_all.assign(area_missing=apt_all["asset_area"].isna())
       .groupby("area_missing")
       .agg(deals=("deal_amount", "size"), median_price=("deal_amount", "median"),
            median_year=("year", "median"), share_before_2003=("year", lambda y: (y < 2003).mean())))
print("\nDo apartments with a missing area differ from those with an area?")
print(cmp.round(2).to_string())

miss_year = apt_all.groupby("year")[["asset_area", "room_num", "year_built"]].apply(lambda d: d.isna().mean() * 100)
fig, ax = plt.subplots()
for col, color in zip(miss_year.columns, [C[0], C[1], C[2]]):
    ax.plot(miss_year.index, miss_year[col], color=color, label=col)
ax.yaxis.set_major_formatter(mtick.PercentFormatter())
ax.set_ylim(0)
ax.legend(frameon=False)
ax.set_title("Apartment rows with missing values, by deal year")
plt.show()
''')

md("""
**Missingness is not random.** Apartments without an area have a much lower median price and come mostly from
earlier years (see the table and chart above). Dropping them therefore tilts the price sample toward newer, pricier
deals, and no imputation would fix that: the unknown values are exactly the ones we can't learn from.

**Decisions**
| field | decision | reason |
|---|---|---|
| `asset_area` | **drop** the row from price/m² analyses | it's the denominator of the target. Imputing it would manufacture the price/m² |
| `deal_amount` | no NaNs; implausible values are filtered (3.4) | |
| `settlement` | **drop** from city analyses | a handful of rows, can't be located |
| `portion` | **keep** partial shares and **normalise** the price (`deal_amount / portion`). **Drop** only rows where the share is unknown | partial deals are common and real (3.5). Without a known share the price can't be converted |
| `room_num` | **keep** the row, leave NaN | only the by-rooms chart needs it. Dropping would lose valid price data from every other chart |
| `year_built` | **keep** the row, leave NaN | same: only the building-age chart needs it |
| deal year | **keep** all years (1998 on) | the old flat label `ד. מגורים` (3.2) covers the early years, so they are not just new builds |

Rooms and build year are not imputed. A model-based fill (e.g. rooms from area) would be correlated with price by
construction and would bias the by-rooms and by-age comparisons.
""")

md("### 3.4 Anomalies in apartment deals (row level)\nFirst, the raw shape of price, area and price per m².")

code(r'''
fig, axes = plt.subplots(1, 3, figsize=(14, 3.8))
unit_price = apt_all["deal_amount"] / apt_all["portion"]            # price of the whole unit (see 3.5)
price_m2 = (unit_price / apt_all["asset_area"]).replace([np.inf, -np.inf], np.nan)
# (values, lower limit, upper limit, view range as powers of 10, tick labels, title)
panels = [
    (unit_price, None, None, (3, 8), ["1K", "10K", "100K", "1M", "10M", "100M"], "Price (₪, log scale)"),
    (apt_all["asset_area"], 15, 1_000, (0, 4), ["1", "10", "100", "1,000", "10,000"], "Area (m², log scale)"),
    (price_m2, 1_000, 150_000, (1, 6), ["10", "100", "1K", "10K", "100K", "1M"], "Price per m² (₪, log scale)"),
]
for ax, (x, lo, hi, (a, b), ticklabels, title) in zip(axes, panels):
    x = x.dropna()
    x = x[x > 0]
    ax.hist(np.log10(x), bins=np.linspace(a, b, 100), color=C[0])
    for limit in (lo, hi):
        if limit:
            ax.axvline(np.log10(limit), color=C[7], ls="--", lw=1)
    ax.set_xticks(range(a, b + 1), ticklabels, fontsize=8)
    ax.set_yticks([])
    ax.set_title(title)
    outside = ((x < 10**a) | (x > 10**b)).sum()
    ax.text(0.98, 0.95, f"{outside:,} off-chart", transform=ax.transAxes, ha="right", va="top", fontsize=8, color=INK2)
plt.tight_layout(); plt.show()
''')

md("""
Dashed lines mark the limits used below. The bulk of the distribution sits inside them. The tails mix real deals
(large penthouses, luxury flats) with errors (a 1 m² area, a symbolic ₪1,000 price, an extra zero in the area).

Instead of cutting at a fixed price or area, a deal is dropped only when it is **inconsistent or implausible**:
1. **Physically implausible size**: under 15 m² or over 1,000 m² cannot be a flat.
2. **Price and area disagree**: price per m² outside ₪1K-150K. A real large flat has a normal price per m². A typo does not.
3. **Local outlier**: price per m² far from its own city and year (robust z-score). A fixed cap can't do this, because
   ₪40K/m² is normal in Tel Aviv and an error in Dimona.

Real extreme deals (a big penthouse, a luxury flat) pass all three tests and are **kept**.
""")

code(r'''
MIN_M2, MAX_M2 = 15, 1_000                      # physically plausible dwelling size
PPSQM_LO, PPSQM_HI = 1_000, 150_000             # price and area must agree

# `deal_amount` is what was paid for the share sold. Dividing by `portion` gives the price of the whole unit (3.5).
df["unit_price"] = df["deal_amount"] / df["portion"]
df["ppsqm"] = df["unit_price"] / df["asset_area"]

stages = [
    ("apartment property types (3.2)",       df["deal_nature"].isin(APT_TYPES)),
    ("share sold known (0 < portion <= 1)",  df["portion"].between(0, 1)),
    ("settlement known",                     df["settlement"].notna()),
    ("area known",                           df["asset_area"].notna()),
    (f"area {MIN_M2}-{MAX_M2:,} m²",         df["asset_area"].between(MIN_M2, MAX_M2)),
    (f"price/m² ₪{PPSQM_LO:,}-{PPSQM_HI:,}", df["ppsqm"].between(PPSQM_LO, PPSQM_HI)),
]
keep = pd.Series(True, index=df.index)
waterfall = []
for name, cond in stages:
    before = int(keep.sum())
    keep &= cond.fillna(False)
    waterfall.append({"step": name, "rows removed": before - int(keep.sum()), "rows left": int(keep.sum())})
    if name == "area known":
        cand_mask = keep.copy()      # every whole-unit apartment deal with a usable area, before the plausibility tests
apt = df[keep].copy()
pd.DataFrame(waterfall)
''')

code(r'''
# Statistical filter: robust z-score (median/MAD) of log price per m² within each settlement-year.
# Only groups with >= 30 deals are judged; small groups don't have a reliable "local normal".
lp = np.log(apt["ppsqm"])
grp = [apt["settlement"], apt["year"]]
med = lp.groupby(grp).transform("median")
mad = (lp - med).abs().groupby(grp).transform("median").replace(0, np.nan)
size = lp.groupby(grp).transform("size")
z = 0.6745 * (lp - med) / mad
is_outlier = (size >= 30) & (z.abs() > 4)
print(f"Statistical outliers removed: {is_outlier.sum():,} ({is_outlier.mean():.2%} of {len(apt):,} apartment deals)")

fig, ax = plt.subplots(figsize=(11, 3.8))
bins = np.linspace(np.log10(1_000), np.log10(150_000), 100)
ax.hist(np.log10(apt["ppsqm"]), bins=bins, color=C[0], label="kept")
ax.hist(np.log10(apt.loc[is_outlier, "ppsqm"]), bins=bins, color=C[7], label="removed as local outlier")
ax.set_xticks([3, 4, 5]); ax.set_xticklabels(["₪1K", "₪10K", "₪100K"])
ax.set_yscale("log"); ax.legend(frameon=False)
ax.set_ylabel("deals (log scale)")
ax.set_title("Price per m²: what the statistical filter removes")
plt.show()

# Record why each candidate deal was dropped (empty = kept)
cand = df[cand_mask].copy()
cand["drop_reason"] = ""
cand.loc[~cand["asset_area"].between(MIN_M2, MAX_M2), "drop_reason"] = f"area outside {MIN_M2}-{MAX_M2:,} m²"
cand.loc[(cand["drop_reason"] == "") & ~cand["ppsqm"].between(PPSQM_LO, PPSQM_HI), "drop_reason"] = f"price/m² outside ₪{PPSQM_LO:,}-{PPSQM_HI:,}"
cand.loc[is_outlier[is_outlier].index, "drop_reason"] = "local price/m² outlier"
apt = cand[cand["drop_reason"] == ""].copy()
apt["age"] = apt["year"] - apt["year_built"]
print(f"{len(apt):,} apartment deals kept ({len(apt)/df['deal_nature'].isin(APT_TYPES).sum():.0%} of apartment-type rows)")
''')

md("""
The removed deals (red) are spread across the whole price range, not just the tails. That is intended: a ₪10K/m² deal is
ordinary nationally but an anomaly in Tel Aviv, and the filter judges each deal against its own city and year.

### 3.5 Partial-share deals (`portion` < 1): separate frame, normalised price
About 1 in 5 apartment deals sells only a share of the flat (half, a third, a quarter...). These are real transactions, so they are
**kept**, but `deal_amount` is then the price of *the share*, not of the flat. `partial` holds them in a separate frame, and the
claim "the paid amount is for the share" is tested against whole-unit deals in the same city and year.
""")

code(r'''
partial = cand[cand["portion"] < 1].copy()
partial["verdict"] = np.where(partial["drop_reason"] == "", "kept", "dropped")
partial["paid_ppsqm"] = partial["deal_amount"] / partial["asset_area"]          # price per m² as recorded
print(f"{len(partial):,} partial-share deals = {len(partial)/len(cand):.1%} of apartment candidates; "
      f"{(partial['verdict'] == 'kept').mean():.1%} survive the plausibility tests")

# Reference: median price per m² of whole-unit deals in the same settlement and year (>= 30 deals)
whole = cand[cand["portion"] == 1]
local = whole.groupby(["settlement", "year"])["ppsqm"].agg(["median", "size"]).query("size >= 30")["median"].rename("local_whole")
chk = partial.join(local, on=["settlement", "year"]).dropna(subset=["local_whole"]).copy()
chk["paid / whole"] = chk["paid_ppsqm"] / chk["local_whole"]
chk["normalised / whole"] = chk["ppsqm"] / chk["local_whole"]
chk["portion group"] = np.select(
    [chk["portion"].between(0.49, 0.51), chk["portion"].between(0.32, 0.34), chk["portion"].between(0.24, 0.26),
     chk["portion"] < 0.24, chk["portion"] > 0.51],
    ["1/2", "1/3", "1/4", "under 1/4", "over 1/2"], default="other")
order_ = ["1/2", "1/3", "1/4", "under 1/4", "over 1/2", "other"]
share_test = chk.groupby("portion group").agg(
    deals=("portion", "size"), expected_if_share=("portion", "median"),
    paid_over_whole=("paid / whole", "median"),
    normalised_over_whole=("normalised / whole", "median"),
    normalised_q25=("normalised / whole", lambda x: x.quantile(.25)),
    normalised_q75=("normalised / whole", lambda x: x.quantile(.75))).loc[order_]
display(share_test.round(2))
ref_ratio = (whole.join(local, on=["settlement", "year"]).dropna(subset=["local_whole"])
             .eval("ppsqm / local_whole").quantile([.25, .5, .75]))
print("Whole-unit deals vs their own city-year median (q25 / median / q75):", ref_ratio.round(2).tolist())
''')

md("""
**Reading the table.** If the paid amount were the price of the whole flat, `paid_over_whole` would be about 1. Instead it is
close to the share sold (0.50 for halves, 0.33 for thirds), so `deal_amount` is the price of the **share**. After dividing by
`portion`, `normalised_over_whole` is about 1.0, with a spread similar to whole-unit deals. The very small shares (under 1/4) are
noisier, because a small error in `portion` is magnified by the division.

So partial deals enter `apt` with `unit_price = deal_amount / portion`, and price per m² and every price chart in section 5 use
`unit_price`. They go through the same plausibility and outlier tests as whole-unit deals. Examples:
""")

code(r'''
show_cols = ["portion group", "verdict", "drop_reason", "settlement", "deal_date", "deal_amount", "portion", "unit_price",
             "asset_area", "paid_ppsqm", "ppsqm", "local_whole", "gush", "chelka"]
ex = partial.join(chk[["portion group", "local_whole"]]).dropna(subset=["local_whole"])
examples = pd.concat([g.sample(min(4, len(g)), random_state=1)
                      for _, g in ex.groupby(["portion group", "verdict"])])[show_cols].reset_index(drop=True)
examples["deal_date"] = examples["deal_date"].dt.strftime("%Y-%m-%d")
examples
''')

md("""
### 3.6 Result
- `df`: all deals, deduplicated, with placeholders and impossible values nulled. Used for volume and property-type charts. **No rows dropped** beyond exact duplicates.
- `apt`: the clean apartment subset used for every price analysis in section 5. It keeps real extreme deals and partial-share deals (price normalised to `unit_price`).
- `partial`: all apartment deals with `portion` < 1, with a `verdict` (kept or dropped) and the reason.
- `cand`: every apartment candidate with its `drop_reason` (empty = kept).
""")

code(r'''
summary = pd.DataFrame({
    "rows": [len(raw), len(df), df["deal_nature"].isin(APT_TYPES).sum(), len(apt)],
}, index=["raw file", "after removing duplicates (df)", "apartment-type rows", "clean apartment deals (apt)"])
summary["% of raw"] = summary["rows"] / len(raw) * 100
summary.round(1)
''')

md("## 4. Deal volume")

code(r'''
yearly = df.groupby("year").size()
full_years = yearly.loc[:2025]          # 2026 is a partial year
fig, ax = plt.subplots()
ax.plot(full_years.index, full_years.values, marker="o", markersize=4)
ax.plot([2025, 2026], yearly.loc[[2025, 2026]].values, ls=":", color=C[0])
ax.annotate("2026: partial year", (2026, yearly.loc[2026]), xytext=(-10, 10),
            textcoords="offset points", ha="right", color=INK2, fontsize=9)
peak = full_years.idxmax()
ax.annotate(f"peak {peak}: {full_years.max():,}", (peak, full_years.max()), xytext=(0, 8),
            textcoords="offset points", ha="center", color=INK, fontsize=9)
ax.yaxis.set_major_formatter(thousands)
ax.set_ylim(0)
ax.set_title("Deals per year (all property types)")
plt.show()
''')

code(r'''
monthly = df[df["deal_date"] >= "2018-01-01"].groupby("month").size()
fig, ax = plt.subplots()
ax.plot(monthly.index.to_timestamp(), monthly.values)
ax.yaxis.set_major_formatter(thousands)
ax.set_ylim(0)
ax.set_title("Deals per month since 2018 (all types)")
plt.show()
''')

code(r'''
types = df["deal_nature"].value_counts()
top = types.head(15)[::-1]
fig, ax = plt.subplots(figsize=(10, 6))
ax.barh([heb(t) for t in top.index], top.values, color=C[0], height=0.7)
for y, v in enumerate(top.values):
    ax.text(v, y, f" {v/len(df):.1%}", va="center", fontsize=8, color=INK2)
ax.xaxis.set_major_formatter(thousands)
ax.grid(axis="y", visible=False)
ax.set_title("Top 15 property types by number of deals")
plt.show()
print(f"{types.size} types in total; the top 15 cover {types.head(15).sum()/len(df):.1%} of deals")
''')

md("""
## 5. Residential apartments

Price analysis (`unit_price` = price of the whole flat) uses the clean subset `apt` built in section 3 (apartment types, whole units and partial shares at their whole-unit price, physically plausible size,
consistent price per m², no local outliers). `RECENT` is the same subset restricted to 2024 on.

Prices are **nominal** (not adjusted for inflation).
""")

code(r'''
RECENT = apt[apt["year"] >= 2024]
print(f"{len(apt):,} apartment deals, {len(RECENT):,} since 2024")
''')

code(r'''
by_year = apt.groupby("year").agg(deals=("ppsqm", "size"),
                                  median_price=("unit_price", "median"),
                                  median_ppsqm=("ppsqm", "median"),
                                  median_area=("asset_area", "median"))
by_year["ppsqm_yoy"] = by_year["median_ppsqm"].pct_change()
by_year
''')

code(r'''
# Two measures with different scales -> two charts, not a dual axis
fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
for ax, col, title, color in [
    (axes[0], "median_price", "Median apartment price", C[0]),
    (axes[1], "median_ppsqm", "Median price per m²", C[1]),
]:
    s = by_year[col]
    ax.plot(s.index, s.values, color=color, marker="o", markersize=3)
    ax.yaxis.set_major_formatter(shekels)
    ax.set_ylim(0)
    ax.set_title(title)
    first, last = s.index.min(), s.index.max()
    ax.text(0.98, 0.05, f"{s[last]/s[first]:.1f}x from {first} to {last} (nominal)", transform=ax.transAxes,
            ha="right", fontsize=9, color=INK2)
plt.tight_layout(); plt.show()
''')

md("### Cities")

code(r'''
RECENT = apt[apt["year"] >= 2024]
city = (RECENT.groupby("settlement")
        .agg(deals=("ppsqm", "size"), median_price=("unit_price", "median"),
             median_ppsqm=("ppsqm", "median"), median_area=("asset_area", "median"),
             whole_share=("portion", lambda x: (x == 1).mean()))
        # a locality where almost every deal is a partial share (e.g. cooperative settlements) has no reliable whole-flat price
        .query("deals >= 300 and whole_share >= 0.2").sort_values("median_ppsqm", ascending=False))
print(f"{len(city)} localities with at least 300 apartment deals since 2024 (and at least 20% whole-unit deals)")

fig, axes = plt.subplots(1, 2, figsize=(13, 6))
for ax, part, title, color in [
    (axes[0], city.head(15), "Most expensive per m² (2024+)", C[0]),
    (axes[1], city.tail(15), "Least expensive per m² (2024+)", C[2]),
]:
    part = part[::-1]
    ax.barh([heb(s) for s in part.index], part["median_ppsqm"], color=color, height=0.7)
    for y, v in enumerate(part["median_ppsqm"]):
        ax.text(v, y, f" ₪{v/1e3:,.0f}K", va="center", fontsize=8, color=INK2)
    ax.xaxis.set_major_formatter(shekels)
    ax.grid(axis="y", visible=False)
    ax.set_title(title)
plt.tight_layout(); plt.show()
''')

code(r'''
top_cities = apt["settlement"].value_counts().head(12).index
heat = (apt[apt["settlement"].isin(top_cities) & (apt["year"] >= 2008)]
        .pivot_table(index="settlement", columns="year", values="ppsqm", aggfunc="median"))
heat = heat.loc[heat[heat.columns.max()].sort_values(ascending=False).index]

fig, ax = plt.subplots(figsize=(13, 5.5))
im = ax.imshow(heat.values / 1e3, aspect="auto", cmap="Blues")
ax.set_xticks(range(heat.shape[1]), heat.columns, rotation=45)
ax.set_yticks(range(heat.shape[0]), [heb(s) for s in heat.index])
ax.grid(False)
for i in range(heat.shape[0]):
    for j in range(heat.shape[1]):
        v = heat.values[i, j] / 1e3
        if not np.isnan(v):
            ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=7,
                    color="white" if im.norm(v) > 0.55 else INK)
fig.colorbar(im, ax=ax, label="₪K per m²", shrink=0.8)
ax.set_title("Median price per m² (₪K), 12 busiest cities")
plt.tight_layout(); plt.show()
''')

code(r'''
# Growth indexed to 2015 = 100, so cities with different price levels compare on one axis
FOCUS = {"תל אביב-יפו": C[0], "ירושלים": C[1], "חיפה": C[2], "באר שבע": C[6]}
idx = (apt[apt["settlement"].isin(FOCUS) & (apt["year"] >= 2010)]
       .pivot_table(index="year", columns="settlement", values="ppsqm", aggfunc="median"))
idx = idx / idx.loc[2015] * 100

fig, ax = plt.subplots()
for city_name, color in FOCUS.items():
    s = idx[city_name]
    ax.plot(s.index, s.values, color=color, label=heb(city_name))
    ax.annotate(f" {heb(city_name)} {s.iloc[-1]:.0f}", (s.index[-1], s.iloc[-1]),
                va="center", fontsize=9, color=INK)
ax.axhline(100, color=INK2, lw=0.8, ls="--")
ax.set_xlim(right=idx.index.max() + 2.5)
ax.xaxis.set_major_locator(mtick.MaxNLocator(integer=True))
ax.legend(frameon=False, loc="upper left")
ax.set_title("Price per m² index (2015 = 100; 2026 is a partial year)")
plt.show()
''')

md("### Rooms and building age")

code(r'''
r = RECENT.dropna(subset=["room_num"]).copy()
r["rooms"] = r["room_num"].clip(upper=6).round().astype(int).map(lambda x: "6+" if x >= 6 else str(x))
order = ["1", "2", "3", "4", "5", "6+"]
groups = [r.loc[r["rooms"] == k, "unit_price"] for k in order]

fig, ax = plt.subplots()
ax.boxplot(groups, tick_labels=order, showfliers=False, widths=0.5,
           medianprops=dict(color=C[1], lw=2), boxprops=dict(color=C[0]),
           whiskerprops=dict(color=INK2), capprops=dict(color=INK2))
for i, g in enumerate(groups, 1):
    ax.text(i + 0.3, g.median(), f"₪{g.median()/1e6:.2f}M", va="center", fontsize=8, color=INK)
ax.yaxis.set_major_formatter(shekels)
ax.set_xlabel("Rooms")
ax.set_title("Apartment price by number of rooms (2024+; box = middle 50%)")
plt.show()
r.groupby("rooms")["unit_price"].agg(["size", "median"]).loc[order]
''')

code(r'''
a = RECENT.dropna(subset=["age"]).copy()
bins = [-np.inf, 1, 5, 10, 20, 30, 40, 50, np.inf]
labels = ["new (≤1)", "2-5", "6-10", "11-20", "21-30", "31-40", "41-50", "50+"]
a["age_bin"] = pd.cut(a["age"], bins=bins, labels=labels)
age_tbl = a.groupby("age_bin", observed=True).agg(deals=("ppsqm", "size"),
                                                  median_ppsqm=("ppsqm", "median"),
                                                  median_area=("asset_area", "median"))
fig, ax = plt.subplots()
ax.bar(age_tbl.index.astype(str), age_tbl["median_ppsqm"], color=C[0], width=0.7)
ax.yaxis.set_major_formatter(shekels)
ax.set_xlabel("Building age at time of sale (years)")
ax.set_title("Median price per m² by building age (2024+)")
plt.show()
age_tbl
''')

code(r'''
# Share of deals in new buildings (pre-sale or up to 1 year old) per year
# Sanity check on including the early years: the new-build share should not jump at the old/new label switch (2003)
apt_all = df[df["deal_nature"].isin(APT_TYPES) & (df["portion"] == 1)].copy()
apt_all["age"] = apt_all["year"] - apt_all["year_built"]
new_share = apt_all.dropna(subset=["age"]).assign(new=lambda d: d["age"] <= 1).groupby("year")["new"].mean()
fig, ax = plt.subplots()
ax.plot(new_share.index, new_share.values * 100, color=C[2])
ax.yaxis.set_major_formatter(mtick.PercentFormatter())
ax.set_ylim(0)
ax.set_title("Share of apartment deals in new buildings (age ≤ 1 year)")
plt.show()
''')

md("""## Summary

- **Volume:** about 3.84M deal records from 1998 to Sep 2026, with no exact duplicates. Deals peaked in **2021 (229K)**,
  then fell sharply to 133K in 2023 (rate hikes) before partly recovering in 2024.
- **Mix:** apartments in buildings (דירה בבית קומות) make up 57% of all deals. The top 15 of 47 property types cover 97%.
- **Cleaning:** 0 is a placeholder for unknown, and impossible values (build year 9999, 99 rooms) are nulled, but the rows are kept.
  Apartments are the 4 flat labels (the old `ד. מגורים` label covers 1998 to 2003). Partial-share deals (about 1 in 5) are kept and
  their price is divided by the share sold. Price analyses drop rows without area or share and
  drop deals that are physically implausible, whose price and area disagree, or that are local outliers. Extreme but
  consistent deals (big penthouses, luxury flats) are kept. This leaves 1.85M clean apartment deals (72% of apartment-type rows).
- **Missing values:** 0 is used as a placeholder. Build year is missing in 28% of rows, area in 21%, rooms in 34%.
  Outliers are extreme (areas up to 10⁹ m², prices up to ₪9B), so medians and filtered subsets are used throughout.
- **Prices (nominal, apartments):** the median price went from ₪0.54M (1998) to about ₪2.02M (2024), and price per m²
  from about ₪6.7K to ₪20.9K. Prices were flat from 2017 to 2020, rose fast from 2021 to 2024, and slipped slightly
  in 2025 to 2026.
- **Cities (2024+):** Tel Aviv-Yafo is in a league of its own at about ₪53K/m². Next are Herzliya, Givatayim,
  Ramat HaSharon, Jerusalem and Ramat Gan (₪36K to ₪40K). The periphery (Katzrin, Beit She'an, Ofakim, Dimona) sits at
  ₪7.5K to ₪9.5K/m², a 6 to 7x gap.
- **Growth since 2015:** Tel Aviv +72% and Jerusalem +75%, while Haifa (+38%) and Be'er Sheva (+18%) grew much less.
- **Rooms:** 3-room apartments have a median price close to 2-room ones (about ₪1.5M). After that, each extra room
  adds about ₪0.5M to ₪1M.
- **Building age:** price per m² barely changes with age. Location matters far more than whether the building is new.

*Caveats:* prices are not inflation-adjusted, 2026 is a partial year, and medians mix locations, so city-level
changes partly reflect which neighborhoods traded in a given year.""")

nb["cells"] = cells
nb["metadata"]["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
nbf.write(nb, "analysis.ipynb")
print("wrote analysis.ipynb")

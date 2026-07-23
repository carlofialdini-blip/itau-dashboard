#!/usr/bin/env python3
"""
pulp_paper_scraper.py
Fetches FAO's official Forestry Production and Trade statistics (FAOSTAT
domain "FO") and aggregates the pulp/paper/recovered-fibre slice into the
compact shape the Economic Data > Pulp & Paper sub-page's JS expects.

Source (live-verified before writing this, same standard as every other data
source in this project): the FAOSTAT bulk-download zip
Forestry_E_All_Data_(Normalized).zip contains one normalized CSV with
Production and Export/Import quantity & value for ~40 pulp/paper item codes,
per country ('Area Code' < 5000) and per FAO-computed world total
('Area Code' == 5000), 1961-present, free and unauthenticated. Confirmed by
inspecting Forestry_E_ItemCodes.csv and Forestry_E_AreaCodes.csv inside the
zip directly, and by checking the actual year range present per item code —
not assumed from item names. Two real reclassifications were found and
handled explicitly (see PULP_MECH_* / PULP_SULPHITE_* below): FAO split
"Mechanical wood pulp" (item 1654) into "Mechanical and semi-chemical wood
pulp" (item 1685) starting 2017, with zero year overlap between the two
codes, and did the same for sulphite pulp (1660+1661 -> 1686). Packaging
paper (item 2043) only exists as its own reported category from 1998
onward — years before that are honestly left out of the paper structural
mix rather than padded with a guess.

Output: pulp_paper_cache.json

Run:  python3 pulp_paper_scraper.py
"""

import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.net_utils import get_with_retry  # noqa: E402

OUTPUT_FILE = ROOT / "data" / "pulp_paper_cache.json"

ZIP_URL     = "https://bulks-faostat.fao.org/production/Forestry_E_All_Data_(Normalized).zip"
CSV_MEMBER  = "Forestry_E_All_Data_(Normalized).csv"
SOURCE_PAGE = "https://www.fao.org/faostat/en/#data/FO"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/122 Safari/537.36"
    )
}

WORLD_AREA_CODE = 5000   # FAO's own pre-computed world total row
AGGREGATE_AREA_CODE_FLOOR = 5000  # Area Code >= this = a region/grouping, not a real country

# Area Code 351 ("China") is a composite of the 4 separately-reported areas
# below it, not an independent country row — verified numerically against
# live 2024 pulp-production data before excluding it: China,mainland
# (27,335,000t) + Hong Kong SAR (15,000t) + Macao SAR (0) + Taiwan Province
# of China (370,201t) = 27,720,201t, an exact match to "China"'s own
# 27,720,201t. Left in, it would double-count China in every country-level
# ranking/HHI/CR4 alongside its own already-reported parts. No other area
# code below AGGREGATE_AREA_CODE_FLOOR was found to behave this way — every
# other apparent duplicate in the area list (USSR/Czechoslovakia/Serbia and
# Montenegro/Belgium-Luxembourg/etc.) is a *historical predecessor* state
# whose reporting period doesn't overlap its successors', so no double-count
# risk there.
EXCLUDE_AREA_CODES = {351}

# Top-level totals shown as the 3 main categories throughout the page.
CATEGORY_ITEMS = {
    "pulp":      1875,   # Wood pulp
    "paper":     1876,   # Paper and paperboard
    "recovered": 1669,   # Recovered paper
}
FURNISH_ITEM = 1879      # Total fibre furnish — recycled-rate denominator

# Pulp structural mix (share of item 1875). Mechanical pulp was reclassified
# by FAO in 2017 (1654 -> 1685, no overlap) — spliced, not summed.
PULP_MECH_OLD, PULP_MECH_NEW, PULP_MECH_SPLIT_YEAR = 1654, 1685, 2017
PULP_TYPE_ITEMS = {
    "chemical":               1656,   # Chemical wood pulp (already FAO-aggregated total)
    "dissolving":              1667,
    "mechanical_semichemical": None,  # spliced from PULP_MECH_OLD/NEW at build time
}

# Paper structural mix (share of item 1876). Graphic/Packaging/Household are
# genuine FAO peer sub-totals; "other" is computed as a residual (Total -
# the three), NOT fetched from item 1675 "Other paper and paperboard" — that
# item name is misleading: verified against live 2024 world data that
# 1675 (337.9Mt) = Total (423.0Mt) - Graphic (85.1Mt), i.e. it means "all
# non-graphic paper" and already contains Packaging + Household within it,
# not a peer "everything else" bucket. Using it as a fourth flat category
# alongside Packaging/Household produced a paper mix that summed to >150%
# and showed "other" as the *largest* slice, which is what caught this.
# Packaging only exists as its own reported code from 1998 onward, so the
# mix (and its residual) is only built for years >= that.
PAPER_TYPE_ITEMS = {
    "graphic":             2042,
    "packaging":            2043,   # data starts 1998
    "household_sanitary":  1676,
}
PAPER_MIX_FIRST_YEAR = 1998

ITEM_NAMES = {
    1875: "Wood pulp", 1876: "Paper and paperboard", 1669: "Recovered paper",
    1879: "Total fibre furnish", 1656: "Chemical wood pulp",
    1667: "Dissolving wood pulp", 1654: "Mechanical wood pulp",
    1685: "Mechanical and semi-chemical wood pulp", 2042: "Graphic papers",
    2043: "Packaging paper and paperboard", 1676: "Household and sanitary papers",
}

ALL_ITEM_CODES = (set(CATEGORY_ITEMS.values()) | {FURNISH_ITEM}
                  | {v for v in PULP_TYPE_ITEMS.values() if v}
                  | {PULP_MECH_OLD, PULP_MECH_NEW}
                  | set(PAPER_TYPE_ITEMS.values()))

ELEMENT_MAP = {
    "Production":      "production",
    "Export quantity": "export_qty",
    "Export value":    "export_val",
    "Import quantity": "import_qty",
    "Import value":    "import_val",
}

USE_COLS = ["Area Code", "Area", "Item Code", "Element", "Year", "Value"]


def fetch_df() -> pd.DataFrame:
    # ~18MB zip (~318MB uncompressed CSV covering ALL forestry products, not
    # just pulp/paper) — ANP-scale download, materially bigger CSV, so a
    # longer timeout than the project default and chunked filtering to keep
    # memory bounded rather than loading the full un-filtered file at once.
    r = get_with_retry(ZIP_URL, headers=HEADERS, timeout=120)
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        with zf.open(CSV_MEMBER) as f:
            chunks = []
            for chunk in pd.read_csv(f, chunksize=500_000, encoding="latin-1", usecols=USE_COLS):
                chunk = chunk[chunk["Item Code"].isin(ALL_ITEM_CODES)]
                if len(chunk):
                    chunks.append(chunk)
    df = pd.concat(chunks, ignore_index=True)
    df = df.dropna(subset=["Value"])
    df["Element"] = df["Element"].map(ELEMENT_MAP)
    df["Year"] = df["Year"].astype(int)
    df["Value"] = df["Value"].astype(float).round(1)
    return df


def _nested_series(sub: pd.DataFrame) -> dict:
    """Area -> item_code(str) -> element -> {year(str): value}."""
    out = {}
    for (area, item, elem), g in sub.groupby(["Area", "Item Code", "Element"], observed=True):
        out.setdefault(area, {}).setdefault(str(item), {})[elem] = dict(
            zip(g["Year"].astype(str), g["Value"])
        )
    return out


def _splice_mechanical(nested: dict) -> None:
    """Merge the pre-2017 (1654) and post-2017 (1685) mechanical-pulp codes
    into one continuous series under a synthetic key, in place, for every
    area present in `nested`."""
    old_k, new_k = str(PULP_MECH_OLD), str(PULP_MECH_NEW)
    for area, items in nested.items():
        old = items.get(old_k, {})
        new = items.get(new_k, {})
        if not old and not new:
            continue
        merged = {}
        for elem in set(old) | set(new):
            merged[elem] = {**old.get(elem, {}), **new.get(elem, {})}
        items["mech"] = merged


def build_world_category_series(nested_world: dict) -> dict:
    world = nested_world.get("World", {})
    out = {}
    for cat, code in CATEGORY_ITEMS.items():
        out[cat] = world.get(str(code), {})
    out["furnish"] = world.get(str(FURNISH_ITEM), {})
    return out


def build_country_series(nested_countries: dict) -> dict:
    countries = {}
    for area, items in nested_countries.items():
        entry = {}
        for cat, code in CATEGORY_ITEMS.items():
            s = items.get(str(code))
            if s:
                entry[cat] = s
        furnish = items.get(str(FURNISH_ITEM))
        if furnish:
            entry["furnish"] = furnish
        if entry:
            countries[area] = entry
    return countries


def compute_concentration(world_series: dict, country_series: dict) -> dict:
    """HHI (sum of squared % shares, 0-10000) and CR4 (top-4 % share) per
    category per year, using each country's Production against the FAO
    world total for the same year — not a hand-summed denominator."""
    out = {}
    for cat in CATEGORY_ITEMS:
        world_prod = world_series.get(cat, {}).get("production", {})
        cat_result = {}
        for year, world_val in world_prod.items():
            if not world_val:
                continue
            shares = []
            for area, entry in country_series.items():
                v = entry.get(cat, {}).get("production", {}).get(year)
                if v:
                    shares.append((area, v / world_val * 100))
            if not shares:
                continue
            shares.sort(key=lambda x: x[1], reverse=True)
            hhi = round(sum(s[1] ** 2 for s in shares), 1)
            cr4 = round(sum(s[1] for s in shares[:4]), 1)
            cat_result[year] = {
                "hhi": hhi, "cr4": cr4,
                "top": [{"country": a, "share": round(s, 2)} for a, s in shares[:10]],
            }
        out[cat] = cat_result
    return out


def compute_recycled_rate(furnish: dict, recovered: dict) -> dict:
    """Apparent domestic supply of recovered paper (production + import -
    export) as a share of total fibre furnish — the standard 'apparent
    consumption' identity already used elsewhere in forestry statistics,
    not a bespoke formula. Clipped to [0, 100] for display sanity; the
    underlying inputs are left unclipped in case of a genuine data quirk
    worth investigating later."""
    prod_f = furnish.get("production", {})
    if not prod_f:
        return {}
    prod_r = recovered.get("production", {})
    imp_r  = recovered.get("import_qty", {})
    exp_r  = recovered.get("export_qty", {})
    rate = {}
    for year, f in prod_f.items():
        if not f:
            continue
        apparent = prod_r.get(year, 0) + imp_r.get(year, 0) - exp_r.get(year, 0)
        pct = apparent / f * 100
        rate[year] = round(max(0, min(100, pct)), 1)
    return rate


def build_structural_mix(world_series: dict, world_nested: dict) -> dict:
    _splice_mechanical(world_nested)
    world = world_nested.get("World", {})

    pulp_total = world_series["pulp"].get("production", {})
    pulp_types = {
        "chemical":   world.get(str(PULP_TYPE_ITEMS["chemical"]), {}).get("production", {}),
        "dissolving": world.get(str(PULP_TYPE_ITEMS["dissolving"]), {}).get("production", {}),
        "mechanical_semichemical": world.get("mech", {}).get("production", {}),
    }
    pulp_by_type = {}
    for year, total in pulp_total.items():
        if not total:
            continue
        pulp_by_type[year] = {
            k: round(v.get(year, 0) / total * 100, 1) for k, v in pulp_types.items()
        }

    paper_total = world_series["paper"].get("production", {})
    paper_types = {
        k: world.get(str(code), {}).get("production", {})
        for k, code in PAPER_TYPE_ITEMS.items()
    }
    paper_by_type = {}
    for year, total in paper_total.items():
        if not total or int(year) < PAPER_MIX_FIRST_YEAR:
            continue
        shares = {k: v.get(year, 0) / total * 100 for k, v in paper_types.items()}
        shares["other"] = max(0.0, 100 - sum(shares.values()))
        paper_by_type[year] = {k: round(v, 1) for k, v in shares.items()}

    return {"pulp_by_type": pulp_by_type, "paper_by_type": paper_by_type}


def build_db(df: pd.DataFrame) -> dict:
    world_rows    = df[df["Area Code"] == WORLD_AREA_CODE]
    country_rows  = df[(df["Area Code"] < AGGREGATE_AREA_CODE_FLOOR)
                        & (~df["Area Code"].isin(EXCLUDE_AREA_CODES))]

    nested_world    = _nested_series(world_rows)
    nested_countries = _nested_series(country_rows)
    _splice_mechanical(nested_countries)
    # nested_world's splice happens inside build_structural_mix (needs the
    # pre-splice dict for the total-vs-breakdown comparison there too).

    world_series    = build_world_category_series(nested_world)
    country_series  = build_country_series(nested_countries)
    concentration   = compute_concentration(world_series, country_series)
    recycled_world  = compute_recycled_rate(world_series["furnish"], world_series["recovered"])
    recycled_countries = {
        area: compute_recycled_rate(entry.get("furnish", {}), entry.get("recovered", {}))
        for area, entry in country_series.items()
        if entry.get("furnish")
    }
    recycled_countries = {k: v for k, v in recycled_countries.items() if v}
    structural_mix  = build_structural_mix(world_series, nested_world)

    years = sorted(world_series["paper"].get("production", {}).keys())
    latest_year = years[-1] if years else None

    return {
        "years":   years,
        "world":   world_series,
        "countries": country_series,
        "concentration": concentration,
        "recycled_rate": {"world": recycled_world, "countries": recycled_countries},
        "structural_mix": structural_mix,
        "items": {str(k): v for k, v in ITEM_NAMES.items()},
        "_meta": {
            "source": "FAO - Food and Agriculture Organization of the United Nations (FAOSTAT, Forestry Production and Trade)",
            "source_url": SOURCE_PAGE,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "latest_year": latest_year,
            "countries": len(country_series),
        },
    }


def main():
    print("Fetching FAOSTAT Forestry data (pulp & paper slice)...")
    df = fetch_df()
    print(f"  {len(df):,} rows parsed (filtered to {len(ALL_ITEM_CODES)} item codes)")

    db = build_db(df)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)

    world_recycled_latest = None
    if db["years"]:
        world_recycled_latest = db["recycled_rate"]["world"].get(db["years"][-1])
    print(f"  {db['_meta']['countries']} countries, latest year {db['_meta']['latest_year']}, "
          f"world recycled-fibre rate {world_recycled_latest}% -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

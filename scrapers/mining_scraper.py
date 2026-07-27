#!/usr/bin/env python3
"""
mining_scraper.py
Fetches the British Geological Survey's World Mineral Statistics — free,
unauthenticated country-level mineral production/trade data, 1970-present —
and aggregates the headline hard-commodity slice into the shape the
Economic Data > Mining sub-page's JS expects. Same role for Mining that
pulp_paper_scraper.py plays for Pulp & Paper.

Source (live-verified before writing this, same standard as every other
data source in this project): BGS publishes World Mineral Statistics as an
OGC API Features service at ogcapi.bgs.ac.uk/collections/world-mineral-
statistics — free, no API key (unlike UN Comtrade, ruled out for the same
reason on the Pulp & Paper page). Confirmed live: 410,021 total records
split across Production (132,946) / Exports (111,304) / Imports (165,771),
per country/commodity/year, 1970-2024.

Unlike FAOSTAT there is no single bulk-download zip here — it's a paginated
REST API (`limit`/`offset`; a `limit=10000` request intermittently
truncated mid-stream during live testing, so this pages in chunks of 2000).

CATEGORIES below is a flat list of specific BGS `bgs_commodity_trans`
values, not the broader `erml_group` labels — verified live that a group
like "Iron and steel" actually contains three physically distinct series
with different units (iron ore / iron, pig / steel, crude) and "Bauxite,
alumina and aluminium" contains three more (bauxite / alumina / aluminium,
primary) — aggregating at the group level would silently sum incompatible
quantities, the same class of mistake the FAOSTAT "Other paper and
paperboard" bug was (see CLAUDE.md §4/§10). Where a metal is reported at
both mine and refined/smelter stage (copper, nickel, zinc, lead, tin), both
stages are kept as separate categories rather than forced into a single
"% mix" chart the way Pulp & Paper's fibre types are — a mine-stage tonne
and a refined-stage tonne of the same metal aren't shares of one common
total (refining can net-import concentrate), so two adjacent trend lines is
the honest representation, not a synthetic percentage breakdown. BGS also
has no value/USD field (`quantity` is physical volume only) and no
pre-computed "World" row (unlike FAOSTAT's Area Code 5000) — world totals
here are summed from the country-level rows.

Output: mining_cache.json

Run:  python3 mining_scraper.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.net_utils import get_with_retry  # noqa: E402

OUTPUT_FILE = ROOT / "data" / "mining_cache.json"

BGS_BASE   = "https://ogcapi.bgs.ac.uk/collections/world-mineral-statistics/items"
SOURCE_PAGE = "https://www.bgs.ac.uk/mineralsuk/statistics/world-mineral-statistics/"
PAGE_SIZE  = 2000  # a limit=10000 request intermittently truncated mid-stream live

HEADERS = {"Accept": "application/json"}

# (key, bgs_commodity_trans, label) — verified live against the actual
# reported series and units, not the broader erml_group.
CATEGORIES = [
    ("iron_ore",       "iron ore",            "Iron Ore"),
    ("crude_steel",    "steel, crude",        "Crude Steel"),
    ("copper",         "copper, mine",        "Copper (Mine)"),
    ("copper_refined", "copper, smelter",     "Copper (Smelter/Refined)"),
    ("gold",           "gold, mine",          "Gold"),
    ("silver",         "silver, mine",        "Silver"),
    ("nickel",         "nickel, mine",        "Nickel (Mine)"),
    ("zinc",           "zinc, mine",          "Zinc (Mine)"),
    ("lead",           "lead, mine",          "Lead (Mine)"),
    ("tin",            "tin, mine",           "Tin (Mine)"),
    ("bauxite",        "bauxite",             "Bauxite"),
    ("aluminium",      "aluminium, primary",  "Aluminium (Primary)"),
    ("manganese",      "manganese ore",       "Manganese Ore"),
]

STATISTIC_TYPES = {
    "Production":  "production",
    "Exports":     "export_qty",
    "Imports":     "import_qty",
}


def fetch_series(commodity: str, statistic_type: str) -> list:
    """Page through every record for one (commodity, statistic type) pair."""
    records, offset = [], 0
    while True:
        params = {
            "limit": PAGE_SIZE, "offset": offset,
            "bgs_commodity_trans": commodity,
            "bgs_statistic_type_trans": statistic_type,
            "f": "json",
        }
        r = get_with_retry(BGS_BASE, headers=HEADERS, timeout=30, params=params)
        data = r.json()
        records.extend(data.get("features", []))
        total = data.get("numberMatched", len(records))
        if offset + PAGE_SIZE >= total:
            break
        offset += PAGE_SIZE
        time.sleep(0.3)  # gentle rate-limit avoidance, same pattern as fetch_brazil_charts()
    return records


def build_db() -> dict:
    world     = {}   # cat_key -> element -> {year: value}
    countries = {}   # country -> cat_key -> element -> {year: value}
    items     = {}
    all_years = set()

    for key, commodity, label in CATEGORIES:
        print(f"  {label} ({commodity})...")
        unit = None
        cat_country_year = {}  # element -> country -> year -> value
        for stat_type, element in STATISTIC_TYPES.items():
            cat_country_year.setdefault(element, {})
            recs = fetch_series(commodity, stat_type)
            for f in recs:
                p = f["properties"]
                country = p.get("country_trans")
                year    = p.get("year", "")[:4]
                value   = p.get("quantity")
                if not country or not year or value is None:
                    continue
                unit = unit or p.get("units")
                all_years.add(year)
                cat_country_year[element].setdefault(country, {})[year] = value

        items[key] = {"label": label, "unit": unit or ""}
        for element, by_country in cat_country_year.items():
            for country, series in by_country.items():
                countries.setdefault(country, {}).setdefault(key, {})[element] = series
            world.setdefault(key, {})[element] = {
                year: round(sum(s.get(year, 0) for s in by_country.values()), 2)
                for year in {y for s in by_country.values() for y in s}
            }
        print(f"    {len(cat_country_year.get('production', {}))} producing countries")

    return world, countries, items, sorted(all_years)


def compute_concentration(world: dict, countries: dict) -> dict:
    """HHI (sum of squared % shares, 0-10000) and CR4 per commodity per
    year, using this dataset's own summed world total as the denominator —
    BGS has no pre-computed World row to read one from, unlike FAOSTAT."""
    out = {}
    for key, _, _ in CATEGORIES:
        world_prod = world.get(key, {}).get("production", {})
        cat_result = {}
        for year, world_val in world_prod.items():
            if not world_val:
                continue
            shares = []
            for country, entry in countries.items():
                v = entry.get(key, {}).get("production", {}).get(year)
                if v:
                    shares.append((country, v / world_val * 100))
            if not shares:
                continue
            shares.sort(key=lambda x: x[1], reverse=True)
            hhi = round(sum(s[1] ** 2 for s in shares), 1)
            cr4 = round(sum(s[1] for s in shares[:4]), 1)
            cat_result[year] = {
                "hhi": hhi, "cr4": cr4,
                "top": [{"country": c, "share": round(s, 2)} for c, s in shares[:10]],
            }
        out[key] = cat_result
    return out


# IBGE's PIM-PF (Pesquisa Industrial Mensal - Produção Física), SIDRA table
# 8888 — a monthly Brazil industrial-production INDEX (2022=100), live-
# verified current through March 2026, well past BGS's 2024 ceiling above.
# This is NOT a substitute for BGS's world tonnage/country rankings (it's
# Brazil-only, and an index, not absolute production) — it's the current-
# month complement, same role the public-company stock panel already
# plays, just for the underlying industrial activity instead of share
# prices. Code 129315 = "2 Indústrias extrativas" (extractive industries),
# the closest PIM-PF category to mining — confirmed via a live query
# against the table's own variable list, not assumed from the code number.
PIMPF_URL = "https://apisidra.ibge.gov.br/values/t/8888/n1/all/v/12606/p/all/c544/{code}"
PIMPF_MINING_CODE = "129315"


def fetch_brazil_index() -> dict:
    r = get_with_retry(PIMPF_URL.format(code=PIMPF_MINING_CODE), timeout=30)
    rows = r.json()[1:]
    data = {}
    for row in rows:
        try:
            data[row["D3C"]] = float(row["V"])  # D3C = "YYYYMM"
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "label": "Extractive Industries (mining) — Brazil",
        "unit": "Index, 2022=100",
        "source": "IBGE - Pesquisa Industrial Mensal (PIM-PF)",
        "data": data,
    }


def main():
    print("Fetching BGS World Mineral Statistics (hard commodities slice)...")
    world, countries, items, years = build_db()
    concentration = compute_concentration(world, countries)

    print("Fetching IBGE PIM-PF (Brazil extractive-industries index, monthly)...")
    brazil_index = fetch_brazil_index()
    print(f"  {len(brazil_index['data'])} monthly points")

    db = {
        "years": years,
        "world": world,
        "countries": countries,
        "concentration": concentration,
        "items": items,
        "brazil_index": brazil_index,
        "_meta": {
            "source": "British Geological Survey (BGS) - World Mineral Statistics",
            "source_url": SOURCE_PAGE,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "latest_year": years[-1] if years else None,
            "countries": len(countries),
            "commodities": len(CATEGORIES),
        },
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)

    print(f"  {len(countries)} countries, {len(CATEGORIES)} commodities, "
          f"latest year {db['_meta']['latest_year']} -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

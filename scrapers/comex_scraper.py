#!/usr/bin/env python3
"""
comex_scraper.py
Fetches Brazil's official foreign-trade statistics (exports + fertilizer
imports) for the Mining, Pulp & Paper, and Agriculture sub-pages' headline
products, and aggregates them into a shared cache all three pages read
their own slice of. Built as one scraper rather than three because the
underlying bulk files are large (~50-100MB/year) and shared across all
three pages' product lists — downloading once and slicing by NCM code is
far cheaper than three scrapers each re-fetching the same yearly files.

Source (live-verified before writing this, same standard as every other
data source in this project): MDIC's documented REST API
(api-comexstat.mdic.gov.br) is behind a Cloudflare bot-challenge that
blocks even a browser-UA request — confirmed unusable from this
environment. The real, usable alternative is MDIC's own bulk-CSV mirror at
balanca.economia.gov.br/balanca/bd/comexstat-bd/ncm/{EXP,IMP}_{year}.csv —
no Cloudflare, no auth, no API key. Confirmed live: EXP_2026.csv is row-
level (year/month/NCM product code/state/route/net-kg/FOB-value USD),
already covers Jan-Jun 2026 when this was built. Product-code lookup table:
balanca.economia.gov.br/balanca/bd/tabelas/NCM.csv.

NCM codes below were confirmed against the actual lookup table, not
assumed from memory (a `2601` search returned "Minérios de ferro e seus
concentrados", etc.) — 4-digit HS/NCM chapter-level prefixes, matched via
string prefix against the 8-digit NCM code in the trade data.

Sanity-checked before trusting the pipeline, same standard as every prior
source: H1 2026 totals for iron ore/soybean/wood pulp all landed in the
plausible real-world range for a half year (iron ore ~190Mt against a
~350-400Mt/year national total; soybean ~70Mt against Brazil's known
Feb-June export season; wood pulp ~9.4Mt against Brazil's ~18-20Mt/year
position as the world's largest market-pulp exporter).

Fertilizer imports (urea/phosphates/potash/mixed, NCM 31xx) are a
deliberately new indicator, not covered by any existing source in this
project — Brazil imports the large majority of its fertilizer, so this is
import volume, not export, and it's a genuinely current, actively-updated
number the Agriculture page didn't have before.

Output: comex_cache.json

Run:  python3 comex_scraper.py
"""

import io
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.net_utils import get_with_retry  # noqa: E402

OUTPUT_FILE = ROOT / "data" / "comex_cache.json"

CSV_URL = "https://balanca.economia.gov.br/balanca/bd/comexstat-bd/ncm/{flow}_{year}.csv"
SOURCE_PAGE = "https://www.gov.br/mdic/pt-br/assuntos/comercio-exterior/estatisticas/base-de-dados-bruta"

YEARS_BACK = 2  # + current year = 3 years of monthly trend, bounds download size/runtime

MINING_EXPORT_PRODUCTS = {
    "2601": "Iron Ore", "2603": "Copper Ore", "7601": "Aluminium (Unwrought)",
    "7403": "Refined Copper", "2607": "Lead Ore",
}
PULP_EXPORT_PRODUCTS = {"4703": "Chemical Wood Pulp"}
AGRI_EXPORT_PRODUCTS = {"1201": "Soybeans", "1005": "Corn", "0901": "Coffee", "1701": "Sugar"}
AGRI_FERTILIZER_IMPORT_PRODUCTS = {
    "3102": "Nitrogen Fertilizers (Urea)", "3103": "Phosphate Fertilizers",
    "3104": "Potash Fertilizers", "3105": "Mixed Fertilizers (DAP etc.)",
}
ALL_EXPORT_PREFIXES = {**MINING_EXPORT_PRODUCTS, **PULP_EXPORT_PRODUCTS, **AGRI_EXPORT_PRODUCTS}

USE_COLS = ["CO_ANO", "CO_MES", "CO_NCM", "SG_UF_NCM", "KG_LIQUIDO", "VL_FOB"]


def fetch_year(flow: str, year: int, prefixes: dict) -> pd.DataFrame:
    url = CSV_URL.format(flow=flow, year=year)
    r = get_with_retry(url, timeout=120)
    df = pd.read_csv(io.BytesIO(r.content), sep=";", dtype=str, usecols=USE_COLS)
    for c in df.columns:
        df[c] = df[c].astype(str).str.strip('"')
    prefix_tuple = tuple(prefixes)
    df = df[df["CO_NCM"].str.startswith(prefix_tuple)]
    df["KG_LIQUIDO"] = pd.to_numeric(df["KG_LIQUIDO"], errors="coerce")
    df["VL_FOB"] = pd.to_numeric(df["VL_FOB"], errors="coerce")
    return df.dropna(subset=["KG_LIQUIDO"])


def build_product_series(df: pd.DataFrame, products: dict) -> dict:
    """Builds both shapes the frontend needs from one filtered dataframe:
    - monthly[prefix]["YYYY-MM"] -> {kg, fob_usd}, for line charts
      (_pulpPoints()-compatible once flattened to a single metric client-side)
    - world[prefix][element][year] / countries[state][prefix][element][year],
      the exact shape _rankRows()/_renderRankTable() already expect
      (same pattern as agriculture_scraper.py's crops.world/crops.countries),
      so the state-ranking table needs zero new JS, just entityLabel='State'.
    """
    monthly, world, countries = {}, {}, {}
    for prefix, label in products.items():
        sub = df[df["CO_NCM"].str.startswith(prefix)]
        if sub.empty:
            continue

        g = sub.groupby(["CO_ANO", "CO_MES"]).agg(kg=("KG_LIQUIDO", "sum"), fob=("VL_FOB", "sum"))
        monthly[prefix] = {
            f"{y}-{str(m).zfill(2)}": {"kg": round(float(r.kg), 1), "fob_usd": round(float(r.fob), 1)}
            for (y, m), r in g.iterrows()
        }

        gy = sub.groupby("CO_ANO").agg(kg=("KG_LIQUIDO", "sum"), fob=("VL_FOB", "sum"))
        world[prefix] = {
            "kg": {y: round(float(v), 1) for y, v in gy["kg"].items()},
            "fob_usd": {y: round(float(v), 1) for y, v in gy["fob"].items()},
        }

        gs = sub.dropna(subset=["SG_UF_NCM"])
        gs = gs[gs["SG_UF_NCM"] != "nan"]
        gs = gs.groupby(["SG_UF_NCM", "CO_ANO"]).agg(kg=("KG_LIQUIDO", "sum"), fob=("VL_FOB", "sum"))
        for (state, year), r in gs.iterrows():
            entry = countries.setdefault(state, {}).setdefault(prefix, {})
            entry.setdefault("kg", {})[year] = round(float(r.kg), 1)
            entry.setdefault("fob_usd", {})[year] = round(float(r.fob), 1)

    return {"items": products, "monthly": monthly, "world": world, "countries": countries}


def main():
    this_year = date.today().year
    years = list(range(this_year - YEARS_BACK, this_year + 1))

    print(f"Fetching MDIC Comex Stat exports ({years[0]}-{years[-1]})...")
    exp_frames = []
    for y in years:
        print(f"  EXP_{y}.csv...")
        exp_frames.append(fetch_year("EXP", y, ALL_EXPORT_PREFIXES))
    exp_df = pd.concat(exp_frames, ignore_index=True)
    print(f"  {len(exp_df):,} rows matched")

    print(f"Fetching MDIC Comex Stat imports, fertilizer only ({years[0]}-{years[-1]})...")
    imp_frames = []
    for y in years:
        print(f"  IMP_{y}.csv...")
        imp_frames.append(fetch_year("IMP", y, AGRI_FERTILIZER_IMPORT_PRODUCTS))
    imp_df = pd.concat(imp_frames, ignore_index=True)
    print(f"  {len(imp_df):,} rows matched")

    mining = build_product_series(exp_df, MINING_EXPORT_PRODUCTS)
    pulp_paper = build_product_series(exp_df, PULP_EXPORT_PRODUCTS)
    agri_exports = build_product_series(exp_df, AGRI_EXPORT_PRODUCTS)
    fertilizer_imports = build_product_series(imp_df, AGRI_FERTILIZER_IMPORT_PRODUCTS)

    for label, section in [("Mining", mining), ("Pulp & Paper", pulp_paper),
                            ("Agriculture exports", agri_exports), ("Fertilizer imports", fertilizer_imports)]:
        print(f"  {label}: {len(section['items'])} products, {len(section['countries'])} states")

    db = {
        "unit": "kg (net weight) / USD FOB",
        "mining": mining,
        "pulp_paper": pulp_paper,
        "agriculture": {"exports": agri_exports, "fertilizer_imports": fertilizer_imports},
        "_meta": {
            "source": "MDIC - Ministério do Desenvolvimento, Indústria, Comércio e Serviços (Comex Stat / Balança Comercial)",
            "source_url": SOURCE_PAGE,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "years_covered": years,
        },
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)

    print(f"\n  -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

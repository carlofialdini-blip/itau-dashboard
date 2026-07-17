#!/usr/bin/env python3
"""
fuel_scraper.py
Fetches Brazil's official fuel-distribution sales data from ANP's open-data
portal (Agência Nacional do Petróleo, Gás Natural e Biocombustíveis) and
aggregates it into the {E,P,R,M,T,G,K} shape the Economic Data > Energy
sub-page's JS expects — the same structure distribuicao-combustiveis-completo
originally hand-built once and embedded as a static snapshot.

Source (live-verified before writing this, same standard as every other data
source in this project): liquidos.zip from ANP's "Movimentação de derivados
de petróleo e biocombustíveis" open-data page contains
Liquidos_Vendas_Atual.csv, a company-level (Agente Regulado), product,
region, and market-segment breakdown of monthly fuel sales, 2017-present.
Its Nome do Produto / Região Destinatário / Mercado Destinatário values
matched the ported dataset's DB.P / DB.R / DB.M arrays exactly, and its
company count (214) was within one of the ported snapshot's (215) — this is
the real upstream source the teammate's dashboard was built from, not a
lookalike.

Output: fuel_data_cache.json

Run:  python3 fuel_scraper.py
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

OUTPUT_FILE = ROOT / "data" / "fuel_data_cache.json"

ZIP_URL    = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/mdpg/liquidos.zip"
CSV_MEMBER = "Liquidos_Vendas_Atual.csv"
SOURCE_PAGE = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/dados-abertos-movimentacao-de-derivados-de-petroleo"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/122 Safari/537.36"
    )
}

USE_COLS = [
    "Ano", "Mês", "Agente Regulado", "Nome do Produto",
    "Região Destinatário", "Mercado Destinatário",
    "Quantidade de Produto (mil m³)",
]
COL_NAMES = ["ano", "mes", "empresa", "produto", "regiao", "mercado", "volume"]


def fetch_csv() -> pd.DataFrame:
    # ~20MB zip; ANP's server can be slow, give it real headroom beyond the
    # project's usual 15s default rather than raising get_with_retry's
    # global default (see core/net_utils.py — that default is tuned for much
    # smaller RSS/API responses).
    r = get_with_retry(ZIP_URL, headers=HEADERS, timeout=90)
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        with zf.open(CSV_MEMBER) as f:
            df = pd.read_csv(f, sep=";", decimal=",", encoding="latin-1",
                              usecols=USE_COLS)
    df.columns = COL_NAMES
    return df


def build_db(df: pd.DataFrame) -> dict:
    companies = sorted(df["empresa"].unique())
    products  = sorted(df["produto"].unique())
    regions   = sorted(df["regiao"].unique())
    mercados  = sorted(df["mercado"].unique())

    eid = {name: i for i, name in enumerate(companies)}
    pid = {name: i for i, name in enumerate(products)}
    rid = {name: i for i, name in enumerate(regions)}
    mid = {name: i for i, name in enumerate(mercados)}

    df = df.assign(
        eid=df["empresa"].map(eid),
        pid=df["produto"].map(pid),
        rid=df["regiao"].map(rid),
        mid=df["mercado"].map(mid),
        ano_s=df["ano"].astype(str),
        yyyymm=df["ano"].astype(str) + df["mes"].astype(int).astype(str).str.zfill(2),
    )

    # T: monthly totals per company+product, summed across region & market
    # segment — matches getTimeSeries()'s "eid,pid,yyyymm" key format.
    t = df.groupby(["eid", "pid", "yyyymm"], observed=True)["volume"].sum()
    T = {f"{a},{b},{c}": round(float(v), 6) for (a, b, c), v in t.items()}

    # G: annual totals per company+product+region — matches getRegiaoVol()'s
    # "eid,pid,ano,rid" key format.
    g = df.groupby(["eid", "pid", "ano_s", "rid"], observed=True)["volume"].sum()
    G = {f"{a},{b},{c},{d}": round(float(v), 6) for (a, b, c, d), v in g.items()}

    # K: annual totals per company+product+market segment — matches
    # getMercadoVol()'s "eid,pid,ano,mid" key format.
    k = df.groupby(["eid", "pid", "ano_s", "mid"], observed=True)["volume"].sum()
    K = {f"{a},{b},{c},{d}": round(float(v), 6) for (a, b, c, d), v in k.items()}

    return {"E": companies, "P": products, "R": regions, "M": mercados,
            "T": T, "G": G, "K": K}


def main():
    print("Fetching ANP fuel-distribution data (liquidos.zip)...")
    df = fetch_csv()
    print(f"  {len(df):,} rows parsed")

    db = build_db(df)
    db["_meta"] = {
        "source": "ANP - Agência Nacional do Petróleo, Gás Natural e Biocombustíveis (dados abertos)",
        "source_url": SOURCE_PAGE,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "companies": len(db["E"]),
        "rows": len(df),
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)

    print(f"  {len(db['E'])} companies, {len(db['T']):,} monthly series points -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
agriculture_scraper.py
Fetches Brazilian agriculture economics data from four official, free,
unauthenticated sources and aggregates them into the compact shape the
Economic Data > Agriculture sub-page's JS expects. No single bulk source
covers this domain the way ANP/FAOSTAT/BGS do for Energy/Pulp & Paper/
Mining, so this scraper is several sub-fetches, each live-verified before
being wired in (same standard as every other data source in this project):

- CONAB (portaldeinformacoes.conab.gov.br/downloads/arquivos/*.txt) — no
  API, no auth, direct semicolon-delimited flat files, latin-1 encoded
  (confirmed by curl, same quirk as fuel_scraper.py's ANP file). Covers
  supply-demand balance (production/exports/imports/consumption/stocks),
  prices (minimum-guaranteed + market, incl. fertilizer SKUs), freight,
  storage capacity, and production costs — ground no other free source
  covers this cleanly.
- IBGE SIDRA API (apisidra.ibge.gov.br, free, no auth, live-verified) —
  tables 1612/1613 (PAM: temporary/permanent crop area, production, yield,
  per crop, national + state) and 3939 (PPM: livestock herd, national +
  state). The official census-grade numbers, broader crop coverage than
  CONAB's own grain/cana/café scope. Annual, ~1-2yr publication lag, same
  as FAOSTAT/BGS elsewhere in this project (latest available: 2024).
- MAPA's Portal de Dados Abertos (dados.agricultura.gov.br, CKAN, free,
  no auth) — SISSER rural-insurance premium-subsidy dataset. Needs a
  browser User-Agent to avoid a 403 from this domain's WAF;
  get_with_retry()'s default headers already spoof one (added for Google
  News), so no special-casing needed here — the opposite of the FRED case
  documented in CLAUDE.md, where that same default UA caused a hang.
  IMPORTANT: raw SISSER rows are per-policy and carry the insured party's
  name and a partially-masked document number (NM_SEGURADO,
  NR_DOCUMENTO_SEGURADO). Those columns are never even read (excluded via
  usecols=), and everything is aggregated to state/crop/year sums before
  being written to the cache — no row-level or person-identifying data
  ever reaches dashboard.html.
- Banco Central do Brasil SGS (already integrated elsewhere in this
  project) — series 22027, "Saldo das operações de crédito por atividade
  econômica - Agropecuária," same BCB_BASE URL family
  core/generate_dashboard.py's fetch_brazil_charts()/fetch_credit_charts()
  already use. Real, monthly, current rural-credit balance.

Sources considered and rejected, with reasons: CEPEA/ESALQ (no free
bulk/API endpoint found — commercially licensed), ABPA/Abiove/CNA/ANDA
(PDF-report-only or membership-gated, confirmed by search — CONAB+IBGE
already cover the same ground with real free APIs), INMET (no bulk export
comparable to CONAB/FAOSTAT/BGS; BDMEP is a per-station form tool, not a
bulk file) — see CLAUDE.md §4 for the full writeup.

Output: agriculture_cache.json

Run:  python3 agriculture_scraper.py
"""

import io
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.net_utils import get_with_retry  # noqa: E402

OUTPUT_FILE = ROOT / "data" / "agriculture_cache.json"

# ── CONAB — Portal de Informações Agropecuárias ─────────────────────────────
CONAB_BASE = "https://portaldeinformacoes.conab.gov.br/downloads/arquivos/{}.txt"
CONAB_SOURCE_PAGE = "https://portaldeinformacoes.conab.gov.br/download-arquivos.html"


def fetch_conab(name: str) -> pd.DataFrame:
    url = CONAB_BASE.format(name)
    r = get_with_retry(url, timeout=60)
    df = pd.read_csv(io.BytesIO(r.content), sep=";", encoding="latin-1", low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    return df


def _strip_obj_cols(df: pd.DataFrame) -> pd.DataFrame:
    for c in df.select_dtypes(include="object").columns:
        df[c] = df[c].str.strip()
    return df


def _to_float_br(s: pd.Series) -> pd.Series:
    """CONAB numeric columns sometimes use comma as decimal separator."""
    return pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False), errors="coerce")


def _safra_start_year(s: pd.Series) -> pd.Series:
    """'1999/00' -> 1999, '2024' -> 2024. Non-numeric leading text -> NaN."""
    return s.astype(str).str.extract(r"(\d{4})")[0].astype(float)


def build_trade() -> dict:
    """CONAB's OfertaDemanda.txt: national supply-demand balance per
    product/harvest-year — the only source in this scraper with exports,
    imports, and domestic consumption all in one place."""
    df = fetch_conab("OfertaDemanda")
    df = _strip_obj_cols(df)
    df["year"] = _safra_start_year(df["dsc_safra"]).astype("Int64")
    for c in ["estoque_inicial_1000t", "producao_1000t", "importacao_1000t",
              "consumo_1000t", "exportacao_1000t", "estoque_final_1000t"]:
        df[c] = _to_float_br(df[c])
    df = df.dropna(subset=["year"])

    items, world = {}, {}
    for produto, g in df.groupby("produto"):
        key = str(g["id_produto"].iloc[0])
        items[key] = produto
        g = g.sort_values("year")
        world[key] = {
            "stock_start":  {str(int(r.year)): r.estoque_inicial_1000t for r in g.itertuples() if pd.notna(r.estoque_inicial_1000t)},
            "production":   {str(int(r.year)): r.producao_1000t        for r in g.itertuples() if pd.notna(r.producao_1000t)},
            "import_qty":   {str(int(r.year)): r.importacao_1000t      for r in g.itertuples() if pd.notna(r.importacao_1000t)},
            "consumption":  {str(int(r.year)): r.consumo_1000t         for r in g.itertuples() if pd.notna(r.consumo_1000t)},
            "export_qty":   {str(int(r.year)): r.exportacao_1000t      for r in g.itertuples() if pd.notna(r.exportacao_1000t)},
            "stock_end":    {str(int(r.year)): r.estoque_final_1000t   for r in g.itertuples() if pd.notna(r.estoque_final_1000t)},
        }
    return {"unit": "1,000 tonnes", "items": items, "world": world}


def build_prices() -> dict:
    """CONAB's minimum-guaranteed price policy + monthly market prices
    (state-level, producer-paid) — the product list includes fertilizer
    formulations (e.g. '00-18-18'), used as the input-price proxy since no
    free ANDA-equivalent API exists."""
    pm = fetch_conab("PrecoMinimo")
    pm = _strip_obj_cols(pm)
    pm["preco"] = _to_float_br(pm["preco"])
    pm = pm.dropna(subset=["preco", "ano_inicio_vigencia"])
    pm = pm[pm["descricao_produto_preco_minimo"].astype(bool)]
    minimum = {}
    for produto, g in pm.groupby("descricao_produto_preco_minimo"):
        by_year = g.groupby("ano_inicio_vigencia")["preco"].mean()
        minimum[produto] = {str(int(y)): round(v, 4) for y, v in by_year.items()}

    mu = fetch_conab("PrecosMensalUF")
    mu = _strip_obj_cols(mu)
    mu["valor_produto_kg"] = _to_float_br(mu["valor_produto_kg"])
    mu = mu.dropna(subset=["valor_produto_kg", "ano"])
    mu = mu[mu["dsc_nivel_comercializacao"].str.contains("PRODUTOR", case=False, na=False)]
    mu = mu[mu["produto"].astype(bool)]
    market = {}
    for produto, g in mu.groupby("produto"):
        by_uf = {}
        for uf, g2 in g.groupby("uf"):
            by_year = g2.groupby("ano")["valor_produto_kg"].mean()
            by_uf[uf] = {str(int(y)): round(v, 4) for y, v in by_year.items()}
        market[produto] = by_uf

    return {
        "minimum_price_unit": "R$ per commercial unit (varies by product)",
        "market_price_unit": "R$ per kg, producer-paid",
        "minimum": minimum,
        "market": market,
    }


def build_logistics() -> dict:
    """CONAB's route-level freight survey — aggregated to yearly national
    averages (no volume/weight field exists to build a meaningful
    route-level ranking from)."""
    df = fetch_conab("Frete")
    df = _strip_obj_cols(df)
    for c in ["distancia_km", "valor_frete_tonelada", "valor_tonelada_km"]:
        df[c] = _to_float_br(df[c])
    df = df.dropna(subset=["ano"])
    by_year = df.groupby("ano").agg(
        avg_freight_per_tonne=("valor_frete_tonelada", "mean"),
        avg_freight_per_tonne_km=("valor_tonelada_km", "mean"),
        avg_distance_km=("distancia_km", "mean"),
    )
    return {
        "unit": "R$ per tonne / R$ per tonne-km",
        "by_year": {
            str(int(y)): {
                "freight_per_tonne": round(r.avg_freight_per_tonne, 2) if pd.notna(r.avg_freight_per_tonne) else None,
                "freight_per_tonne_km": round(r.avg_freight_per_tonne_km, 4) if pd.notna(r.avg_freight_per_tonne_km) else None,
                "distance_km": round(r.avg_distance_km, 1) if pd.notna(r.avg_distance_km) else None,
            }
            for y, r in by_year.iterrows()
        },
    }


def build_storage() -> dict:
    """CONAB's registered-warehouse catalog — aggregated to state totals,
    not kept warehouse-by-warehouse (8MB raw, and individual capacity isn't
    analytically useful at dashboard scale)."""
    df = fetch_conab("ArmazensCadastrados")
    df = _strip_obj_cols(df)
    cap_col = "qtd_capacidade_estatica(t)"
    df[cap_col] = _to_float_br(df[cap_col])
    df = df.dropna(subset=[cap_col, "uf"])
    by_state = df.groupby("uf").agg(
        capacity_t=(cap_col, "sum"),
        warehouse_count=(cap_col, "count"),
    )
    return {
        "unit": "tonnes (static capacity)",
        "national_total_t": round(float(df[cap_col].sum()), 0),
        "national_warehouse_count": int(len(df)),
        "by_state": {
            uf: {"capacity_t": round(r.capacity_t, 0), "warehouse_count": int(r.warehouse_count)}
            for uf, r in by_state.iterrows()
        },
    }


def build_costs() -> dict:
    """CONAB's production-cost survey — variable + fixed cost per hectare,
    averaged by product/year (mixes farm sizes/regions/technology levels,
    same limitation the raw survey itself has; a simple yearly average is
    the honest, non-oversold summary of it)."""
    df = fetch_conab("CustoProducao")
    df = _strip_obj_cols(df)
    df["total_cost_ha"] = _to_float_br(df["vlr_custo_variavel_ha"]) + _to_float_br(df["vlr_custo_fixo_ha"])
    df = df.dropna(subset=["total_cost_ha", "ano"])
    by_product = {}
    for produto, g in df.groupby("produto"):
        by_year = g.groupby("ano")["total_cost_ha"].mean()
        by_product[produto] = {str(int(y)): round(v, 2) for y, v in by_year.items()}
    return {"unit": "R$ per hectare (variable + fixed cost)", "by_product": by_product}


# ── IBGE SIDRA — PAM (crops) + PPM (livestock) ──────────────────────────────
SIDRA_VALUES_URL = "https://apisidra.ibge.gov.br/values/t/{table}/n{n}/all/v/{v}/p/{p}/c{c}/{codes}"
SIDRA_SOURCE_PAGE = "https://sidra.ibge.gov.br/"

# Top temporary crops by 2024 production value (table 1612, dimension c81) —
# live-checked against the real product list rather than assumed; permanent
# crops (table 1613, dimension c82) are a separate table in SIDRA's own
# scheme. Kept to the major, analytically-meaningful products rather than
# every ~60 code IBGE tracks — same "meaningful representation, not
# exhaustive" pruning this project already applies (FAOSTAT country list,
# BGS commodity list).
TEMP_CROPS = {
    "2696": "Sugarcane", "2713": "Soybean", "2711": "Corn", "2708": "Cassava",
    "2692": "Rice", "2689": "Cotton (seed)", "2716": "Wheat", "2714": "Sorghum",
    "2702": "Beans", "2691": "Peanuts",
}
PERM_CROPS = {
    "2733": "Orange", "2720": "Banana", "2723": "Coffee (total)",
    "2728": "Palm oil (Dendê)", "2748": "Grapes", "2737": "Mango",
}
# Temporary crops (table 1612) report "Área plantada" as var 109; permanent
# crops (table 1613) don't have a planted-area concept the same way (trees
# aren't replanted every harvest) and instead report "Área destinada à
# colheita" (var 2313) as the closest equivalent — confirmed live by
# querying table 1613 with v=all, which does not include 109 at all.
TEMP_CROP_VARS = {"109": "area_planted", "216": "area_harvested", "214": "production", "112": "yield"}
PERM_CROP_VARS = {"2313": "area_planted", "216": "area_harvested", "214": "production", "112": "yield"}

LIVESTOCK = {
    "2670": "Cattle", "32794": "Pigs (total)", "32796": "Poultry (total)",
    "2677": "Sheep", "2681": "Goats", "2672": "Horses", "2675": "Buffalo",
}
LIVESTOCK_VARS = {"105": "herd"}

STATE_YEARS_BACK = 15  # bound state-level payload size; national goes back further


def _sidra_fetch(table: str, n: int, v: str, p: str, c: int, codes: str) -> list:
    url = SIDRA_VALUES_URL.format(table=table, n=n, v=v, p=p, c=c, codes=codes)
    r = get_with_retry(url, timeout=45)
    data = r.json()
    return data[1:] if data else []


def _sidra_to_nested(rows: list, var_map: dict, product_map: dict, area_key="D1N") -> dict:
    """area_key -> product_code(str) -> metric -> {year: value}."""
    out = {}
    for row in rows:
        code = row.get("D4C")
        if code not in product_map:
            continue
        metric = var_map.get(row["D2C"])
        if metric is None:
            continue
        try:
            v = float(row["V"])
        except (TypeError, ValueError):
            continue
        area = row[area_key]
        year = row["D3C"]
        out.setdefault(area, {}).setdefault(code, {}).setdefault(metric, {})[year] = v
    return out


def build_crops() -> dict:
    this_year = date.today().year
    state_start = this_year - STATE_YEARS_BACK

    temp_codes = ",".join(TEMP_CROPS)
    perm_codes = ",".join(PERM_CROPS)
    temp_var_codes = ",".join(TEMP_CROP_VARS)
    perm_var_codes = ",".join(PERM_CROP_VARS)

    n1_temp = _sidra_fetch("1612", 1, temp_var_codes, "all", 81, temp_codes)
    n1_perm = _sidra_fetch("1613", 1, perm_var_codes, "all", 82, perm_codes)
    time.sleep(0.5)
    n3_temp = _sidra_fetch("1612", 3, temp_var_codes, f"{state_start}-{this_year}", 81, temp_codes)
    n3_perm = _sidra_fetch("1613", 3, perm_var_codes, f"{state_start}-{this_year}", 82, perm_codes)

    all_items = {**TEMP_CROPS, **PERM_CROPS}
    # NOTE: dict.update() on the *outer* {"Brasil": {...}} dicts would
    # replace the whole "Brasil" value rather than merge the inner
    # product dicts (both sides share that one top-level key) — caught by
    # a sanity check (national soy production came back None despite
    # state-level data being correct) before this shipped. Merge the
    # inner dicts explicitly instead.
    world_temp = _sidra_to_nested(n1_temp, TEMP_CROP_VARS, TEMP_CROPS).get("Brasil", {})
    world_perm = _sidra_to_nested(n1_perm, PERM_CROP_VARS, PERM_CROPS).get("Brasil", {})
    world = {**world_temp, **world_perm}

    states = _sidra_to_nested(n3_temp, TEMP_CROP_VARS, TEMP_CROPS)
    perm_states = _sidra_to_nested(n3_perm, PERM_CROP_VARS, PERM_CROPS)
    for state, entry in perm_states.items():
        states.setdefault(state, {}).update(entry)

    result = {"unit": "hectares / tonnes / kg per hectare", "items": all_items,
              "world": world, "countries": states}
    _splice_conab_current_season(result)
    return result


# CONAB product name -> the same SIDRA product code used above, so the
# splice below extends the exact same series IBGE anchors rather than
# creating a parallel one. Cassava (Mandioca, 2708) has no CONAB
# Levantamento equivalent — stays IBGE-only (2024), documented honestly
# rather than guessed at.
CONAB_LEVANTAMENTO_GRAOS_TO_SIDRA = {
    "SOJA": "2713", "MILHO": "2711", "ALGODAO EM CAROCO": "2689",
    "ARROZ": "2692", "TRIGO": "2716", "FEIJAO": "2702",
}
CONAB_LEVANTAMENTO_CANA_CODE = "2696"
CONAB_LEVANTAMENTO_CAFE_CODE = "2723"

# CONAB's Levantamento files key states by 2-letter UF (e.g. "MT"), while
# IBGE SIDRA's n3 query above keys them by full name (e.g. "Mato Grosso") —
# without this mapping, merging the two would silently create ~25 duplicate
# state entries (confirmed live: crops['countries'] grew from 27 to 52
# keys on the first run, exactly double, before this was caught). CONAB
# also has a small "NI" ("Não Informado") catch-all bucket with no real
# state — dropped, not mapped to anything.
UF_TO_STATE_NAME = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul",
    "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}


def _splice_conab_current_season(crops: dict) -> None:
    """IBGE's PAM/PPM (used above) is the official census, but it's
    finalized roughly 1-2 years after a season ends — the same lag as
    FAOSTAT/BGS elsewhere in this project. CONAB separately runs its own
    *monthly, in-season* crop-monitoring bulletins (LevantamentoGraos/Cana/
    Cafe.txt, live-verified: LevantamentoGraos already covers the 2025/26
    season through its 9th monthly bulletin; LevantamentoCana reaches
    2026/27). This splices each product's *latest* bulletin per season (id_
    levantamento is a running bulletin number within a season — earlier
    ones are superseded estimates, so only the max per season/state is
    kept) onto the same crops['world'][code]['production'/'area_planted']
    and crops['countries'][state][code][...] dicts IBGE already populated —
    extending the series with CONAB's current-season estimate rather than
    replacing IBGE's more rigorous finalized figures for past years. Units
    converted from CONAB's mil_t/mil_ha to the same raw tonnes/hectares
    IBGE uses so the two halves of one series are comparable.

    IMPORTANT: IBGE's "year" is a calendar year; CONAB's safra ("2024/25")
    is labeled by its *start* year, which collides with IBGE's plain
    "2024" even though the two describe different, non-equivalent
    production cycles. Naively writing CONAB's value under that shared key
    silently overwrote IBGE's real calendar-2024 figure with CONAB's
    2024/25-safra estimate in an early version of this function — caught
    live (soy's world total shifted from a known-correct 144.5Mt to
    171.5Mt after splicing, i.e. finalized data got corrupted, not just
    extended). Fixed by only ever writing years strictly beyond whatever
    IBGE's own most recent year already is for that code — CONAB can add
    new years, never touch existing ones."""
    def _merge(df: pd.DataFrame, code_map: dict, has_area: bool = True) -> None:
        df = _strip_obj_cols(df)
        df["uf"] = df["uf"].map(UF_TO_STATE_NAME)
        df = df.dropna(subset=["uf"])
        df["year"] = _safra_start_year(df["ano_agricola"])
        df["id_levantamento"] = pd.to_numeric(df["id_levantamento"], errors="coerce")
        df = df.dropna(subset=["year", "id_levantamento"])
        # Must group by product too, not just season+state — grouping by
        # just (ano_agricola, uf) picks one row per state per season
        # across *all* products mixed together (whichever happens to sort
        # last), silently dropping every other product for that state/
        # season. Caught live: soy's 2025/26 rows existed pre-groupby (243
        # rows) but vanished post-groupby (0), while cana/café — which
        # only ever have one product per file — were unaffected, which is
        # what made this file-specific rather than a global bug.
        df = df.sort_values("id_levantamento").groupby(["ano_agricola", "uf", "produto"]).tail(1)
        df["producao_mil_t"] = _to_float_br(df["producao_mil_t"]) * 1000
        if has_area:
            df["area_plantada_mil_ha"] = _to_float_br(df["area_plantada_mil_ha"]) * 1000

        for produto, code in code_map.items():
            g = df[df["produto"] == produto]
            if g.empty or code not in crops["world"]:
                continue
            ibge_years = [int(y) for y in crops["world"][code].get("production", {})]
            ibge_max_year = max(ibge_years) if ibge_years else -1
            for metric, col in (("production", "producao_mil_t"),
                                 ("area_planted", "area_plantada_mil_ha") if has_area else (None, None)):
                if metric is None:
                    continue
                by_year = g.groupby("year")[col].sum()
                new_years = {str(int(y)): round(v, 1) for y, v in by_year.items() if int(y) > ibge_max_year}
                crops["world"][code].setdefault(metric, {}).update(new_years)
                for state, g2 in g.groupby("uf"):
                    entry = crops["countries"].setdefault(state, {}).setdefault(code, {})
                    by_year_s = g2.groupby("year")[col].sum()
                    new_years_s = {str(int(y)): round(v, 1) for y, v in by_year_s.items() if int(y) > ibge_max_year}
                    entry.setdefault(metric, {}).update(new_years_s)

    _merge(fetch_conab("LevantamentoGraos"), CONAB_LEVANTAMENTO_GRAOS_TO_SIDRA)
    _merge(fetch_conab("LevantamentoCana"), {"CANA DE ACUCAR": CONAB_LEVANTAMENTO_CANA_CODE}, has_area=True)
    # Coffee deliberately excluded: CONAB's own SerieHistoricaCafe.txt
    # (its finalized series, not just the in-season Levantamento) reports
    # 2024 production at ~54.2M t vs IBGE's 3.39M t for the same year — a
    # ~16x gap, consistent across both CONAB files, so not a parsing bug,
    # but also not a cleanly-explained unit difference (coffee cherry-to-
    # green-bean ratios run ~5-6x, not 16x) worth trusting without more
    # verification than time allowed for. Coffee stays IBGE-only (2024)
    # rather than risk splicing in a figure that may be on a different,
    # unreconciled basis (e.g. a different processing stage, or arabica+
    # conilon summed under a shared "CAFE" label some other way).


def build_livestock() -> dict:
    this_year = date.today().year
    state_start = this_year - STATE_YEARS_BACK
    codes = ",".join(LIVESTOCK)

    n1 = _sidra_fetch("3939", 1, "105", "all", 79, codes)
    time.sleep(0.5)
    n3 = _sidra_fetch("3939", 3, "105", f"{state_start}-{this_year}", 79, codes)

    world = _sidra_to_nested(n1, LIVESTOCK_VARS, LIVESTOCK).get("Brasil", {})
    states = _sidra_to_nested(n3, LIVESTOCK_VARS, LIVESTOCK)
    return {"unit": "head", "items": LIVESTOCK, "world": world, "countries": states}


# ── MAPA — SISSER rural insurance ───────────────────────────────────────────
# 2006-2015 file deliberately not fetched — 2016-2025 already gives a full
# decade of current-program history, and adding the older file would
# roughly double this step's runtime/bandwidth for years outside the
# program's more relevant recent design (post-2016 rule changes). Same
# "explain the gap honestly, don't pad it" standard as FAOSTAT's
# PAPER_MIX_FIRST_YEAR.
SISSER_FILES = [
    "https://dados.agricultura.gov.br/dataset/baefdc68-9bad-4204-83e8-f2888b79ab48/resource/54e04a6b-15b3-4bda-a330-b8e805deabe4/download/dados_abertos_psr_2016a2024csv.csv",
    "https://dados.agricultura.gov.br/dataset/baefdc68-9bad-4204-83e8-f2888b79ab48/resource/ac7e4351-974f-4958-9294-627c5cbf289a/download/dados_abertos_psr_2025csv.csv",
]
SISSER_SOURCE_PAGE = "https://dados.agricultura.gov.br/dataset/sisser3"

# Only these columns are ever read from SISSER — NM_SEGURADO,
# NR_DOCUMENTO_SEGURADO, and every other person/policy-identifying field
# are excluded at the pandas usecols= level, not merely dropped after
# loading. See module docstring.
SISSER_COLS = ["SG_UF_PROPRIEDADE", "NM_CULTURA_GLOBAL", "ANO_APOLICE",
               "VL_LIMITE_GARANTIA", "VL_PREMIO_LIQUIDO", "VL_SUBVENCAO_FEDERAL",
               "VALOR_INDENIZAÇÃO"]


def build_insurance() -> dict:
    frames = []
    for url in SISSER_FILES:
        r = get_with_retry(url, timeout=90)
        df = pd.read_csv(io.BytesIO(r.content), sep=";", encoding="latin-1",
                          usecols=SISSER_COLS, low_memory=False)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)

    for c in ["VL_LIMITE_GARANTIA", "VL_PREMIO_LIQUIDO", "VL_SUBVENCAO_FEDERAL", "VALOR_INDENIZAÇÃO"]:
        df[c] = _to_float_br(df[c])
    df = df.dropna(subset=["ANO_APOLICE"])
    df["ANO_APOLICE"] = df["ANO_APOLICE"].astype(int)

    def _agg(group_cols):
        g = df.groupby(group_cols).agg(
            policies=("VL_PREMIO_LIQUIDO", "count"),
            insured_value=("VL_LIMITE_GARANTIA", "sum"),
            premium=("VL_PREMIO_LIQUIDO", "sum"),
            federal_subsidy=("VL_SUBVENCAO_FEDERAL", "sum"),
            indemnization=("VALOR_INDENIZAÇÃO", "sum"),
        )
        return g

    by_state = {}
    for (uf, year), r in _agg(["SG_UF_PROPRIEDADE", "ANO_APOLICE"]).iterrows():
        by_state.setdefault(uf, {})[str(year)] = {
            "policies": int(r.policies), "insured_value": round(r.insured_value, 0),
            "premium": round(r.premium, 0), "federal_subsidy": round(r.federal_subsidy, 0),
            "indemnization": round(r.indemnization, 0),
        }

    by_crop = {}
    for (crop, year), r in _agg(["NM_CULTURA_GLOBAL", "ANO_APOLICE"]).iterrows():
        crop = (crop or "").strip()
        if not crop:
            continue
        by_crop.setdefault(crop, {})[str(year)] = {
            "policies": int(r.policies), "insured_value": round(r.insured_value, 0),
            "premium": round(r.premium, 0), "federal_subsidy": round(r.federal_subsidy, 0),
            "indemnization": round(r.indemnization, 0),
        }

    return {
        "unit": "R$",
        "note": "Aggregated to state/crop/year sums only — no policy-level or "
                "insured-party data is retained.",
        "by_state": by_state,
        "by_crop": by_crop,
    }


# ── Banco Central do Brasil SGS — rural credit ──────────────────────────────
# Same endpoint family core/generate_dashboard.py's fetch_brazil_charts()/
# fetch_credit_charts() already use (BCB_BASE there) — duplicated here
# rather than imported, matching this project's "each scraper is
# self-contained" convention (CLAUDE.md §3).
BCB_SERIES_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series}/dados?dataInicial={start}&dataFinal={end}&formato=json"
RURAL_CREDIT_SERIES = 22027  # "Saldo das operações de crédito por atividade econômica - Agropecuária"


def build_credit() -> dict:
    today = date.today()
    start = today.replace(year=today.year - 10)
    url = BCB_SERIES_URL.format(series=RURAL_CREDIT_SERIES,
                                 start=start.strftime("%d/%m/%Y"),
                                 end=today.strftime("%d/%m/%Y"))
    r = get_with_retry(url, timeout=30)
    raw = r.json()
    points = {}
    for pt in raw:
        try:
            d = pt["data"].split("/")            # DD/MM/YYYY
            date_iso = f"{d[2]}-{d[1]}-{d[0]}"
            points[date_iso] = float(pt["valor"].replace(",", "."))
        except (KeyError, ValueError, IndexError):
            continue
    return {
        "unit": "R$ million",
        "series_id": RURAL_CREDIT_SERIES,
        "title": "Saldo das operações de crédito por atividade econômica — Agropecuária",
        "data": points,
    }


def main():
    print("Fetching Brazilian agriculture economics data...")

    print("  CONAB — supply-demand balance (OfertaDemanda)...")
    trade = build_trade()
    print(f"    {len(trade['items'])} products")

    print("  CONAB — prices (minimum + monthly market)...")
    prices = build_prices()
    print(f"    {len(prices['minimum'])} minimum-price products, {len(prices['market'])} market-price products")

    print("  CONAB — freight (logistics)...")
    logistics = build_logistics()

    print("  CONAB — storage capacity...")
    storage = build_storage()
    print(f"    {storage['national_warehouse_count']} warehouses, {storage['national_total_t']:,.0f} t")

    print("  CONAB — production costs...")
    costs = build_costs()
    print(f"    {len(costs['by_product'])} products")

    print("  IBGE SIDRA — crops (PAM)...")
    crops = build_crops()
    print(f"    {len(crops['items'])} crops, {len(crops['countries'])} states")

    print("  IBGE SIDRA — livestock (PPM)...")
    livestock = build_livestock()
    print(f"    {len(livestock['items'])} herd types, {len(livestock['countries'])} states")

    print("  MAPA — rural insurance (SISSER)...")
    insurance = build_insurance()
    print(f"    {len(insurance['by_state'])} states, {len(insurance['by_crop'])} crops")

    print("  BCB — rural credit (SGS 22027)...")
    credit = build_credit()
    print(f"    {len(credit['data'])} monthly points")

    db = {
        "crops": crops,
        "livestock": livestock,
        "trade": trade,
        "prices": prices,
        "logistics": logistics,
        "storage": storage,
        "costs": costs,
        "credit": credit,
        "insurance": insurance,
        "_meta": {
            "sources": {
                "conab": CONAB_SOURCE_PAGE,
                "ibge_sidra": SIDRA_SOURCE_PAGE,
                "mapa_sisser": SISSER_SOURCE_PAGE,
                "bcb": "https://www3.bcb.gov.br/sgspub",
            },
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)

    print(f"\n  -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

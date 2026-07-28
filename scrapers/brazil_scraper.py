#!/usr/bin/env python3
"""
brazil_scraper.py
Fetches Brazil macro/sector news from Google News RSS (pt-BR), organised by sector.
Output: brazil_news_cache.json

Run:  python3 brazil_scraper.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import feedparser

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.net_utils import get_many, DEFAULT_HEADERS  # noqa: E402
from core.scoring import importance_bucket  # noqa: E402
from core.trusted_sources import is_trusted  # noqa: E402

OUTPUT_FILE  = ROOT / "data" / "brazil_news_cache.json"
MAX_KEEP     = 50
MIN_SCORE    = 2
MIN_KEEP     = 30   # floor per sector — backfilled by score if the MIN_SCORE gate leaves fewer than this

BRASILIA_TZ = ZoneInfo("America/Sao_Paulo")

HEADERS = DEFAULT_HEADERS

# ── Sector definitions ───────────────────────────────────────────────────────
# Keyword lists are deliberately technical/specific (analyst-level terms,
# tickers, regulators, indices) rather than generic sector words.
#
# Each keyword is queried SEPARATELY (not OR'd into one mega-query). Google
# News RSS ranks a query's results by relevance, not recency, and caps each
# query at ~100 entries — a broad OR query "spends" that budget on whichever
# single sub-topic is most relevant overall, which can crowd out today's
# articles on the query's other terms entirely. Confirmed by direct testing:
# a 19-term OR query for Economy returned zero today-dated articles out of
# 100 results; the same terms queried individually surfaced several. Querying
# per-term costs more requests but is what actually finds today's news across
# a sector's different sub-topics instead of just its single dominant one.
BRAZIL_SECTORS: dict[str, dict] = {
    "Economy": {
        "keywords": [
            "IPCA", "Banco Central", "política monetária",
            "reforma tributária", "PIB", "dívida pública", "Focus",
            "risco-país", "Selic", "meta de inflação",
        ],
        "description": "Macro economy, monetary policy, fiscal accounts, inflation",
        "color": "#16a34a",
    },
    "Energy": {
        "keywords": [
            "ANEEL", "ANP", "Petrobras", "diesel", "hidrelétrica", "GNL",
            "ONS", "pré-sal", "energia eólica", "leilão de petróleo",
        ],
        "description": "Oil, gas, Petrobras, energy sector",
        "color": "#f97316",
    },
    "Mining & Steel": {
        "keywords": [
            "Vale", "minério de ferro", "ANM", "IBRAM", "Gerdau", "cobre",
            "CSN", "siderurgia", "níquel",
        ],
        "description": "Mining, iron ore, steel industry",
        "color": "#f59e0b",
    },
    "Pulp & Paper": {
        "keywords": [
            "Suzano", "Eldorado", "Klabin", "Bracell",
            "celulose de mercado", "fibra curta", "fibra longa", "BHKP",
        ],
        "description": "Pulp, paper, cellulose sector",
        "color": "#10b981",
    },
    "Agriculture": {
        "keywords": [
            "porto de Santos", "fertilizantes", "safra", "frigorífico",
            "Conab", "farelo de soja", "febre aftosa", "Minerva", "JBS",
            "soja em grão",
        ],
        "description": "Agribusiness, commodities, exports",
        "color": "#84cc16",
    },
    "Financial": {
        "keywords": [
            "Santander", "Banco do Brasil", "BTG Pactual", "Pix",
            "Bradesco", "Nubank", "fintech", "Stone", "Itaú", "IPO",
        ],
        "description": "Banking sector, credit, interest rates",
        "color": "#2563eb",
    },
    "Industry": {
        "keywords": [
            "CNI", "Embraer", "WEG", "Zona Franca de Manaus",
            "produção industrial", "PMI industrial",
            "capacidade instalada",
        ],
        "description": "Manufacturing, industrial output, PMI",
        "color": "#7c3aed",
    },
    "Trade & FX": {
        "keywords": [
            "tarifaço", "Mercosul", "MDIC", "acordo comercial",
            "balança comercial", "dólar comercial", "superávit comercial",
        ],
        "description": "Trade balance, foreign exchange, exports/imports",
        "color": "#0891b2",
    },
}

BRAZIL_ECO_TERMS = {
    "economia", "pib", "selic", "ipca", "inflação", "dólar", "câmbio",
    "balança", "exportação", "importação", "investimento", "fiscal",
    "resultado", "produção", "crescimento", "recessão", "juros",
    "mercado", "bolsa", "ações", "lucro", "receita", "ebitda",
    "dividendo", "guidance", "trimestre", "anual", "desemprego",
    "billion", "million", "bilhão", "milhão", "percent", "%",
}


def relevance_score(title: str, sector_keywords: list[str]) -> int:
    """Source trust is a hard pre-filter (see is_trusted()), not scored here —
    every candidate reaching this function already passed that gate."""
    title_lower  = title.lower()
    score = 0

    if "brasil" in title_lower or "brazil" in title_lower or "brasileiro" in title_lower:
        score += 2

    kw_hits = sum(1 for kw in sector_keywords if kw.lower() in title_lower)
    score += min(kw_hits, 3)

    eco_hits = sum(1 for t in BRAZIL_ECO_TERMS if t in title_lower)
    score += min(eco_hits, 2)

    if len(title) < 30:
        score -= 2

    return score


def google_news_url(term: str) -> str:
    query = f'Brasil "{term}"' if " " in term else f"Brasil {term}"
    return (
        f"https://news.google.com/rss/search?"
        f"q={quote(query)}"
        f"&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    )


def is_today_brasilia(published: str, today_brasilia) -> bool:
    """True if published falls on today's Brasília calendar date. Google
    News gives GMT timestamps; convert before comparing dates, otherwise
    articles from the last few hours of the Brasília day would be wrongly
    read as "tomorrow" in UTC, and early-UTC-morning articles (still
    yesterday in Brasília) would be wrongly kept."""
    try:
        return parsedate_to_datetime(published).astimezone(BRASILIA_TZ).date() == today_brasilia
    except Exception:
        return False


def merge_articles(existing: list[dict], new_articles: list[dict], today_brasilia) -> list[dict]:
    """Same-day only: carry over still-today existing articles not already
    in this run's results, then sort strictly by recency and cap. No day
    bucketing needed — every kept article is from the same calendar day."""
    new_links = {a["link"] for a in new_articles}
    fresh_existing = [
        a for a in existing
        if a["link"] not in new_links and is_today_brasilia(a.get("published", ""), today_brasilia)
    ]
    combined = new_articles + fresh_existing

    def _sort_key(a):
        try:
            return parsedate_to_datetime(a["published"])
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    combined.sort(key=_sort_key, reverse=True)
    return combined[:MAX_KEEP]



def fetch_sector(sector_name: str, config: dict) -> list[dict]:
    keywords       = config["keywords"]
    today_brasilia = datetime.now(BRASILIA_TZ).date()

    candidates  = []
    seen_titles = set()
    seen_links  = set()

    # This sector's terms are fetched concurrently — they are independent
    # requests and this scraper issues ~139 of them across all sectors, which
    # was the single biggest cost in the whole news pipeline (~3min). Parsing
    # below stays sequential and in input order, so dedup/scoring behaviour is
    # byte-identical to the sequential version.
    fetched = get_many([google_news_url(t) for t in keywords],
                       headers=HEADERS, timeout=15, retries=1)
    ok = sum(1 for _u, r, e in fetched if e is None)
    print(f"      {ok}/{len(keywords)} term queries returned", flush=True)

    for ti, (term, (_u, response, exc)) in enumerate(zip(keywords, fetched), 1):
        if exc is not None:
            print(f"      [{ti}/{len(keywords)}] {term}: ERROR — {exc}", flush=True)
            continue
        feed = feedparser.parse(response.content)

        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link  = entry.get("link", "")
            if not title or title in seen_titles or (link and link in seen_links):
                continue

            published = entry.get("published", "")
            try:
                pub_dt = parsedate_to_datetime(published)
            except Exception:
                continue

            if pub_dt.astimezone(BRASILIA_TZ).date() != today_brasilia:
                continue  # hard gate: today (Brasília) only, no exceptions

            source_info = entry.get("source", {})
            if not is_trusted(source_info.get("href", "")):
                continue  # hard gate: only whitelisted domains

            seen_titles.add(title)
            if link:
                seen_links.add(link)

            source = source_info.get("title", "Unknown")
            score  = relevance_score(title, keywords)

            candidates.append({
                "title":     title,
                "link":      link,
                "published": published,
                "source":    source,
                "_score":    score,
                "_pub_dt":   pub_dt,
            })

    qualified = [a for a in candidates if a["_score"] >= MIN_SCORE]

    # Floor: if today's genuinely-relevant articles don't reach MIN_KEEP,
    # backfill with the next-best-scored candidates from today (never from
    # another day) rather than leaving the sector thin.
    if len(qualified) < MIN_KEEP:
        qualified_links = {a["link"] for a in qualified}
        remaining = [a for a in candidates if a["link"] not in qualified_links]
        remaining.sort(key=lambda x: x["_score"], reverse=True)
        qualified += remaining[:MIN_KEEP - len(qualified)]

    qualified.sort(key=lambda x: x["_pub_dt"], reverse=True)
    kept = qualified[:MAX_KEEP]

    for a in kept:
        a["importance_score"] = a.pop("_score")
        a["importance"]       = importance_bucket(a["importance_score"])
        del a["_pub_dt"]

    return kept


def main():
    import os
    today_brasilia = datetime.now(BRASILIA_TZ).date()

    existing_cache: dict = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_cache = json.load(f)

    print()
    print("=" * 60)
    print("Brazil News Scraper  (today only, Brasília time)")
    print("=" * 60)

    cache = {}

    for sector_name, config in BRAZIL_SECTORS.items():
        print(f"\n  [{sector_name}]")
        new_articles = fetch_sector(sector_name, config)
        existing_articles = existing_cache.get(sector_name, {}).get("articles", [])
        articles = merge_articles(existing_articles, new_articles, today_brasilia)
        print(f"  → {len(new_articles)} new, {len(articles)} total after purge")

        cache[sector_name] = {
            "sector":       sector_name,
            "description":  config["description"],
            "color":        config["color"],
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "articles":     articles,
        }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=4, ensure_ascii=False)

    total = sum(len(v["articles"]) for v in cache.values())
    print()
    print("=" * 60)
    print(f"Done — {total} articles across {len(cache)} sectors")
    print(f"Saved → {OUTPUT_FILE}")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()

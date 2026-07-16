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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.net_utils import get_with_retry, DEFAULT_HEADERS  # noqa: E402
from core.scoring import importance_bucket  # noqa: E402

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
            "PIB", "IPCA", "IGP-M", "Selic", "Copom", "Banco Central",
            "política monetária", "resultado primário", "dívida pública",
            "arcabouço fiscal", "meta de inflação", "Tesouro Nacional",
            "leilão de títulos", "Focus", "taxa de desemprego", "CAGED", "PNAD",
            "risco-país", "rating soberano", "reforma tributária",
        ],
        "description": "Macro economy, monetary policy, fiscal accounts, inflation",
        "color": "#16a34a",
    },
    "Energy": {
        "keywords": [
            "Petrobras", "pré-sal", "ANP", "leilão de petróleo", "bacia de Santos",
            "refino", "diesel", "gás natural", "GNL", "gasoduto", "Comgás",
            "ANEEL", "leilão de energia", "tarifa de energia", "matriz energética",
            "energia eólica", "energia solar", "hidrelétrica", "ONS",
        ],
        "description": "Oil, gas, Petrobras, energy sector",
        "color": "#f97316",
    },
    "Mining & Steel": {
        "keywords": [
            "Vale", "minério de ferro", "Carajás", "S11D", "níquel", "cobre",
            "bauxita", "CSN", "Usiminas", "Gerdau", "CBMM", "siderurgia",
            "vergalhão", "bobina de aço", "antidumping aço", "IBRAM", "ANM",
            "barragem de rejeitos",
        ],
        "description": "Mining, iron ore, steel industry",
        "color": "#f59e0b",
    },
    "Pulp & Paper": {
        "keywords": [
            "Suzano", "Klabin", "Bracell", "Eldorado", "celulose de mercado",
            "fibra curta", "fibra longa", "BHKP", "NBSK", "preço da celulose",
            "exportação de celulose", "papel kraft", "tissue", "eucalipto",
            "manejo florestal", "Ibá",
        ],
        "description": "Pulp, paper, cellulose sector",
        "color": "#10b981",
    },
    "Agriculture": {
        "keywords": [
            "Conab", "safra", "soja em grão", "farelo de soja", "milho safrinha",
            "café arábica", "açúcar VHP", "etanol de cana", "algodão em pluma",
            "boi gordo", "arroba", "JBS", "Marfrig", "Minerva", "frigorífico",
            "febre aftosa", "porto de Santos", "fertilizantes", "quebra de safra",
        ],
        "description": "Agribusiness, commodities, exports",
        "color": "#84cc16",
    },
    "Financial": {
        "keywords": [
            "Itaú", "Bradesco", "Banco do Brasil", "Santander", "BTG Pactual",
            "Nubank", "inadimplência", "carteira de crédito", "spread bancário",
            "Basileia", "Pix", "open finance", "CVM", "crédito consignado",
            "crédito imobiliário", "fintech", "Stone", "PagSeguro", "IPO",
            "oferta pública",
        ],
        "description": "Banking sector, credit, interest rates",
        "color": "#2563eb",
    },
    "Industry": {
        "keywords": [
            "PIM", "PMI industrial", "CNI", "capacidade instalada", "Anfavea",
            "Embraer", "WEG", "indústria automotiva", "bens de capital",
            "Zona Franca de Manaus", "IPI", "desoneração da folha",
            "produção industrial",
        ],
        "description": "Manufacturing, industrial output, PMI",
        "color": "#7c3aed",
    },
    "Trade & FX": {
        "keywords": [
            "balança comercial", "superávit comercial", "déficit comercial",
            "Secex", "MDIC", "Mercosul", "acordo comercial", "tarifaço",
            "dólar comercial", "PTAX", "swap cambial", "reservas internacionais",
            "investimento estrangeiro direto", "balanço de pagamentos",
        ],
        "description": "Trade balance, foreign exchange, exports/imports",
        "color": "#0891b2",
    },
}

# ── Trusted sources ──────────────────────────────────────────────────────────
TRUSTED_SOURCES = {
    "Reuters", "Bloomberg", "Financial Times", "The Wall Street Journal",
    "CNBC", "MarketWatch", "Investing.com",
    "Valor Econômico", "InfoMoney", "Exame", "NeoFeed",
    "Agência Brasil", "CNN Brasil", "UOL Economia",
    "Estadão", "O Estado de S. Paulo", "Folha de S.Paulo",
    "O Globo", "G1", "Broadcast", "Agência Estado",
    "Seu Dinheiro", "MoneyTimes", "Capital Aberto",
    "Suno Research", "Genial Investimentos", "XP Investimentos",
    "BTG Pactual", "Itaú BBA", "Bradesco BBI",
}
TRUSTED_LOWER = {s.lower() for s in TRUSTED_SOURCES}

BRAZIL_ECO_TERMS = {
    "economia", "pib", "selic", "ipca", "inflação", "dólar", "câmbio",
    "balança", "exportação", "importação", "investimento", "fiscal",
    "resultado", "produção", "crescimento", "recessão", "juros",
    "mercado", "bolsa", "ações", "lucro", "receita", "ebitda",
    "dividendo", "guidance", "trimestre", "anual", "desemprego",
    "billion", "million", "bilhão", "milhão", "percent", "%",
}


def relevance_score(title: str, source: str, sector_keywords: list[str]) -> int:
    title_lower  = title.lower()
    source_lower = source.lower()
    score = 0

    if source_lower in TRUSTED_LOWER:
        score += 3
    elif any(t in source_lower for t in TRUSTED_LOWER):
        score += 2

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

    for term in keywords:
        url = google_news_url(term)
        try:
            response = get_with_retry(url, headers=HEADERS, timeout=15, retries=1)
            feed = feedparser.parse(response.content)
        except Exception as e:
            print(f"      {term}: ERROR — {e}")
            time.sleep(0.4)
            continue

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

            seen_titles.add(title)
            if link:
                seen_links.add(link)

            source = entry.get("source", {}).get("title", "Unknown")
            score  = relevance_score(title, source, keywords)

            candidates.append({
                "title":     title,
                "link":      link,
                "published": published,
                "source":    source,
                "_score":    score,
                "_pub_dt":   pub_dt,
            })

        time.sleep(0.4)

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

        time.sleep(1.5)

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

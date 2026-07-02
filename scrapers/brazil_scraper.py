#!/usr/bin/env python3
"""
brazil_scraper.py
Fetches Brazil macro/sector news from Google News RSS (pt-BR), organised by sector.
Output: brazil_news_cache.json

Run:  python3 brazil_scraper.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import requests
import feedparser

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT         = Path(__file__).resolve().parent.parent
OUTPUT_FILE  = ROOT / "data" / "brazil_news_cache.json"
MAX_KEEP     = 50
MIN_SCORE    = 2
MAX_AGE_DAYS = 1    # keep today (0) and yesterday (1) only

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/122 Safari/537.36"
    )
}

# ── Sector definitions ───────────────────────────────────────────────────────
BRAZIL_SECTORS: dict[str, dict] = {
    "Economy": {
        "query": 'Brasil (economia OR PIB OR SELIC OR Banco Central OR fiscal OR inflação OR IPCA OR dívida pública)',
        "description": "Macro economy, monetary policy, fiscal accounts, inflation",
        "color": "#16a34a",
    },
    "Energy": {
        "query": 'Brasil (Petrobras OR petróleo OR pré-sal OR combustível OR refino OR gás natural OR energia)',
        "description": "Oil, gas, Petrobras, energy sector",
        "color": "#f97316",
    },
    "Mining & Steel": {
        "query": 'Brasil (Vale OR Gerdau OR mineração OR minério OR aço OR siderurgia OR ferro OR cobre)',
        "description": "Mining, iron ore, steel industry",
        "color": "#f59e0b",
    },
    "Pulp & Paper": {
        "query": 'Brasil (Suzano OR celulose OR papel OR floresta OR eucalipto OR "indústria de papel")',
        "description": "Pulp, paper, cellulose sector",
        "color": "#10b981",
    },
    "Agriculture": {
        "query": 'Brasil (agronegócio OR soja OR milho OR café OR açúcar OR exportação agrícola OR grãos)',
        "description": "Agribusiness, commodities, exports",
        "color": "#84cc16",
    },
    "Financial": {
        "query": 'Brasil (Itaú OR Bradesco OR "Banco do Brasil" OR crédito OR inadimplência OR Pix OR fintech OR bancos)',
        "description": "Banking sector, credit, interest rates",
        "color": "#2563eb",
    },
    "Industry": {
        "query": 'Brasil (produção industrial OR manufatura OR PMI OR indústria OR WEG OR Embraer OR automotivo)',
        "description": "Manufacturing, industrial output, PMI",
        "color": "#7c3aed",
    },
    "Trade & FX": {
        "query": 'Brasil (balança comercial OR câmbio OR real OR exportações OR importações OR BRL OR dólar)',
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


def extract_keywords(query: str) -> list[str]:
    cleaned = re.sub(r'OR|AND|NOT|"', ' ', query)
    return [w.strip() for w in cleaned.split() if len(w.strip()) > 2]


def google_news_url(query: str) -> str:
    return (
        f"https://news.google.com/rss/search?"
        f"q={quote(query)}"
        f"&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    )


def is_fresh(published: str, now: datetime) -> bool:
    try:
        return (now - parsedate_to_datetime(published)).days <= MAX_AGE_DAYS
    except Exception:
        return False


def balance_by_day(articles: list[dict], now: datetime, max_keep: int) -> list[dict]:
    today_arts, yest_arts = [], []
    for a in articles:
        try:
            age = (now - parsedate_to_datetime(a["published"])).days
        except Exception:
            continue
        if age == 0:   today_arts.append(a)
        elif age == 1: yest_arts.append(a)
    half = max_keep // 2
    t    = today_arts[:half]
    y    = yest_arts[:half]
    gap  = max_keep - len(t) - len(y)
    if gap > 0:
        if len(t) < half: y = yest_arts[:len(y) + gap]
        else:             t = today_arts[:len(t) + gap]
    return t + y


def merge_articles(existing: list[dict], new_articles: list[dict], now: datetime) -> list[dict]:
    new_links = {a["link"] for a in new_articles}
    fresh_existing = [
        a for a in existing
        if a["link"] not in new_links and is_fresh(a.get("published", ""), now)
    ]
    return balance_by_day(new_articles + fresh_existing, now, MAX_KEEP)



def fetch_sector(sector_name: str, config: dict) -> list[dict]:
    url      = google_news_url(config["query"])
    keywords = extract_keywords(config["query"])

    try:
        response = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except Exception as e:
        print(f"    ERROR: {e}")
        return []

    candidates = []
    seen = set()
    now  = datetime.now(timezone.utc)

    for entry in feed.entries:
        title = entry.get("title", "").strip()
        if not title or title in seen:
            continue
        seen.add(title)

        published = entry.get("published", "")
        try:
            pub_dt   = parsedate_to_datetime(published)
            age_days = (now - pub_dt).days
        except Exception:
            continue

        if age_days > MAX_AGE_DAYS:
            continue

        source = entry.get("source", {}).get("title", "Unknown")
        score  = relevance_score(title, source, keywords)

        candidates.append({
            "title":     title,
            "link":      entry.get("link", ""),
            "published": published,
            "source":    source,
            "_score":    score,
            "_pub_dt":   pub_dt,
        })

    filtered = [a for a in candidates if a["_score"] >= MIN_SCORE]
    filtered.sort(key=lambda x: x["_pub_dt"], reverse=True)
    kept = filtered[:MAX_KEEP]

    for a in kept:
        del a["_score"]
        del a["_pub_dt"]

    return kept


def main():
    import os
    now = datetime.now(timezone.utc)

    existing_cache: dict = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_cache = json.load(f)

    print()
    print("=" * 60)
    print("Brazil News Scraper  (last 3 days only)")
    print("=" * 60)

    cache = {}

    for sector_name, config in BRAZIL_SECTORS.items():
        print(f"\n  [{sector_name}]")
        new_articles = fetch_sector(sector_name, config)
        existing_articles = existing_cache.get(sector_name, {}).get("articles", [])
        articles = merge_articles(existing_articles, new_articles, now)
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

#!/usr/bin/env python3
"""
credit_scraper.py
Fetches Brazilian credit-market news from Google News RSS (pt-BR).
Sectors: RJs, debt issuance, bank credit, leverage, spreads, ratings, capital markets.
Output: credit_news_cache.json

Run:  python3 credit_scraper.py
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
OUTPUT_FILE  = ROOT / "data" / "credit_news_cache.json"
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
CREDIT_SECTORS: dict[str, dict] = {
    "Recuperação Judicial": {
        "query": (
            '"recuperação judicial" OR "RJ" OR falência OR insolvência '
            'OR "reestruturação de dívida" OR "plano de recuperação"'
        ),
        "description": "Judicial recovery proceedings, bankruptcies, debt restructuring",
        "color": "#dc2626",
    },
    "Emissão de Dívida": {
        "query": (
            'Brasil (debênture OR debenture OR CRI OR CRA OR FIDC '
            'OR "emissão de dívida" OR "nota comercial" OR "eurobond" OR bond '
            'OR "certificado de recebíveis")'
        ),
        "description": "Debentures, CRIs, CRAs, FIDCs, bonds and commercial notes",
        "color": "#7c3aed",
    },
    "Crédito Bancário": {
        "query": (
            'Brasil ("crédito corporativo" OR "empréstimo" OR "financiamento empresarial" '
            'OR "linha de crédito" OR "crédito bancário" OR "crédito para empresas" '
            'OR "banco concede" OR "crédito ao setor")'
        ),
        "description": "Corporate bank credit, loans, financing facilities granted by banks",
        "color": "#2563eb",
    },
    "Alavancagem": {
        "query": (
            'Brasil (alavancagem OR "dívida líquida" OR "net debt" OR ebitda '
            'OR covenant OR "nível de endividamento" OR "endividamento corporativo" '
            'OR "razão dívida" OR "leverage")'
        ),
        "description": "Company leverage, net debt/EBITDA ratios, covenants",
        "color": "#f59e0b",
    },
    "Custo de Crédito": {
        "query": (
            'Brasil ("spread de crédito" OR "custo de financiamento" '
            'OR "taxa de captação" OR "custo da dívida" OR "custo do crédito" '
            'OR "taxa de juros corporativa" OR "prêmio de risco")'
        ),
        "description": "Credit spreads, cost of debt, financing rates for companies",
        "color": "#059669",
    },
    "Rating": {
        "query": (
            'Brasil (rating OR rebaixamento OR "upgrade" OR "downgrade" '
            'OR "Fitch" OR "Moody\'s" OR "Standard & Poor\'s" OR "S&P" '
            'OR "classificação de risco" OR "perspectiva negativa" OR "perspectiva positiva")'
        ),
        "description": "Credit ratings, upgrades, downgrades by Fitch, Moody's, S&P",
        "color": "#0891b2",
    },
    "Mercado de Capitais": {
        "query": (
            'Brasil ("mercado de dívida" OR "renda fixa" OR "oferta de dívida" '
            'OR "emissão primária" OR "captação de recursos" OR "roadshow" '
            'OR "escritura de emissão" OR "instrumento de dívida")'
        ),
        "description": "Debt capital markets, fixed income issuances, primary offerings",
        "color": "#84cc16",
    },
}

# ── Trusted sources ──────────────────────────────────────────────────────────
TRUSTED_SOURCES = {
    "Reuters", "Bloomberg", "Financial Times", "The Wall Street Journal",
    "CNBC", "MarketWatch", "Investing.com",
    "Valor Econômico", "InfoMoney", "Exame", "NeoFeed", "Pipeline Capital",
    "Agência Brasil", "CNN Brasil", "UOL Economia",
    "Estadão", "O Estado de S. Paulo", "Folha de S.Paulo", "O Globo",
    "Broadcast", "Agência Estado", "Seu Dinheiro", "MoneyTimes",
    "Capital Aberto", "Investidor Institucional", "Ágora Investimentos",
    "BTG Pactual", "Itaú BBA", "Bradesco BBI", "XP Investimentos",
    "Suno Research", "Genial Investimentos",
}
TRUSTED_LOWER = {s.lower() for s in TRUSTED_SOURCES}

CREDIT_TERMS = {
    "crédito", "dívida", "débito", "empréstimo", "financiamento", "captação",
    "spread", "juros", "taxa", "rating", "debenture", "debênture", "cri", "cra",
    "fidc", "bond", "covenant", "inadimplência", "default", "recuperação",
    "falência", "reestruturação", "alavancagem", "ebitda", "leverage",
    "capital", "emissão", "oferta", "banco", "bilhão", "milhão", "r$",
}


def relevance_score(title: str, source: str, sector_keywords: list[str]) -> int:
    title_lower  = title.lower()
    source_lower = source.lower()
    score = 0

    if source_lower in TRUSTED_LOWER:
        score += 3
    elif any(t in source_lower for t in TRUSTED_LOWER):
        score += 2

    kw_hits = sum(1 for kw in sector_keywords if kw.lower() in title_lower)
    score += min(kw_hits, 4)

    credit_hits = sum(1 for t in CREDIT_TERMS if t in title_lower)
    score += min(credit_hits, 2)

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
    print("Credit News Scraper  (last 3 days only)")
    print("=" * 60)

    cache = {}

    for sector_name, config in CREDIT_SECTORS.items():
        print(f"\n  [{sector_name}]")
        new_articles = fetch_sector(sector_name, config)
        existing_articles = existing_cache.get(sector_name, {}).get("articles", [])
        articles = merge_articles(existing_articles, new_articles, now)
        print(f"  → {len(new_articles)} new, {len(articles)} total")

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

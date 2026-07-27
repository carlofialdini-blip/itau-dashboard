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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.net_utils import get_with_retry, DEFAULT_HEADERS  # noqa: E402
from core.scoring import importance_bucket  # noqa: E402
from core.trusted_sources import is_trusted  # noqa: E402

OUTPUT_FILE  = ROOT / "data" / "credit_news_cache.json"
MAX_KEEP     = 50
MIN_SCORE    = 2
MAX_AGE_DAYS = 7    # keep articles up to 7 days old

HEADERS = DEFAULT_HEADERS

# ── Sector definitions ───────────────────────────────────────────────────────
# Each keyword is queried SEPARATELY (not OR'd into one mega-query) — same
# fix as brazil_scraper.py's, applied here after the same relevance-dilution
# root cause was confirmed for scraper.py's portfolio companies (Bradesco:
# see that file's comment). A broad OR query spends Google News RSS's
# ~100-result relevance budget on whichever single sub-topic is most
# dominant, which can crowd out real coverage of a sector's other terms
# entirely. Previously each sector here was one big "Brasil (t1 OR t2 OR ...
# OR t8)" query; now every term below is its own request.
CREDIT_SECTORS: dict[str, dict] = {
    "Recuperação Judicial": {
        "keywords": [
            "recuperação judicial", "RJ", "falência", "insolvência",
            "reestruturação de dívida", "plano de recuperação",
        ],
        "description": "Judicial recovery proceedings, bankruptcies, debt restructuring",
        "color": "#dc2626",
    },
    "Emissão de Dívida": {
        "keywords": [
            "debênture", "debenture", "CRI", "CRA", "FIDC",
            "emissão de dívida", "nota comercial", "eurobond", "bond",
            "certificado de recebíveis",
        ],
        "description": "Debentures, CRIs, CRAs, FIDCs, bonds and commercial notes",
        "color": "#7c3aed",
    },
    "Crédito Bancário": {
        "keywords": [
            "crédito corporativo", "empréstimo", "financiamento empresarial",
            "linha de crédito", "crédito bancário", "crédito para empresas",
            "banco concede", "crédito ao setor",
        ],
        "description": "Corporate bank credit, loans, financing facilities granted by banks",
        "color": "#2563eb",
    },
    "Alavancagem": {
        "keywords": [
            "alavancagem", "dívida líquida", "net debt", "ebitda", "covenant",
            "nível de endividamento", "endividamento corporativo",
            "razão dívida", "leverage",
        ],
        "description": "Company leverage, net debt/EBITDA ratios, covenants",
        "color": "#f59e0b",
    },
    "Custo de Crédito": {
        "keywords": [
            "spread de crédito", "custo de financiamento", "taxa de captação",
            "custo da dívida", "custo do crédito", "taxa de juros corporativa",
            "prêmio de risco",
        ],
        "description": "Credit spreads, cost of debt, financing rates for companies",
        "color": "#059669",
    },
    "Rating": {
        "keywords": [
            "rating", "rebaixamento", "upgrade", "downgrade", "Fitch",
            "Moody's", "Standard & Poor's", "S&P", "classificação de risco",
            "perspectiva negativa", "perspectiva positiva",
        ],
        "description": "Credit ratings, upgrades, downgrades by Fitch, Moody's, S&P",
        "color": "#0891b2",
    },
    "Mercado de Capitais": {
        "keywords": [
            "mercado de dívida", "renda fixa", "oferta de dívida",
            "emissão primária", "captação de recursos", "roadshow",
            "escritura de emissão", "instrumento de dívida",
        ],
        "description": "Debt capital markets, fixed income issuances, primary offerings",
        "color": "#84cc16",
    },
}

CREDIT_TERMS = {
    "crédito", "dívida", "débito", "empréstimo", "financiamento", "captação",
    "spread", "juros", "taxa", "rating", "debenture", "debênture", "cri", "cra",
    "fidc", "bond", "covenant", "inadimplência", "default", "recuperação",
    "falência", "reestruturação", "alavancagem", "ebitda", "leverage",
    "capital", "emissão", "oferta", "banco", "bilhão", "milhão", "r$",
}


def relevance_score(title: str, sector_keywords: list[str]) -> int:
    """Source trust is a hard pre-filter (see is_trusted()), not scored here —
    every candidate reaching this function already passed that gate."""
    title_lower  = title.lower()
    score = 0

    kw_hits = sum(1 for kw in sector_keywords if kw.lower() in title_lower)
    score += min(kw_hits, 4)

    credit_hits = sum(1 for t in CREDIT_TERMS if t in title_lower)
    score += min(credit_hits, 2)

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


def is_fresh(published: str, now: datetime) -> bool:
    try:
        return (now - parsedate_to_datetime(published)).days <= MAX_AGE_DAYS
    except Exception:
        return False


def balance_by_day(articles: list[dict], now: datetime, max_keep: int) -> list[dict]:
    today_arts, yest_arts, older_arts = [], [], []
    for a in articles:
        try:
            age = (now - parsedate_to_datetime(a["published"])).days
        except Exception:
            continue
        if age == 0:   today_arts.append(a)
        elif age == 1: yest_arts.append(a)
        elif age <= MAX_AGE_DAYS: older_arts.append(a)
    half = max_keep // 2
    t    = today_arts[:half]
    y    = yest_arts[:half]
    gap  = max_keep - len(t) - len(y)
    if gap > 0 and len(y) < len(yest_arts):
        extra = yest_arts[len(y):len(y) + gap]
        y += extra
        gap -= len(extra)
    if gap > 0 and len(t) < len(today_arts):
        extra = today_arts[len(t):len(t) + gap]
        t += extra
        gap -= len(extra)
    o = older_arts[:gap] if gap > 0 else []
    return t + y + o


def merge_articles(existing: list[dict], new_articles: list[dict], now: datetime) -> list[dict]:
    new_links = {a["link"] for a in new_articles}
    fresh_existing = [
        a for a in existing
        if a["link"] not in new_links and is_fresh(a.get("published", ""), now)
    ]
    return balance_by_day(new_articles + fresh_existing, now, MAX_KEEP)



def fetch_sector(sector_name: str, config: dict) -> list[dict]:
    keywords = config["keywords"]
    now = datetime.now(timezone.utc)

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
                pub_dt   = parsedate_to_datetime(published)
                age_days = (now - pub_dt).days
            except Exception:
                continue

            if age_days > MAX_AGE_DAYS:
                continue

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

        time.sleep(0.4)

    filtered = [a for a in candidates if a["_score"] >= MIN_SCORE]
    filtered.sort(key=lambda x: x["_pub_dt"], reverse=True)
    kept = filtered[:MAX_KEEP]

    for a in kept:
        a["importance_score"] = a.pop("_score")
        a["importance"]       = importance_bucket(a["importance_score"])
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
    print("Credit News Scraper  (last 7 days)")
    print("=" * 60)

    cache = dict(existing_cache)

    for sector_name, config in CREDIT_SECTORS.items():
        print(f"\n  [{sector_name}]  ({len(config['keywords'])} keyword queries)")
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

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=4, ensure_ascii=False)

        time.sleep(1.5)

    total = sum(len(v["articles"]) for v in cache.values())
    print()
    print("=" * 60)
    print(f"Done — {total} articles across {len(cache)} sectors")
    print(f"Saved → {OUTPUT_FILE}")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()

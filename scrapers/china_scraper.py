#!/usr/bin/env python3
"""
china_scraper.py
Fetches China-focused economic news from Google News RSS, organised by sector.
Output: china_news_cache.json

Run:  python3 china_scraper.py
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import requests
import feedparser

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT         = Path(__file__).resolve().parent.parent
OUTPUT_FILE  = ROOT / "data" / "china_news_cache.json"
MAX_KEEP     = 50   # articles kept per sector after filtering
MIN_SCORE    = 2    # minimum relevance score
MAX_AGE_DAYS = 1    # keep today (0) and yesterday (1) only

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/122 Safari/537.36"
    )
}

# ── Sector definitions ───────────────────────────────────────────────────────
# Each sector has a Google News query and a color for the dashboard.
# Edit this dict to add, remove, or adjust sectors.
CHINA_SECTORS: dict[str, dict] = {
    "Economy": {
        "query":       'China economy GDP "economic growth" PBOC "economic data" OR "trade surplus"',
        "description": "Macro economy, monetary policy, GDP, trade",
        "color":       "#3b82f6",
    },
    "Mining": {
        "query":       'China mining "iron ore" OR copper OR lithium OR "rare earth" OR nickel OR cobalt',
        "description": "Mining, metals, raw materials",
        "color":       "#f59e0b",
    },
    "Steel": {
        "query":       'China steel OR rebar OR "hot rolled" OR "steel production" OR "steel mills" OR "steel demand"',
        "description": "Steel industry, iron ore demand, production data",
        "color":       "#94a3b8",
    },
    "Pulp & Paper": {
        "query":       'China pulp OR "paper industry" OR cellulose OR "paper production" OR "wood pulp"',
        "description": "Pulp, paper, cellulose markets",
        "color":       "#10b981",
    },
    "Construction": {
        "query":       'China "real estate" OR property OR housing OR construction OR infrastructure OR Evergrande',
        "description": "Real estate, infrastructure, construction",
        "color":       "#8b5cf6",
    },
    "Agriculture": {
        "query":       'China agriculture soybean OR wheat OR rice OR pork OR grain OR "food imports" OR livestock',
        "description": "Grains, soybeans, food commodities",
        "color":       "#84cc16",
    },
    "Industrial": {
        "query":       'China manufacturing PMI OR "industrial production" OR factory OR "industrial output" OR "factory output"',
        "description": "Manufacturing, PMI, industrial output",
        "color":       "#ef4444",
    },
    "Energy": {
        "query":       'China energy coal OR "crude oil" OR gas OR renewables OR solar OR "energy consumption" OR "oil imports"',
        "description": "Energy, coal, oil, renewables",
        "color":       "#f97316",
    },
}

# ── Trusted sources ──────────────────────────────────────────────────────────
TRUSTED_SOURCES = {
    # International wires
    "Reuters", "Bloomberg", "Financial Times", "The Wall Street Journal",
    "WSJ", "CNBC", "MarketWatch", "Investing.com", "TradingView",
    "Barron's", "Forbes", "Business Insider", "S&P Global",
    # China-focused
    "South China Morning Post", "Caixin", "Caixin Global",
    "Xinhua", "Nikkei Asia", "Global Times",
    # Trade / commodities
    "Fastmarkets", "Metal Bulletin", "Platts", "Argus Media",
    "World Steel Association", "Steel Orbis",
    # Brazilian (China coverage)
    "Valor Econômico", "InfoMoney", "Exame", "Agência Brasil",
    "CNN Brasil",
}
TRUSTED_LOWER = {s.lower() for s in TRUSTED_SOURCES}

# Terms that raise confidence the article is about China in an economic context
CHINA_ECONOMIC_TERMS = {
    "gdp", "growth", "economy", "trade", "export", "import", "tariff",
    "production", "output", "demand", "supply", "market", "industry",
    "price", "yuan", "renminbi", "pboc", "cpi", "pmi", "government",
    "investment", "infrastructure", "policy", "ministry", "customs",
    "tonnes", "tons", "billion", "million", "percent", "quarter",
}

# ── Relevance scoring ────────────────────────────────────────────────────────

def relevance_score(title: str, source: str, sector_keywords: list[str]) -> int:
    title_lower  = title.lower()
    source_lower = source.lower()
    score = 0

    # Source credibility
    if source_lower in TRUSTED_LOWER:
        score += 3
    elif any(t in source_lower for t in TRUSTED_LOWER):
        score += 2

    # "China" must appear in title
    if "china" in title_lower or "chinese" in title_lower:
        score += 2

    # Sector keywords in title
    kw_hits = sum(1 for kw in sector_keywords if kw.lower() in title_lower)
    score += min(kw_hits, 3)

    # Generic economic terms
    eco_hits = sum(1 for t in CHINA_ECONOMIC_TERMS if t in title_lower)
    score += min(eco_hits, 2)

    # Penalty: too short
    if len(title) < 30:
        score -= 2

    return score


def extract_keywords(query: str) -> list[str]:
    """Pull meaningful words out of the sector query string."""
    # Remove operators and quotes
    cleaned = re.sub(r'OR|AND|NOT|"', ' ', query)
    return [w.strip() for w in cleaned.split() if len(w.strip()) > 2]


# ── Fetcher ──────────────────────────────────────────────────────────────────

def google_news_url(query: str) -> str:
    return (
        f"https://news.google.com/rss/search?"
        f"q={quote(query)}"
        f"&hl=en&gl=US&ceid=US:en"
    )


def fetch_sector(sector_name: str, config: dict) -> list[dict]:
    url      = google_news_url(config["query"])
    keywords = extract_keywords(config["query"])

    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except Exception as e:
        print(f"    ERROR fetching {sector_name}: {e}")
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
            continue  # unparseable date → discard

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


# ── Cache helpers ─────────────────────────────────────────────────────────────

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



# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    import os
    now = datetime.now(timezone.utc)

    # Load existing cache to preserve still-fresh articles
    existing_cache: dict = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_cache = json.load(f)

    print()
    print("=" * 60)
    print("China News Scraper  (last 3 days only)")
    print("=" * 60)

    cache = {}

    for sector_name, config in CHINA_SECTORS.items():
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

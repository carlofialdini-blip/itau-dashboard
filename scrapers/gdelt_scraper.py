#!/usr/bin/env python3
"""
gdelt_scraper.py
Fetches portfolio-company news from the GDELT DOC 2.0 API (api.gdeltproject.org)
as a complement to scraper.py's Google News RSS feed — a second, independent
index that isn't subject to Google News' relevance-ranking/~100-result cap
(the mechanism that was starving some companies, e.g. Bradesco, of same-week
coverage even though real recent articles existed).

Two query legs per company: Portuguese (sourcelang:portuguese, restricted to
sourcecountry:brazil) and English (sourcelang:english, global — international
wires covering Brazilian companies, e.g. Reuters/Bloomberg, aren't
sourcecountry:brazil). Both legs are hard-filtered to a domain whitelist
(core.trusted_sources) same as the Google News scrapers — GDELT itself does
no relevance ranking or quality filtering at all, so this project's usual
domain whitelist matters even more here than for Google News.

BATCHED QUERIES: companies are grouped BATCH_SIZE at a time and OR'd into one
query (e.g. `("Petrobras" OR "Vale" OR ...) sourcelang:...`), with each
returned article attributed back to whichever single company name/ticker
matches as a whole word in its title (skipped if zero or more than one
company matches — safer to drop an ambiguous result than misattribute it).
This exists because a portfolio of ~150 companies at 1 request/company/leg
would take ~30 minutes (150 x 2 x 6s throttle) — well past
update_dashboard.py's 600s per-step timeout. BATCH_SIZE has NOT been
empirically verified against GDELT's query complexity limit (development
testing triggered GDELT's rate limit before this could be confirmed live —
see CLAUDE.md §8). Start conservative and increase only after confirming
live that a larger batch doesn't silently drop or garble results.

DISAMBIGUATION: prefers an explicit "Ticker" column in portfolio.xlsx (e.g.
VALE3 for Vale, which is otherwise a common Portuguese word) over the bare
company name — optional per row, blank is fine, falls back to company name.
This replaces a hardcoded per-company Python dict, which doesn't scale to a
~150-row portfolio (silently weaker queries for any company someone forgot
to add to the dict, with no visible error).

GDELT's own API enforces (and states via a 429 response body) a "one request
every 5 seconds" limit, with no documented recovery window if exceeded —
confirmed empirically to lock out for over 30 minutes after a short burst of
unspaced test requests. This scraper throttles to one request per 6+ seconds
and treats a 429 as a hard stop for the run (one retry after a long backoff,
then abort) rather than retrying aggressively, which would just prolong the
lockout.

Output: gdelt_news_cache.json, same company-keyed shape as news_cache.json
(scraper.py) so it flows through the existing flatten_news() loader in
core/generate_dashboard.py unchanged. Written incrementally (after every
batch, not just once at the end) so a timeout kill loses at most the
in-flight batch, not the whole run.

Run:  python3 gdelt_scraper.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime, format_datetime
from pathlib import Path

import pandas as pd
import requests

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.scoring import importance_bucket  # noqa: E402
from core.trusted_sources import is_trusted, outlet_name, TRUSTED_DOMAINS, TRUSTED_DOMAINS_EN  # noqa: E402

EXCEL_FILE  = ROOT / "data" / "portfolio.xlsx"
OUTPUT_FILE = ROOT / "data" / "gdelt_news_cache.json"

GDELT_URL     = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_INTERVAL  = 6.0   # seconds between GDELT requests — above its documented 5s floor
TIMESPAN      = "7d"  # matches scraper.py's MAX_AGE_DAYS freshness window
MAX_AGE_DAYS  = 7
MAX_KEEP      = 25    # per company, after merging both language legs
MIN_SCORE     = 0     # attribution (whole-word match) already gates relevance; this is a light extra floor

# UNVERIFIED against GDELT's real query-complexity ceiling — see module
# docstring. Reduce if a production run's per-batch article counts look
# suspiciously low/skewed toward whichever company is first in the OR list.
BATCH_SIZE = 5

# Legacy fallback for the current 10-company portfolio if its Ticker column
# is ever blank — new companies should just get a Ticker in portfolio.xlsx
# instead of an entry here.
AMBIGUOUS_TERM_FALLBACK = {
    "Vale":   "VALE3",
    "Gerdau": "GGBR4",
}

HEADERS = {"User-Agent": "ItauPortfolioDashboard/1.0 (internal research tool; not for redistribution)"}

FINANCIAL_TERMS = {
    "lucro", "receita", "resultado", "ação", "ações", "dividendo", "ebitda",
    "balanço", "bolsa", "mercado", "trimestre", "ipo", "fusão", "aquisição",
    "profit", "revenue", "earnings", "shares", "stock", "dividend", "merger",
    "acquisition", "quarter", "guidance", "rating", "upgrade", "downgrade",
}


class GdeltRateLimited(Exception):
    pass


_last_call = [0.0]


def _throttle():
    elapsed = time.monotonic() - _last_call[0]
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_call[0] = time.monotonic()


def gdelt_request(params: dict, retries_on_429: int = 1):
    _throttle()
    try:
        r = requests.get(GDELT_URL, params=params, headers=HEADERS, timeout=20, verify=False)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        print(f"      ERROR (connection): {e}")
        return None

    if r.status_code == 429:
        if retries_on_429 > 0:
            print("      Rate limited by GDELT — backing off 60s before one retry...")
            time.sleep(60)
            return gdelt_request(params, retries_on_429=retries_on_429 - 1)
        print("      Still rate limited after backoff — stopping GDELT fetches for this run.")
        raise GdeltRateLimited()

    if r.status_code != 200:
        print(f"      ERROR: HTTP {r.status_code} — {r.text[:200]}")
        return None

    try:
        return r.json()
    except ValueError:
        return None


def query_term(company: str, ticker: str) -> str:
    if ticker:
        return ticker
    return AMBIGUOUS_TERM_FALLBACK.get(company, company)


def parse_seendate(seendate: str):
    try:
        return datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def relevance_score(title: str) -> int:
    title_lower = title.lower()
    score = 1  # attribution already required a confident whole-word match
    hits = sum(1 for t in FINANCIAL_TERMS if t in title_lower)
    score += min(hits, 2)
    if len(title) < 25:
        score -= 2
    return score


def attribute(title: str, company_patterns: dict) -> str | None:
    """Match exactly one batched company as a whole word in the title,
    checking BOTH the term GDELT was queried with (which may be a ticker,
    e.g. VALE3) AND the plain company name — an article headline often
    uses the name even when the query matched on the ticker elsewhere in
    the body. Zero or multiple companies matching is dropped rather than
    guessed at — an under-attributed batch is safer than a misattributed
    one, but checking only the query term would under-attribute far more
    than necessary and quietly undercut GDELT's whole recall advantage."""
    title_lower = title.lower()
    matched = [
        company for company, patterns in company_patterns.items()
        if any(re.search(r'\b' + re.escape(p.lower()) + r'\b', title_lower) for p in patterns)
    ]
    return matched[0] if len(matched) == 1 else None


def fetch_batch(batch: list, language: str, domains: set, sourcecountry: str = None) -> dict:
    """One GDELT request covering every company in batch (each item a dict
    with 'company' and 'term'). Returns {company: [article, ...]}."""
    terms = [c["term"] for c in batch]
    company_patterns = {
        c["company"]: list({c["term"], c["company"]})  # dedup when term == company name
        for c in batch
    }
    quoted = " OR ".join(f'"{t}"' for t in terms)
    query = f'({quoted}) sourcelang:{language}'
    if sourcecountry:
        query += f' sourcecountry:{sourcecountry}'

    params = {
        "query":      query,
        "mode":       "artlist",
        "format":     "json",
        "maxrecords": min(250, 40 * len(terms)),
        "timespan":   TIMESPAN,
        "sort":       "datedesc",
    }
    data = gdelt_request(params)
    result = {c["company"]: [] for c in batch}
    if not data:
        return result

    lang_tag = "pt" if language == "portuguese" else "en"
    for a in data.get("articles", []):
        title  = (a.get("title") or "").strip()
        url    = a.get("url", "")
        domain = a.get("domain", "")
        if not title or not url or not domain:
            continue
        if not is_trusted(domain, domains):
            continue
        company = attribute(title, company_patterns)
        if company is None:
            continue
        dt = parse_seendate(a.get("seendate", ""))
        if dt is None:
            continue
        score = relevance_score(title)
        if score < MIN_SCORE:
            continue
        result[company].append({
            "title":     title,
            "link":      url,
            "published": format_datetime(dt),
            "source":    outlet_name(domain),
            "language":  lang_tag,
            "importance_score": score,
            "importance":       importance_bucket(score),
            "_pub_dt":   dt,
        })
    return result


def is_fresh(published: str, now: datetime) -> bool:
    try:
        return (now - parsedate_to_datetime(published)).days <= MAX_AGE_DAYS
    except Exception:
        return False


def merge_articles(existing: list, new_articles: list, now: datetime) -> list:
    new_links = {a["link"] for a in new_articles}
    fresh_existing = [
        a for a in existing
        if a["link"] not in new_links and is_fresh(a.get("published", ""), now)
    ]
    combined = new_articles + fresh_existing

    def _sort_key(a):
        try:
            return parsedate_to_datetime(a["published"])
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    combined.sort(key=_sort_key, reverse=True)
    return combined[:MAX_KEEP]


def chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def main():
    df = pd.read_excel(EXCEL_FILE)
    df = df[df["Company"].astype(str).str.strip() != "Company"]

    companies = []
    for _, row in df.iterrows():
        company = str(row["Company"]).strip()
        sector  = str(row.get("Sector", "")).strip()
        ticker  = str(row.get("Ticker", "") or "").strip()
        if ticker.lower() == "nan":
            ticker = ""
        companies.append({"company": company, "sector": sector, "term": query_term(company, ticker)})

    existing_cache: dict = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_cache = json.load(f)

    now = datetime.now(timezone.utc)

    print()
    print("=" * 60)
    print(f"GDELT News Scraper  (complements scraper.py, PT + EN, batches of {BATCH_SIZE})")
    print("=" * 60)

    cache = dict(existing_cache)  # start from existing so a partial run still has everything else
    new_by_company = {c["company"]: [] for c in companies}

    def checkpoint():
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=4, ensure_ascii=False)

    try:
        for batch in chunk(companies, BATCH_SIZE):
            names = ", ".join(c["company"] for c in batch)
            print(f"\n  Batch: {names}")

            pt = fetch_batch(batch, "portuguese", TRUSTED_DOMAINS, sourcecountry="brazil")
            en = fetch_batch(batch, "english", TRUSTED_DOMAINS_EN)

            for c in batch:
                company = c["company"]
                seen_links, seen_titles, deduped = set(), set(), []
                for a in pt.get(company, []) + en.get(company, []):
                    if a["link"] in seen_links or a["title"] in seen_titles:
                        continue
                    seen_links.add(a["link"])
                    seen_titles.add(a["title"])
                    deduped.append(a)
                deduped.sort(key=lambda x: x["_pub_dt"], reverse=True)
                for a in deduped:
                    del a["_pub_dt"]
                new_by_company[company] = deduped[:MAX_KEEP]
                print(f"    {company}: {len(deduped)} new")

            # checkpoint after every batch — a timeout kill only loses the in-flight batch
            for c in batch:
                company = c["company"]
                existing_articles = existing_cache.get(company, {}).get("articles", [])
                articles = merge_articles(existing_articles, new_by_company[company], now)
                cache[company] = {
                    "company":       company,
                    "sector":        c["sector"],
                    "keywords":      [f"PT:{c['term']}", f"EN:{c['term']}"],
                    "last_updated":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "article_count": len(articles),
                    "articles":      articles,
                }
            checkpoint()

    except GdeltRateLimited:
        print("\n  Stopping — GDELT rate limit hit. Companies not yet reached keep their existing cache.")
        checkpoint()

    total = sum(len(v["articles"]) for v in cache.values())
    print()
    print("=" * 60)
    print(f"Done — {total} articles across {len(cache)} companies")
    print(f"Saved → {OUTPUT_FILE}")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()

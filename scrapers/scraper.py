import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
import feedparser

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.net_utils import get_with_retry, DEFAULT_HEADERS  # noqa: E402
from core.scoring import importance_bucket  # noqa: E402
from core.trusted_sources import is_trusted  # noqa: E402

EXCEL_FILE   = ROOT / "data" / "portfolio.xlsx"
OUTPUT_FILE  = ROOT / "data" / "news_cache.json"

MAX_KEEP     = 50   # articles kept per company after filtering
MIN_SCORE    = 3    # minimum relevance score
MAX_AGE_DAYS = 7    # keep articles up to 7 days old

HEADERS = DEFAULT_HEADERS

# ── Company aliases and stock tickers ───────────────────────────────────────
# These supplement the keywords column and make queries far more specific.
COMPANY_ALIASES: dict[str, list[str]] = {
    "Petrobras":       ["PETR3", "PETR4", "petróleo", "pré-sal", "combustível", "refino"],
    "Vale":            ["VALE3", "Vale S.A.", "minério", "minério de ferro", "pelota"],
    "Itaú Unibanco":   ["ITUB3", "ITUB4", "Itaú", "Unibanco", "banco digital"],
    "Banco do Brasil": ["BBAS3", "BB Seguridade", "Agrobanco", "banco público"],
    "Bradesco":        ["BBDC3", "BBDC4", "Bradesco Seguros", "Next"],
    "Embraer":         ["EMBR3", "E175", "E195", "E2", "jato", "aeronave"],
    "Suzano":          ["SUZB3", "celulose", "eucalipto", "papel e celulose"],
    "WEG":             ["WEGE3", "motor elétrico", "automação industrial", "energia renovável"],
    "Gerdau":          ["GGBR4", "GOAU4", "aço", "siderurgia", "vergalhão"],
    "Ambev":           ["ABEV3", "Brahma", "Skol", "Guaraná Antarctica"],
}

# Generic business/financial terms — presence in title adds to relevance score
FINANCIAL_TERMS = {
    "ações", "bolsa", "bovespa", "b3", "lucro", "prejuízo", "resultado",
    "dividendo", "dividend", "receita", "revenue", "earnings", "ebitda",
    "guidance", "balanço", "cvm", "sec", "ipo", "oferta pública",
    "fusão", "aquisição", "merger", "acquisition", "ceo", "diretoria",
    "produção", "exportação", "importação", "contrato", "parceria",
    "investimento", "capex", "desinvestimento", "shares", "stock",
    "mercado", "trimestre", "quarter", "fiscal", "anual",
    "rating", "upgrade", "downgrade", "target price", "recomendação",
    "alta", "queda", "valorização", "desvalorização", "resultados",
    "relatório", "projeção", "previsão", "forecast",
}

# ── Company names that are also common words in Portuguese ──────────────────
# For these, matching the name alone in the title is NOT enough.
AMBIGUOUS_NAMES = {"Vale", "Gerdau"}


# ── Query builder ────────────────────────────────────────────────────────────
#
# One request per specific term (keyword or alias), each combined with the
# company name via AND — never OR'd together into a single mega-query. Google
# News RSS ranks a query's results by relevance, not recency, and caps each
# query at ~100 entries; a broad "company AND (term1 OR term2 OR ... OR
# term8)" query spends that whole relevance budget on whichever single term
# is most dominant, which can starve a company of results entirely even
# though real recent coverage exists. Root-caused for Bradesco: its combined
# query returned Google's full 100-result cap, but only 1 of those 100 was
# from a whitelisted domain, and even that one scored below MIN_SCORE because
# Google's phrase match was against the article body, not the title — a
# single-keyword test ("Bradesco BBDC4") surfaced a full page of good, recent
# results the mega-query had been quietly starving out. This is the exact
# same relevance-dilution problem already diagnosed and fixed for
# brazil_scraper.py (see that file's module comment) — applied here.

def build_queries(company: str, keywords: list[str]) -> list[str]:
    """All the Google News queries to run for one company — one per specific
    term, not one term-1-OR-term-2-OR-... query. No cap on term count: each
    is its own request now, not competing for one query's relevance budget."""
    aliases = COMPANY_ALIASES.get(company, [])
    terms = keywords + aliases
    if not terms:
        # No specific terms available at all — fall back to requiring at
        # least a financial-context word, same safety net as before.
        return [f'"{company}" (ações OR resultados OR lucro OR bolsa)']

    queries = []
    for t in terms:
        t_fmt = f'"{t}"' if " " in t else t
        queries.append(f'"{company}" {t_fmt}')
    return queries


def google_news_url(query: str) -> str:
    return (
        f"https://news.google.com/rss/search?"
        f"q={quote(query)}"
        f"&hl=pt-BR"
        f"&gl=BR"
        f"&ceid=BR:pt-419"
    )


# ── Relevance scoring ────────────────────────────────────────────────────────

def relevance_score(title: str, company: str, keywords: list[str]) -> int:
    """
    Score an article for relevance to the company (higher = more relevant).
    Source trust is a hard pre-filter (see is_trusted()), not scored here —
    every candidate reaching this function already passed that gate.

    Scoring breakdown:
      +3  Company name as whole word in title  (skipped for ambiguous names)
      +2  Company alias or ticker in title
      +1  Each company keyword in title (cap +3)
      +1  Each generic financial term in title (cap +2)
      -2  Title shorter than 30 chars (likely a fragment/junk)
    """
    title_lower  = title.lower()
    score = 0

    # Company name present in title (skip for ambiguous words like "vale")
    if company not in AMBIGUOUS_NAMES:
        pattern = re.compile(r'\b' + re.escape(company.lower()) + r'\b')
        if pattern.search(title_lower):
            score += 3

    # Company alias / ticker in title
    aliases = COMPANY_ALIASES.get(company, [])
    for alias in aliases:
        if alias.lower() in title_lower:
            score += 2
            break

    # Company-specific keywords in title
    kw_hits = sum(1 for kw in keywords if kw.lower() in title_lower)
    score += min(kw_hits, 3)

    # Generic financial terms in title
    fin_hits = sum(1 for t in FINANCIAL_TERMS if t in title_lower)
    score += min(fin_hits, 2)

    # Penalty: suspiciously short title
    if len(title) < 30:
        score -= 2

    return score


# ── Fetcher ──────────────────────────────────────────────────────────────────

def fetch_articles(company: str, keywords: list[str]) -> tuple[list[dict], int, int]:
    """
    Fetch, score, and filter articles for one company — one Google News
    request per specific term (see build_queries() above), merged and
    deduped across all of them before scoring/capping.
    Returns (kept_articles, raw_count, kept_count).
    """
    queries = build_queries(company, keywords)

    candidates  = []
    seen_titles = set()
    seen_links  = set()
    now = datetime.now(timezone.utc)

    for query in queries:
        url = google_news_url(query)
        try:
            response = get_with_retry(url, headers=HEADERS, timeout=15, retries=1)
            feed = feedparser.parse(response.content)
        except Exception as e:
            print(f"      {query}: ERROR — {e}")
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
                continue  # unparseable date → discard

            if age_days > MAX_AGE_DAYS:
                continue

            source_info = entry.get("source", {})
            if not is_trusted(source_info.get("href", "")):
                continue  # hard gate: only whitelisted domains

            seen_titles.add(title)
            if link:
                seen_links.add(link)

            source = source_info.get("title", "Google News")
            score  = relevance_score(title, company, keywords)

            candidates.append({
                "title":     title,
                "link":      link,
                "published": published,
                "source":    source,
                "summary":   entry.get("summary", ""),
                "_score":    score,
                "_pub_dt":   pub_dt,
            })

        time.sleep(0.4)

    # Keep only articles above score threshold, sort newest first
    filtered = [a for a in candidates if a["_score"] >= MIN_SCORE]
    filtered.sort(key=lambda x: x["_pub_dt"], reverse=True)
    kept = filtered[:MAX_KEEP]

    for a in kept:
        a["importance_score"] = a.pop("_score")
        a["importance"]       = importance_bucket(a["importance_score"])
        del a["_pub_dt"]

    return kept, len(candidates), len(kept)


# ── Main ─────────────────────────────────────────────────────────────────────

# ── Cache helpers ─────────────────────────────────────────────────────────────

def is_fresh(published: str, now: datetime) -> bool:
    try:
        return (now - parsedate_to_datetime(published)).days <= MAX_AGE_DAYS
    except Exception:
        return False


def balance_by_day(articles: list[dict], now: datetime, max_keep: int) -> list[dict]:
    """
    Prefer a 50/50 split between today (age_days==0) and yesterday (age_days==1).
    If both days are sparse, fill remaining slots from the rest of the
    MAX_AGE_DAYS window (age 2..MAX_AGE_DAYS) instead of dropping them.
    """
    today_arts, yest_arts, older_arts = [], [], []
    for a in articles:
        try:
            age = (now - parsedate_to_datetime(a["published"])).days
        except Exception:
            continue
        if age == 0:
            today_arts.append(a)
        elif age == 1:
            yest_arts.append(a)
        elif age <= MAX_AGE_DAYS:
            older_arts.append(a)

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
    """Merge new + still-fresh cached articles, then enforce the 50/50 day balance."""
    new_links = {a["link"] for a in new_articles}
    fresh_existing = [
        a for a in existing
        if a["link"] not in new_links and is_fresh(a.get("published", ""), now)
    ]
    return balance_by_day(new_articles + fresh_existing, now, MAX_KEEP)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    import os
    df = pd.read_excel(EXCEL_FILE)
    df = df[df["Company"].astype(str).str.strip() != "Company"]

    # Load existing cache to preserve still-fresh articles
    existing_cache: dict = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_cache = json.load(f)

    # Start from the existing cache and checkpoint after every company (not
    # once at the very end) — per-keyword querying means each company now
    # costs several requests instead of one, so a mid-run failure/timeout
    # should lose at most the in-flight company, not the whole run.
    news = dict(existing_cache)
    now  = datetime.now(timezone.utc)

    print()
    print("=" * 60)
    print("Fetching Google News  (last 7 days, per-keyword queries)")
    print("=" * 60)

    for _, row in df.iterrows():
        company = str(row["Company"]).strip()
        sector  = str(row.get("Sector", "")).strip()
        keywords = []

        kw_cell = row.get("Keywords", "")
        if not pd.isna(kw_cell) and str(kw_cell).strip():
            keywords = [x.strip() for x in str(kw_cell).split(",") if x.strip()]

        queries = build_queries(company, keywords)
        print(f"\n  {company}  ({len(queries)} queries)")

        try:
            new_articles, raw, kept = fetch_articles(company, keywords)
            print(f"  {raw} fetched → {kept} new")
        except Exception as e:
            print(f"  ERROR: {e}")
            new_articles = []

        existing_articles = existing_cache.get(company, {}).get("articles", [])
        articles = merge_articles(existing_articles, new_articles, now)
        print(f"  {len(articles)} total after merge + purge")

        news[company] = {
            "company":       company,
            "sector":        sector,
            "keywords":      keywords,
            "last_updated":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "article_count": len(articles),
            "articles":      articles,
        }

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(news, f, indent=4, ensure_ascii=False)

        time.sleep(1.5)

    total = sum(len(v["articles"]) for v in news.values())
    print()
    print("=" * 60)
    print(f"Done — {total} articles across {len(news)} companies")
    print(f"Saved → {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()

import base64
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jinja2 import Environment, FileSystemLoader

from net_utils import get_with_retry

ROOT = Path(__file__).resolve().parent.parent

MAX_NEWS_AGE_DAYS = 7   # keep articles up to 7 days old

NEWS_FILE          = ROOT / "data" / "news_cache.json"
EVENTS_FILE        = ROOT / "data" / "events.json"
CHINA_NEWS_FILE    = ROOT / "data" / "china_news_cache.json"
CHINA_EVENTS_FILE  = ROOT / "data" / "china_events.json"
BRAZIL_NEWS_FILE   = ROOT / "data" / "brazil_news_cache.json"
BRAZIL_EVENTS_FILE = ROOT / "data" / "brazil_events.json"
CREDIT_NEWS_FILE   = ROOT / "data" / "credit_news_cache.json"
GDELT_NEWS_FILE    = ROOT / "data" / "gdelt_news_cache.json"
FUEL_DATA_FILE     = ROOT / "data" / "fuel_data_cache.json"
PULP_PAPER_DATA_FILE = ROOT / "data" / "pulp_paper_cache.json"
MINING_DATA_FILE   = ROOT / "data" / "mining_cache.json"
OUTPUT_HTML        = ROOT / "dashboard.html"
TEMPLATE_FOLDER    = str(ROOT / "templates")
TEMPLATE_FILE      = "dashboard_template.html"
LOGO_FILE          = ROOT / "assets" / "itau_logo.png"


def _logo_data_uri() -> str:
    """Embed the logo inline so dashboard.html stays a single self-contained file."""
    if not LOGO_FILE.exists():
        return ""
    b64 = base64.b64encode(LOGO_FILE.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"

# ── China datasets shown on the Economic Data tab ───────────────────────────
# Each entry drives one chart card; data is fetched live by the browser.
# series[].key must match a field in the chinadata.live API response.
CHINA_DATASETS = [
    # Economy
    {"id": "china-gdp-growth",         "sector": "Economy",     "sectorColor": "#3b82f6",
     "title": "GDP Growth Rate",
     "series": [{"key": "value",   "label": "GDP Growth %",    "color": "#3b82f6"}],
     "chartType": "line"},
    {"id": "china-trade-monthly",       "sector": "Economy",     "sectorColor": "#3b82f6",
     "title": "Monthly Trade",
     "series": [{"key": "export",  "label": "Exports",         "color": "#10b981"},
                {"key": "import",  "label": "Imports",         "color": "#ef4444"},
                {"key": "balance", "label": "Balance",         "color": "#f59e0b"}],
     "chartType": "line"},
    {"id": "china-m2-money-supply",     "sector": "Economy",     "sectorColor": "#3b82f6",
     "title": "M2 Money Supply",
     "series": [{"key": "value",   "label": "M2",              "color": "#8b5cf6"}],
     "chartType": "line"},
    {"id": "china-rmb-usd-rate",        "sector": "Economy",     "sectorColor": "#3b82f6",
     "title": "RMB / USD Exchange Rate",
     "series": [{"key": "value",   "label": "RMB per USD",     "color": "#f59e0b"}],
     "chartType": "line"},
    {"id": "china-usa-brazil-trade",    "sector": "Economy",     "sectorColor": "#3b82f6",
     "title": "China vs USA: Trade with Brazil",
     "series": [{"key": "china",   "label": "China–Brazil",    "color": "#ef4444"},
                {"key": "usa",     "label": "USA–Brazil",      "color": "#3b82f6"}],
     "chartType": "line"},
    # Mining & Metals
    {"id": "steel-production-china-vs-world",       "sector": "Mining", "sectorColor": "#f59e0b",
     "title": "Steel Production: China vs World",
     "series": [{"key": "china",            "label": "China",         "color": "#ef4444"},
                {"key": "world_minus_china", "label": "Rest of World", "color": "#475569"}],
     "chartType": "bar"},
    {"id": "copper-production-china-vs-world",      "sector": "Mining", "sectorColor": "#f59e0b",
     "title": "Copper Production: China vs World",
     "series": [{"key": "china",            "label": "China",         "color": "#ef4444"},
                {"key": "world_minus_china", "label": "Rest of World", "color": "#475569"}],
     "chartType": "bar"},
    {"id": "rare-earth-production-china-vs-world",  "sector": "Mining", "sectorColor": "#f59e0b",
     "title": "Rare Earth Production: China vs World",
     "series": [{"key": "china",            "label": "China",         "color": "#ef4444"},
                {"key": "world_minus_china", "label": "Rest of World", "color": "#475569"}],
     "chartType": "bar"},
    # Pulp & Paper
    {"id": "paper-pulp-production-china-vs-world",  "sector": "Pulp & Paper", "sectorColor": "#10b981",
     "title": "Paper & Pulp Production: China vs World",
     "series": [{"key": "china",            "label": "China",         "color": "#ef4444"},
                {"key": "world_minus_china", "label": "Rest of World", "color": "#475569"}],
     "chartType": "bar"},
    # Energy
    {"id": "china-energy-consumption",              "sector": "Energy",  "sectorColor": "#f97316",
     "title": "Energy Consumption",
     "series": [{"key": "value",   "label": "Consumption",     "color": "#f97316"}],
     "chartType": "line"},
    {"id": "coal-consumption-china-vs-world",       "sector": "Energy",  "sectorColor": "#f97316",
     "title": "Coal Consumption: China vs World",
     "series": [{"key": "china",            "label": "China",         "color": "#ef4444"},
                {"key": "world_minus_china", "label": "Rest of World", "color": "#475569"}],
     "chartType": "bar"},
    # Agriculture
    {"id": "wheat-production-china-vs-world",       "sector": "Agriculture", "sectorColor": "#84cc16",
     "title": "Wheat Production: China vs World",
     "series": [{"key": "china",            "label": "China",         "color": "#ef4444"},
                {"key": "world_minus_china", "label": "Rest of World", "color": "#475569"}],
     "chartType": "bar"},
    {"id": "pork-production-china-vs-world",        "sector": "Agriculture", "sectorColor": "#84cc16",
     "title": "Pork Production: China vs World",
     "series": [{"key": "china",            "label": "China",         "color": "#ef4444"},
                {"key": "world_minus_china", "label": "Rest of World", "color": "#475569"}],
     "chartType": "bar"},
    # Industrial
    {"id": "china-car-production-monthly",          "sector": "Industrial", "sectorColor": "#94a3b8",
     "title": "Car Production (Monthly)",
     "series": [{"key": "value",   "label": "Units (10k)",     "color": "#94a3b8"}],
     "chartType": "bar"},
    {"id": "cement-production-china-vs-world",      "sector": "Industrial", "sectorColor": "#94a3b8",
     "title": "Cement Production: China vs World",
     "series": [{"key": "china",            "label": "China",         "color": "#ef4444"},
                {"key": "world_minus_china", "label": "Rest of World", "color": "#475569"}],
     "chartType": "bar"},
]

# ── Brazil macro datasets (BCB OLINDA API) ────────────────────────────────────
# bcb_series: SGS series ID from api.bcb.gov.br
# last_n: how many data points to fetch (daily series need more)
BRAZIL_DATASETS = [
    # Monetary Policy
    {"id": "br-selic",         "bcb_series": 432,   "last_n": 36,
     "sector": "Monetary Policy", "sectorColor": "#2563eb",
     "title": "SELIC Target Rate",        "unit": "%",
     "color": "#2563eb",  "chartType": "line"},
    # Inflation
    {"id": "br-ipca-mom",      "bcb_series": 433,   "last_n": 60,
     "sector": "Inflation", "sectorColor": "#dc2626",
     "title": "IPCA — Monthly Inflation", "unit": "% MoM",
     "color": "#dc2626",  "chartType": "bar"},
    {"id": "br-ipca-12m",      "bcb_series": 13522, "last_n": 60,
     "sector": "Inflation", "sectorColor": "#dc2626",
     "title": "IPCA — Accumulated 12M",   "unit": "%",
     "color": "#ef4444",  "chartType": "line"},
    {"id": "br-igpm",          "bcb_series": 4447,  "last_n": 60,
     "sector": "Inflation", "sectorColor": "#dc2626",
     "title": "IGP-M — Monthly",          "unit": "% MoM",
     "color": "#f97316",  "chartType": "bar"},
    # FX
    {"id": "br-brl-usd",       "bcb_series": 1,     "last_n": 252,
     "sector": "FX & Trade", "sectorColor": "#0891b2",
     "title": "BRL / USD (PTAX)",         "unit": "R$ per USD",
     "color": "#059669",  "chartType": "line"},
    # GDP & Activity
    {"id": "br-gdp",           "bcb_series": 4192,  "last_n": 20,
     "sector": "GDP & Activity", "sectorColor": "#16a34a",
     "title": "GDP — Nominal Level",      "unit": "R$ Million",
     "color": "#16a34a",  "chartType": "bar"},
    {"id": "br-industrial",    "bcb_series": 22099, "last_n": 60,
     "sector": "GDP & Activity", "sectorColor": "#16a34a",
     "title": "Industrial Production Index", "unit": "Index",
     "color": "#84cc16",  "chartType": "line"},
    {"id": "br-unemployment",  "bcb_series": 24369, "last_n": 30,
     "sector": "GDP & Activity", "sectorColor": "#16a34a",
     "title": "Unemployment Rate (PNAD)",  "unit": "%",
     "color": "#7c3aed",  "chartType": "line"},
    # Trade & External
    {"id": "br-trade",         "bcb_series": 22707, "last_n": 60,
     "sector": "FX & Trade", "sectorColor": "#0891b2",
     "title": "Trade Balance",            "unit": "USD Million",
     "color": "#0891b2",  "chartType": "bar"},
    {"id": "br-fx-reserves",   "bcb_series": 13621, "last_n": 60,
     "sector": "FX & Trade", "sectorColor": "#0891b2",
     "title": "International FX Reserves","unit": "USD Million",
     "color": "#06b6d4",  "chartType": "line"},
    # Money
    {"id": "br-m2",            "bcb_series": 27838, "last_n": 60,
     "sector": "Monetary Policy", "sectorColor": "#2563eb",
     "title": "M2 Money Supply Growth",   "unit": "% MoM",
     "color": "#8b5cf6",  "chartType": "bar"},
]

# ── Credit market datasets (BCB OLINDA) ───────────────────────────────────────
CREDIT_DATASETS = [
    # Interest rates
    {"id": "cr-rate-pj",      "bcb_series": 20786, "sector": "Interest Rates",  "sectorColor": "#7c3aed",
     "title": "Average Corporate Loan Rate",     "unit": "% p.a.", "color": "#7c3aed", "chartType": "line"},
    {"id": "cr-rate-giro",    "bcb_series": 20714, "sector": "Interest Rates",  "sectorColor": "#7c3aed",
     "title": "Working Capital Rate (PJ)",       "unit": "% p.a.", "color": "#8b5cf6", "chartType": "line"},
    # Spreads
    {"id": "cr-spread-giro",  "bcb_series": 20719, "sector": "Credit Spreads",  "sectorColor": "#dc2626",
     "title": "Working Capital Spread (PJ)",     "unit": "p.p.",   "color": "#dc2626", "chartType": "line"},
    # Delinquency
    {"id": "cr-default-pj",   "bcb_series": 21085, "sector": "Credit Quality",  "sectorColor": "#f59e0b",
     "title": "Corporate Delinquency Rate",      "unit": "%",      "color": "#f59e0b", "chartType": "line"},
    # CDB benchmark
    {"id": "cr-cdb",          "bcb_series": 25433, "sector": "Benchmark Rates", "sectorColor": "#059669",
     "title": "CDB Average Rate",                "unit": "% p.m.", "color": "#059669", "chartType": "line"},
]


BRASILIA_TZ = ZoneInfo("America/Sao_Paulo")


def parse_date(date_string):
    """Parse an RSS pubDate and convert to Brasília time for display.

    Feeds publish in UTC/GMT; every displayed clock time in this app
    (article "posted at", the header "Updated" stamp) should read in
    Brasília local time, not the feed's raw UTC. Elapsed-time deltas and
    day-bucketing elsewhere are unaffected — aware-datetime subtraction is
    correct regardless of which tzinfo is attached.
    """
    try:
        return parsedate_to_datetime(date_string).astimezone(BRASILIA_TZ)
    except Exception:
        return None


def elapsed_time(dt):
    if dt is None:
        return ""
    now     = datetime.now(timezone.utc)
    seconds = (now - dt).total_seconds()
    if seconds < 60:    return f"{int(seconds)} sec ago"
    if seconds < 3600:  return f"{int(seconds / 60)} min ago"
    if seconds < 86400: return f"{int(seconds / 3600)} h ago"
    return f"{int(seconds / 86400)} d ago"




def balance_by_day(articles: list, now_utc: datetime, max_keep: int) -> list:
    """Prefer 50% today / 50% yesterday; fall back to the rest of the
    MAX_NEWS_AGE_DAYS window when both are sparse. Uses the pre-parsed
    'datetime' field."""
    today_arts, yest_arts, older_arts = [], [], []
    for a in articles:
        dt = a.get("datetime")
        if dt is None:
            continue
        age = (now_utc - dt).days
        if age == 0:   today_arts.append(a)
        elif age == 1: yest_arts.append(a)
        elif age <= MAX_NEWS_AGE_DAYS: older_arts.append(a)
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

def flatten_news(news):
    rows = []
    companies, sectors, providers = set(), set(), set()
    now_utc = datetime.now(timezone.utc)
    for company, data in news.items():
        sector = data["sector"]
        companies.add(company)
        sectors.add(sector)
        for article in data["articles"]:
            dt = parse_date(article["published"])
            if dt is None or (now_utc - dt).days > MAX_NEWS_AGE_DAYS:
                continue  # hard gate: never show articles older than MAX_NEWS_AGE_DAYS
            provider = article.get("source", "Unknown")
            providers.add(provider)
            rows.append({
                "company":         company,
                "sector":          sector,
                "provider":        provider,
                "title":           article["title"],
                "link":            article["link"],
                "published":       article["published"],
                "published_short": dt.strftime("%b %d, %H:%M") if dt else "",
                "datetime":        dt,
                "elapsed":         elapsed_time(dt),
                "importance":      article.get("importance", "low"),
            })
    now_utc = datetime.now(timezone.utc)
    rows.sort(
        key=lambda x: x["datetime"] if x["datetime"] else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    rows = balance_by_day(rows, now_utc, 500)  # enforce 50/50 across full feed
    return rows, sorted(companies), sorted(sectors), sorted(providers)


def load_events():
    if not os.path.exists(EVENTS_FILE):
        return [], []
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    today  = date.today()
    events = []
    for ev in raw:
        try:
            ev_date = date.fromisoformat(ev["date"])
        except (KeyError, ValueError):
            continue
        days = (ev_date - today).days
        if days < -7:
            continue
        if days < 0:       countdown = f"{abs(days)}d ago"
        elif days == 0:    countdown = "Today"
        elif days == 1:    countdown = "Tomorrow"
        else:              countdown = f"In {days}d"
        events.append({
            "date":        ev["date"],
            "date_obj":    ev_date,
            "month_label": ev_date.strftime("%B %Y").upper(),
            "type":        ev.get("type", "other"),
            "company":     ev.get("company") or "",
            "sector":      ev.get("sector") or "",
            "title":       ev.get("title", ""),
            "description": ev.get("description", ""),
            "days":        days,
            "countdown":   countdown,
            "is_past":     days < 0,
            "is_today":    days == 0,
            "is_soon":     0 <= days <= 7,
        })
    events.sort(key=lambda x: x["date_obj"])
    months = {}
    for ev in events:
        months.setdefault(ev["month_label"], []).append(ev)
    return events, list(months.items())


def load_china_news():
    """Returns (china_news dict for sidebar colors, flat sorted article list)."""
    if not os.path.exists(CHINA_NEWS_FILE):
        return {}, []
    with open(CHINA_NEWS_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)

    rows = []
    now_utc = datetime.now(timezone.utc)
    for sector_name, data in cache.items():
        for article in data["articles"]:
            dt = parse_date(article.get("published", ""))
            if dt is None or (now_utc - dt).days > MAX_NEWS_AGE_DAYS:
                continue  # hard gate: never show articles older than MAX_NEWS_AGE_DAYS
            rows.append({
                "sector":          sector_name,
                "color":           data["color"],
                "title":           article["title"],
                "link":            article["link"],
                "published":       article.get("published", ""),
                "published_short": dt.strftime("%b %d, %H:%M") if dt else "",
                "source":          article.get("source", ""),
                "datetime":        dt,
                "elapsed":         elapsed_time(dt),
                "importance":      article.get("importance", "low"),
            })

    now_utc = datetime.now(timezone.utc)
    rows.sort(
        key=lambda x: x["datetime"] if x["datetime"] else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    rows = balance_by_day(rows, now_utc, 500)  # enforce 50/50 across full feed
    return cache, rows


def load_china_events():
    if not os.path.exists(CHINA_EVENTS_FILE):
        return [], []
    with open(CHINA_EVENTS_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    today  = date.today()
    events = []
    for ev in raw:
        try:
            ev_date = date.fromisoformat(ev["date"])
        except (KeyError, ValueError):
            continue
        days = (ev_date - today).days
        if days < -7:
            continue
        if days < 0:    countdown = f"{abs(days)}d ago"
        elif days == 0: countdown = "Today"
        elif days == 1: countdown = "Tomorrow"
        else:           countdown = f"In {days}d"
        events.append({
            "date":        ev["date"],
            "date_obj":    ev_date,
            "month_label": ev_date.strftime("%B %Y").upper(),
            "type":        ev.get("type", "other"),
            "sector":      ev.get("sector", ""),
            "title":       ev.get("title", ""),
            "description": ev.get("description", ""),
            "days":        days,
            "countdown":   countdown,
            "is_past":     days < 0,
            "is_today":    days == 0,
            "is_soon":     0 <= days <= 7,
        })
    events.sort(key=lambda x: x["date_obj"])
    months = {}
    for ev in events:
        months.setdefault(ev["month_label"], []).append(ev)
    return events, list(months.items())


CHINADATA_BASE = "https://chinadata.live/api/v2"
CHINADATA_HDR  = {"User-Agent": "Mozilla/5.0 (compatible; dashboard-generator/1.0)"}


def fetch_china_charts() -> dict:
    """Pre-fetch all chart dataset values so charts render without browser API calls."""
    result = {}
    for ds in CHINA_DATASETS:
        ds_id = ds["id"]
        try:
            r = get_with_retry(f"{CHINADATA_BASE}/data/{ds_id}",
                                headers=CHINADATA_HDR, timeout=15)
            result[ds_id] = r.json()["data"]
            print(f"  {ds_id}: {len(result[ds_id]['data'])} pts")
        except Exception as e:
            print(f"  {ds_id}: FAILED — {e}")
    return result


def fetch_china_catalog() -> list:
    """Fetch full list of all datasets available on chinadata.live."""
    try:
        r = get_with_retry(f"{CHINADATA_BASE}/datasets",
                            headers=CHINADATA_HDR, timeout=15)
        catalog = r.json()["data"]
        print(f"  {len(catalog)} datasets in catalog")
        return catalog
    except Exception as e:
        print(f"  Catalog fetch failed — {e}")
        return []


BCB_BASE = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series}/dados?dataInicial={start}&dataFinal={end}&formato=json"
BCB_HDR  = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def load_brazil_news():
    """Returns (cache dict for sidebar colours, flat sorted article list)."""
    if not os.path.exists(BRAZIL_NEWS_FILE):
        return {}, []
    with open(BRAZIL_NEWS_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)
    rows   = []
    now_utc = datetime.now(timezone.utc)
    for sector_name, data in cache.items():
        for article in data["articles"]:
            dt = parse_date(article.get("published", ""))
            if dt is None or (now_utc - dt).days > MAX_NEWS_AGE_DAYS:
                continue
            rows.append({
                "sector":          sector_name,
                "color":           data["color"],
                "title":           article["title"],
                "link":            article["link"],
                "published":       article.get("published", ""),
                "published_short": dt.strftime("%b %d, %H:%M") if dt else "",
                "source":          article.get("source", ""),
                "datetime":        dt,
                "elapsed":         elapsed_time(dt),
                "importance":      article.get("importance", "low"),
            })
    now_utc = datetime.now(timezone.utc)
    rows.sort(
        key=lambda x: x["datetime"] if x["datetime"] else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    rows = balance_by_day(rows, now_utc, 500)  # enforce 50/50 across full feed
    return cache, rows


def load_brazil_events():
    if not os.path.exists(BRAZIL_EVENTS_FILE):
        return [], []
    with open(BRAZIL_EVENTS_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    today  = date.today()
    events = []
    for ev in raw:
        try:
            ev_date = date.fromisoformat(ev["date"])
        except (KeyError, ValueError):
            continue
        days = (ev_date - today).days
        if days < -7:
            continue
        if days < 0:    countdown = f"{abs(days)}d ago"
        elif days == 0: countdown = "Today"
        elif days == 1: countdown = "Tomorrow"
        else:           countdown = f"In {days}d"
        events.append({
            "date":        ev["date"],
            "date_obj":    ev_date,
            "month_label": ev_date.strftime("%B %Y").upper(),
            "type":        ev.get("type", "other"),
            "sector":      ev.get("sector", ""),
            "title":       ev.get("title", ""),
            "description": ev.get("description", ""),
            "days":        days,
            "countdown":   countdown,
            "is_past":     days < 0,
            "is_today":    days == 0,
            "is_soon":     0 <= days <= 7,
        })
    events.sort(key=lambda x: x["date_obj"])
    months = {}
    for ev in events:
        months.setdefault(ev["month_label"], []).append(ev)
    return events, list(months.items())


def load_credit_news():
    """Returns (cache dict for sidebar colours, flat sorted article list)."""
    if not os.path.exists(CREDIT_NEWS_FILE):
        return {}, []
    with open(CREDIT_NEWS_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)
    rows   = []
    now_utc = datetime.now(timezone.utc)
    for sector_name, data in cache.items():
        for article in data["articles"]:
            dt = parse_date(article.get("published", ""))
            if dt is None or (now_utc - dt).days > MAX_NEWS_AGE_DAYS:
                continue
            rows.append({
                "sector":          sector_name,
                "color":           data["color"],
                "title":           article["title"],
                "link":            article["link"],
                "published":       article.get("published", ""),
                "published_short": dt.strftime("%b %d, %H:%M") if dt else "",
                "source":          article.get("source", ""),
                "datetime":        dt,
                "elapsed":         elapsed_time(dt),
                "importance":      article.get("importance", "low"),
            })
    now_utc = datetime.now(timezone.utc)
    rows.sort(
        key=lambda x: x["datetime"] if x["datetime"] else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    rows = balance_by_day(rows, now_utc, 500)  # enforce 50/50 across full feed
    return cache, rows


def load_unified_news():
    """Single feed combining Portfolio + Brazil + China + Credit + GDELT news.

    Every row gets a `country` tag (Brazil for Portfolio/Brazil/Credit —
    portfolio companies are all B3-listed and credit news is the Brazilian
    credit market; China for China) and an `origin` tag (which source it
    came from, used for badge coloring since only Brazil/China/Credit carry
    a per-sector color already). Sorted strictly by recency, matching every
    prior per-source feed — importance is shown per-card, never used to
    resort.
    """
    portfolio_rows, companies = [], []
    if os.path.exists(NEWS_FILE):
        with open(NEWS_FILE, "r", encoding="utf-8") as f:
            portfolio_raw = json.load(f)
        portfolio_rows, companies, _, _ = flatten_news(portfolio_raw)
    for r in portfolio_rows:
        r["source"]  = r.pop("provider")
        r["country"] = "Brazil"
        r["origin"]  = "portfolio"

    _, brazil_rows = load_brazil_news()
    for r in brazil_rows:
        r["country"] = "Brazil"
        r["origin"]  = "brazil"
        r["company"] = None

    _, china_rows = load_china_news()
    for r in china_rows:
        r["country"] = "China"
        r["origin"]  = "china"
        r["company"] = None

    _, credit_rows = load_credit_news()
    for r in credit_rows:
        r["country"] = "Brazil"
        r["origin"]  = "credit"
        r["company"] = None

    gdelt_rows = []
    if os.path.exists(GDELT_NEWS_FILE):
        with open(GDELT_NEWS_FILE, "r", encoding="utf-8") as f:
            gdelt_raw = json.load(f)
        gdelt_rows, _, _, _ = flatten_news(gdelt_raw)
    for r in gdelt_rows:
        r["source"]  = r.pop("provider")
        r["country"] = "Brazil"
        r["origin"]  = "gdelt"

    rows = portfolio_rows + brazil_rows + china_rows + credit_rows + gdelt_rows
    rows.sort(
        key=lambda x: x["datetime"] if x["datetime"] else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    sectors = sorted({r["sector"] for r in rows if r.get("sector")})
    sources = sorted({r["source"] for r in rows if r.get("source")})
    return rows, ["Brazil", "China"], sectors, sorted(companies), sources


def load_unified_events():
    """Single Calendar feed combining Brazil + China events, with Portfolio
    (Cockpit) earnings/COPOM events folded into the Brazil bucket. Portfolio
    events that duplicate a Brazil event on the same (date, title) are
    dropped rather than shown twice — this happens for COPOM, which both
    events_generator.py and brazil_events_generator.py independently
    schedule with identical titles ("COPOM – Reunião (1º dia)" /
    "COPOM – Decisão da Selic") for the same dates.
    """
    brazil_events, _    = load_brazil_events()
    china_events, _     = load_china_events()
    portfolio_events, _ = load_events()

    for ev in brazil_events:
        ev["country"] = "Brazil"
    for ev in china_events:
        ev["country"] = "China"

    brazil_keys = {(ev["date"], ev["title"]) for ev in brazil_events}
    for ev in portfolio_events:
        if (ev["date"], ev["title"]) in brazil_keys:
            continue  # duplicate COPOM entry — Brazil's generator already has it
        ev["country"] = "Brazil"
        brazil_events.append(ev)

    events = brazil_events + china_events
    events.sort(key=lambda x: x["date_obj"])
    months = {}
    for ev in events:
        months.setdefault(ev["month_label"], []).append(ev)
    return events, list(months.items())


def fetch_brazil_charts() -> dict:
    """Pre-fetch BCB OLINDA macro series and normalise to {date, value} format."""
    import time as _time
    from datetime import date as _date

    result = {}
    today     = _date.today()
    today_str = today.strftime("%d/%m/%Y")

    # How far back each dataset goes (years)
    LOOKBACK = {
        "br-brl-usd": 1,       # daily — 1 year is plenty
        "br-fx-reserves": 2,
        "default": 5,
    }

    for ds in BRAZIL_DATASETS:
        ds_id     = ds["id"]
        series_id = ds["bcb_series"]
        years     = LOOKBACK.get(ds_id, LOOKBACK["default"])

        start_date = _date(today.year - years, today.month, 1)
        start_str  = start_date.strftime("%d/%m/%Y")

        url = BCB_BASE.format(series=series_id, start=start_str, end=today_str)
        try:
            r = get_with_retry(url, headers=BCB_HDR, timeout=20)
            raw = r.json()
            points = []
            for pt in raw:
                try:
                    d_parts  = pt["data"].split("/")     # DD/MM/YYYY
                    date_iso = f"{d_parts[2]}-{d_parts[1]}-{d_parts[0]}"
                    value    = float(pt["valor"].replace(",", "."))
                    points.append({"date": date_iso, "value": value})
                except (ValueError, KeyError, IndexError):
                    continue
            result[ds_id] = {
                "title":  ds["title"],
                "unit":   ds["unit"],
                "source": "Banco Central do Brasil (BCB / OLINDA)",
                "data":   points,
            }
            print(f"  {ds_id}: {len(points)} pts")
        except Exception as e:
            print(f"  {ds_id}: FAILED — {e}")
        _time.sleep(0.5)   # gentle rate-limit avoidance
    return result


def fetch_credit_charts() -> dict:
    """Fetch BCB credit market series — same normalisation as Brazil charts."""
    import time as _time
    from datetime import date as _date

    result    = {}
    today     = _date.today()
    today_str = today.strftime("%d/%m/%Y")
    start_str = _date(today.year - 5, today.month, 1).strftime("%d/%m/%Y")

    for ds in CREDIT_DATASETS:
        ds_id     = ds["id"]
        series_id = ds["bcb_series"]
        url = BCB_BASE.format(series=series_id, start=start_str, end=today_str)
        try:
            r = get_with_retry(url, headers=BCB_HDR, timeout=20)
            raw    = r.json()
            points = []
            for pt in raw:
                try:
                    d_parts  = pt["data"].split("/")
                    date_iso = f"{d_parts[2]}-{d_parts[1]}-{d_parts[0]}"
                    value    = float(pt["valor"].replace(",", "."))
                    points.append({"date": date_iso, "value": value})
                except (ValueError, KeyError, IndexError):
                    continue
            result[ds_id] = {
                "title":  ds["title"],
                "unit":   ds["unit"],
                "source": "Banco Central do Brasil (BCB / OLINDA)",
                "data":   points,
            }
            print(f"  {ds_id}: {len(points)} pts")
        except Exception as e:
            print(f"  {ds_id}: FAILED — {e}")
        _time.sleep(0.5)
    return result


# ── Cockpit market data ───────────────────────────────────────────────────────
COCKPIT_GROUPS = [
    ("Equities",       ["sp500",   "ibov"]),
    ("Interest Rates", ["us10y",   "br10y"]),
    # Keys and labels both read base/quote = "how much QUOTE per 1 BASE",
    # matching each ticker's real direction (e.g. usd_brl from USDBRL=X,
    # which yfinance quotes as BRL-per-USD) — usd_brl was previously keyed
    # "brl_usd" and labeled "BRL / USD", backwards from what it measured.
    ("Exchange Rates", ["usd_brl", "usd_cny", "brl_cny", "eur_brl"]),
    ("Commodities",    ["brent", "iron_ore", "nat_gas", "diesel", "gasoline",
                         "gold", "silver", "copper", "aluminum", "nickel"]),
]


def _ck_make(label, unit, color, series, show_chart=True, source="Yahoo Finance"):
    """Build a cockpit item dict with pre-formatted current value and change.

    `source` is shown in the UI as a hover tooltip on every card (see
    templates/dashboard_template.html's ICON_INFO) — pass the *actual*
    provider used, not a nominal default, when a fetch has a primary/fallback
    path (e.g. Iron Ore, Aluminum: Yahoo Finance first, FRED if that's
    empty). Never leave this as a guess; call sites determine it from which
    fetch path actually returned data.
    """
    cur  = series[-1]["y"] if series else None
    prev = series[-2]["y"] if len(series) >= 2 else None
    if cur is None:
        return {"label": label, "unit": unit, "color": color, "show_chart": show_chart,
                "source": source,
                "current_fmt": "N/A", "change_fmt": "", "change_dir": "", "data": []}
    if unit == "pts":
        cur_fmt = f"{cur:,.0f}"
    elif unit == "%":
        cur_fmt = f"{cur:.2f}%"
    else:
        cur_fmt = f"{cur:,.2f}"
    if prev and prev != 0:
        if unit == "%":
            raw = (cur - prev) * 100          # basis points
            chg_fmt = f"{'+' if raw >= 0 else ''}{raw:.1f} bps"
        else:
            pct = (cur - prev) / abs(prev) * 100
            chg_fmt = f"{'+' if pct >= 0 else ''}{pct:.2f}%"
        chg_dir = "pos" if cur >= prev else "neg"
    else:
        chg_fmt, chg_dir = "", ""
    return {"label": label, "unit": unit, "color": color, "show_chart": show_chart,
            "source": source,
            "current_fmt": cur_fmt, "change_fmt": chg_fmt,
            "change_dir": chg_dir, "data": series}


def _ck_yf(ticker, period="1y"):
    """Download one Yahoo Finance ticker and return a list of {x, y} dicts."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, interval="1d",
                         auto_adjust=True, progress=False)
        if df.empty:
            return []
        col = df["Close"]
        if hasattr(col, "columns"):          # MultiIndex edge case
            col = col.iloc[:, 0]
        col = col.dropna()
        pts = []
        for idx, v in zip(col.index, col.values):
            try:
                fv = float(v)
                if fv == fv:                 # exclude NaN
                    pts.append({"x": str(idx.date()), "y": round(fv, 4)})
            except (TypeError, ValueError):
                pass
        return pts
    except Exception as e:
        print(f"    {ticker}: {e}")
        return []


def _ck_fred(series_id, max_pts=60, timeout=15):
    """Fetch a FRED series via its public CSV endpoint (no API key needed).

    Deliberately does NOT use get_with_retry's default browser-spoofed
    User-Agent (core.net_utils.DEFAULT_HEADERS, needed elsewhere to get past
    Google News' bot detection) — confirmed live that fred.stlouisfed.org's
    fredgraph.csv endpoint reliably *hangs to timeout* on that Chrome UA
    (two separate get_with_retry attempts, ~58s total, both timing out) while
    responding in under 1s to a plain/no-spoof User-Agent on the identical
    URL. This isn't network flakiness — it's deterministic per-UA behavior,
    most likely a CDN/WAF treating an unexecuted "browser" request as
    suspicious. Passing an explicit non-browser UA here avoids it; retries
    still exist for genuine transient failures, but no longer need inflating
    to compensate for a UA-triggered hang.
    """
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        r = get_with_retry(url, headers={"User-Agent": "python-requests (dashboard-generator)"},
                            timeout=timeout)
        lines = r.text.strip().split("\n")
        pts = []
        for line in lines[1:]:
            p = line.split(",")
            if len(p) != 2 or p[1].strip() in (".", ""):
                continue
            try:
                pts.append({"x": p[0].strip(), "y": round(float(p[1].strip()), 2)})
            except ValueError:
                pass
        return pts[-max_pts:]
    except Exception as e:
        print(f"    FRED {series_id}: {e}")
        return []


ANBIMA_ETTJ_URL     = "https://www.anbima.com.br/informacoes/est-termo/CZ-down.asp"
ANBIMA_HDR          = {"User-Agent": "Mozilla/5.0"}
BR_ETTJ_HISTORY_FILE = ROOT / "data" / "br_ettj_history.json"
TEN_YEAR_BDAYS      = 2520   # 252 business days/year * 10 — ANBIMA's vertex grid uses business days


def _parse_anbima_ettj_pref(text: str, vertex_target: int = TEN_YEAR_BDAYS):
    """Parse ANBIMA's CZ-down.asp response and return the nominal (PREFIXADOS)
    yield, in percent, at the vertex closest to vertex_target business days.

    Response is a semicolon-delimited Latin-1-ish text file: a few header
    rows of NSS model parameters, then a vertex table:
      Vertices;ETTJ IPCA;ETTJ PREF;Inflação Implícita
      2.520;7,8062;14,4448;6,1579
    Numbers use Brazilian formatting (comma decimal, dot thousands).
    """
    lines = text.strip().splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("Vertices;"):
            start = i + 1
            break
    if start is None:
        return None

    best, best_diff = None, None
    for line in lines[start:]:
        parts = line.split(";")
        if len(parts) < 3:
            continue
        try:
            vertex = int(parts[0].replace(".", ""))
            pref_str = parts[2].strip()
            if not pref_str:
                continue
            pref = float(pref_str.replace(".", "").replace(",", "."))
        except (ValueError, IndexError):
            continue
        diff = abs(vertex - vertex_target)
        if best_diff is None or diff < best_diff:
            best_diff, best = diff, pref
    return best


def _fetch_anbima_ettj_for_date(d: date):
    """One day's Brazil 10Y nominal (prefixada) yield from ANBIMA's daily
    ETTJ curve. Returns None on weekends/holidays (no curve published) or
    once past ANBIMA's free retention window (~a few months back)."""
    url = f"{ANBIMA_ETTJ_URL}?Dt_Ref={d.strftime('%d/%m/%Y')}"
    try:
        r = get_with_retry(url, headers=ANBIMA_HDR, timeout=15, retries=1)
        return _parse_anbima_ettj_pref(r.text)
    except Exception:
        return None


def _load_ettj_history():
    if BR_ETTJ_HISTORY_FILE.exists():
        try:
            with open(BR_ETTJ_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def fetch_br10y():
    """Brazil 10Y nominal government bond yield — real market-derived rate
    from ANBIMA's daily ETTJ (Estrutura a Termo das Taxas de Juros) curve,
    not the Selic policy rate and not a proxy. ANBIMA's free download only
    serves one date per request, so a local history file is accumulated
    across runs (same merge/append pattern the news scrapers use): backfill
    a short window on first run, then append just the newest day going
    forward. ANBIMA's own retention for this free endpoint is only a few
    months, so a longer backfill wouldn't return anything anyway.
    """
    history   = _load_ettj_history()
    have_dates = {p["x"] for p in history}
    today_    = date.today()

    if not history:
        print("      backfilling Brazil ETTJ history (first run)...")
        for offset in range(60, -1, -1):
            d = today_ - timedelta(days=offset)
            if d.weekday() >= 5:   # skip weekends — no curve published
                continue
            date_key = d.isoformat()
            if date_key in have_dates:
                continue
            rate = _fetch_anbima_ettj_for_date(d)
            if rate is not None:
                history.append({"x": date_key, "y": rate})
                have_dates.add(date_key)
    else:
        for d in (today_, today_ - timedelta(days=1)):
            date_key = d.isoformat()
            if date_key in have_dates:
                continue
            rate = _fetch_anbima_ettj_for_date(d)
            if rate is not None:
                history.append({"x": date_key, "y": rate})
                have_dates.add(date_key)

    history.sort(key=lambda p: p["x"])
    history = history[-400:]
    with open(BR_ETTJ_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    return history


def fetch_cockpit_data():
    import time as _time

    print("  Fetching cockpit market data...")
    data = {}

    yf_map = [
        ("sp500",   "^GSPC",    "S&P 500",          "pts",     "#3b82f6", True),
        ("ibov",    "^BVSP",    "IBOVESPA",          "pts",     "#16a34a", True),
        ("us10y",   "^TNX",     "US 10Y Treasury",   "%",       "#ef4444", True),
        ("usd_brl", "USDBRL=X", "USD / BRL",         "BRL",     "#8b5cf6", False),
        ("usd_cny", "USDCNY=X", "USD / CNY",         "CNY",     "#f59e0b", False),
        ("eur_brl", "EURBRL=X", "EUR / BRL",         "BRL",     "#0284c7", False),
        ("brent",   "BZ=F",     "Brent Crude",       "USD/bbl", "#1e293b", True),
    ]
    for key, ticker, label, unit, color, show_chart in yf_map:
        print(f"    {label}...")
        data[key] = _ck_make(label, unit, color, _ck_yf(ticker), show_chart=show_chart)
        _time.sleep(0.3)

    # BRL/CNY: direct ticker, then compute from USDBRL + USDCNY as fallback
    print("    BRL / CNY...")
    brlcny = _ck_yf("BRLCNY=X")
    if not brlcny:
        brl_s = {p["x"]: p["y"] for p in data["usd_brl"]["data"]}
        cny_s = {p["x"]: p["y"] for p in data["usd_cny"]["data"]}
        brlcny = [{"x": d, "y": round(cny_s[d] / brl_s[d], 4)}
                  for d in sorted(set(brl_s) & set(cny_s))
                  if brl_s[d] and brl_s[d] != 0]
    data["brl_cny"] = _ck_make("BRL / CNY", "CNY", "#06b6d4", brlcny, show_chart=False)

    # Brazil 10Y nominal yield via ANBIMA ETTJ (real curve, not Selic)
    print("    Brazil 10Y Treasury (ANBIMA ETTJ)...")
    data["br10y"] = _ck_make("Brazil 10Y Treasury", "%", "#d97706", fetch_br10y(),
                              source="ANBIMA (ETTJ curve)")

    # Iron ore: yfinance TIO=F, then FRED PIORECRUSD as fallback — source
    # label reflects whichever path actually returned data, never a guess.
    print("    Iron Ore...")
    iron, iron_source = _ck_yf("TIO=F"), "Yahoo Finance"
    if not iron:
        iron, iron_source = _ck_fred("PIORECRUSD", max_pts=52), "FRED"
    data["iron_ore"] = _ck_make("Iron Ore 62% Fe", "USD/t", "#92400e", iron, source=iron_source)

    # ── Additional commodities ───────────────────────────────────────────
    # All verified live before wiring in (see CLAUDE.md §4) — no ticker here
    # was guessed. Diesel and Gasoline use the standard commodity-market
    # benchmark contracts for those fuels (NY Harbor ULSD and RBOB
    # respectively), not a literal "diesel" or "gasoline" ticker, because
    # those don't exist as such — labeled honestly, same spirit as the old
    # Suzano/Klabin pulp proxies this replaces, just for genuinely
    # correlated fuel grades rather than an unrelated equity.
    commodity_map = [
        ("nat_gas",  "NG=F",  "Natural Gas (Henry Hub)",   "USD/MMBtu", "#0ea5e9"),
        ("diesel",   "HO=F",  "Diesel (NY Harbor ULSD)",   "USD/gal",   "#a16207"),
        ("gasoline", "RB=F",  "Gasoline (RBOB)",           "USD/gal",   "#ea580c"),
        ("gold",     "GC=F",  "Gold",                      "USD/oz",    "#ca8a04"),
        ("silver",   "SI=F",  "Silver",                    "USD/oz",    "#94a3b8"),
        ("copper",   "HG=F",  "Copper",                    "USD/lb",    "#c2410c"),
    ]
    for key, ticker, label, unit, color in commodity_map:
        print(f"    {label}...")
        data[key] = _ck_make(label, unit, color, _ck_yf(ticker))
        _time.sleep(0.3)

    # Aluminum: yfinance COMEX future first (confirmed real — "Aluminum
    # Futures" on CMX, not a mislabeled/adjacent ticker), FRED as fallback.
    print("    Aluminum...")
    alum, alum_source = _ck_yf("ALI=F"), "Yahoo Finance"
    if not alum:
        alum, alum_source = _ck_fred("PALUMUSDM", max_pts=60), "FRED"
    data["aluminum"] = _ck_make("Aluminum", "USD/t", "#64748b", alum, source=alum_source)

    # Nickel: no free/clean Yahoo Finance ticker exists (checked LNI=F, NI=F,
    # NID=F, NICKEL — all delisted/404). FRED's monthly global price series
    # is the only real option; labeled as monthly since that's a materially
    # different cadence from every other daily card here.
    print("    Nickel (FRED, monthly)...")
    data["nickel"] = _ck_make("Nickel", "USD/t", "#0f766e", _ck_fred("PNICKUSDM", max_pts=36),
                               source="FRED (monthly)")

    # Jet Fuel: deliberately not included. Yahoo Finance's only candidate
    # ticker, JET=F, is not an outright price — it oscillates near zero
    # (-0.48 to +0.50 over 6 months, confirmed by pulling the full series),
    # meaning it's a differential/crack-spread contract, not a jet fuel
    # price. No other free source was found. Rather than show a permanent
    # "N/A" card (which reads as broken) or a mislabeled/duplicated number,
    # the metric is simply omitted — see CLAUDE.md §4/§8 if a real source
    # ever turns up.

    return data


# ── Pulp & Paper: public-company performance ────────────────────────────────
# FAOSTAT (below) is annual and necessarily ~1 year behind — there is no
# monthly/quarterly version of that dataset, industry-wide. This is the
# recency complement: live daily share prices for the major publicly-traded
# pulp & paper companies, via the same yfinance path Cockpit already uses.
# Every ticker below was verified live (yf.Ticker().info's shortName/
# quoteType/currency, not assumed) before being wired in — same standard as
# the Cockpit commodities audit (CLAUDE.md §4): TSX-listed Canfor Pulp
# Products (CFX.TO) was dropped after its live lookup returned no data at
# all (name=None) — confirmed delisted, it was taken private in 2023, not a
# transient fetch failure.
PULP_COMPANIES = [
    ("SUZB3.SA",  "Suzano",                    "Brazil"),
    ("KLBN11.SA", "Klabin",                    "Brazil"),
    ("IP",        "International Paper",       "United States"),
    ("SW",        "Smurfit WestRock",          "Ireland / United States"),
    ("UPM.HE",    "UPM-Kymmene",               "Finland"),
    ("STERV.HE",  "Stora Enso",                "Finland"),
    ("MNDI.L",    "Mondi",                     "UK / South Africa"),
    ("2689.HK",   "Nine Dragons Paper",        "China"),
    ("3861.T",    "Oji Holdings",              "Japan"),
    ("3863.T",    "Nippon Paper Industries",   "Japan"),
    ("SPPJY",     "Sappi",                     "South Africa"),
    ("SON",       "Sonoco Products",           "United States"),
    ("PKG",       "Packaging Corp of America", "United States"),
    ("METSB.HE",  "Metsä Board",               "Finland"),
]


def fetch_pulp_companies() -> list:
    import time as _time
    print("  Fetching pulp & paper public-company performance...")
    result = []
    for ticker, name, country in PULP_COMPANIES:
        series = _ck_yf(ticker, period="6mo")
        item = _ck_make(name, "", "#2563eb", series, show_chart=True, source="Yahoo Finance")
        item["ticker"] = ticker
        item["country"] = country
        result.append(item)
        print(f"    {name} ({ticker}): {item['current_fmt'] or 'N/A'}")
        _time.sleep(0.3)
    return result


# ── Mining: public-company performance ──────────────────────────────────────
# Same recency-complement role as PULP_COMPANIES: BGS's own data (below) is
# annual, this is the live daily layer on top of it. Every ticker verified
# live before wiring in — same standard as Pulp & Paper's audit. Caught one
# real mislabel this way: the obvious "GOLD" ticker resolves to an unrelated
# company ("Gold.com, Inc."), not Barrick — Barrick Mining Corp (formerly
# Barrick Gold) trades as "B" on the NYSE since its 2025 rename, confirmed
# via yf.Ticker().info's shortName, not assumed from the old familiar symbol.
MINING_COMPANIES = [
    ("VALE",        "Vale",                      "Brazil"),
    ("BHP",         "BHP Group",                 "Australia"),
    ("RIO",         "Rio Tinto",                 "UK / Australia"),
    ("GLEN.L",      "Glencore",                  "Switzerland / UK"),
    ("FCX",         "Freeport-McMoRan",          "United States"),
    ("AAL.L",       "Anglo American",            "UK / South Africa"),
    ("NEM",         "Newmont",                   "United States"),
    ("B",           "Barrick Mining",            "Canada"),
    ("SCCO",        "Southern Copper",           "United States / Peru"),
    ("TECK",        "Teck Resources",            "Canada"),
    ("FMG.AX",      "Fortescue",                 "Australia"),
    ("GMEXICOB.MX", "Grupo Mexico",              "Mexico"),
    ("AA",          "Alcoa",                     "United States"),
    ("MT",          "ArcelorMittal",             "Luxembourg"),
    ("CLF",         "Cleveland-Cliffs",          "United States"),
]


def fetch_mining_companies() -> list:
    import time as _time
    print("  Fetching mining public-company performance...")
    result = []
    for ticker, name, country in MINING_COMPANIES:
        series = _ck_yf(ticker, period="6mo")
        item = _ck_make(name, "", "#f59e0b", series, show_chart=True, source="Yahoo Finance")
        item["ticker"] = ticker
        item["country"] = country
        result.append(item)
        print(f"    {name} ({ticker}): {item['current_fmt'] or 'N/A'}")
        _time.sleep(0.3)
    return result


def load_mining_data() -> dict:
    """Reads the BGS mining snapshot written by scrapers/mining_scraper.py.

    Same fault-isolation as load_pulp_paper_data(): honest all-empty shape
    if the step hasn't run yet or failed.
    """
    empty = {"years": [], "world": {}, "countries": {}, "concentration": {}, "items": {}}
    if not os.path.exists(MINING_DATA_FILE):
        return empty
    with open(MINING_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_fuel_data() -> dict:
    """Reads the ANP fuel-distribution snapshot written by scrapers/fuel_scraper.py.

    That fetch runs as its own step in update_dashboard.py, fault-isolated
    the same way every news scraper/event generator is — if it fails or
    hasn't run yet, the Energy sub-page keeps whatever was cached last
    rather than breaking the whole build.
    """
    empty = {"E": [], "P": [], "R": [], "M": [], "T": {}, "G": {}, "K": {}}
    if not os.path.exists(FUEL_DATA_FILE):
        return empty
    with open(FUEL_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_pulp_paper_data() -> dict:
    """Reads the FAOSTAT pulp/paper snapshot written by
    scrapers/pulp_paper_scraper.py.

    Same fault-isolation as load_fuel_data(): this fetch runs as its own
    step in update_dashboard.py, and an honest all-empty shape is returned
    if it hasn't run yet or failed, rather than breaking the whole build.
    """
    empty = {"years": [], "world": {}, "countries": {}, "concentration": {},
              "recycled_rate": {"world": {}, "countries": {}},
              "structural_mix": {"pulp_by_type": {}, "paper_by_type": {}},
              "items": {}}
    if not os.path.exists(PULP_PAPER_DATA_FILE):
        return empty
    with open(PULP_PAPER_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def render(unified_news, news_countries, news_sectors, news_companies, news_sources,
           unified_events, event_months,
           china_charts, china_catalog,
           brazil_charts,
           credit_charts,
           cockpit,
           fuel_data,
           pulp_paper_data, pulp_news, pulp_companies,
           mining_data, mining_news, mining_companies):
    env      = Environment(loader=FileSystemLoader(TEMPLATE_FOLDER))
    template = env.get_template(TEMPLATE_FILE)
    html = template.render(
        # Cockpit
        cockpit=cockpit,
        cockpit_groups=COCKPIT_GROUPS,
        cockpit_json=json.dumps(cockpit),
        # News (unified: Portfolio + Brazil + China + Credit + GDELT)
        news=unified_news,
        news_total=len(unified_news),
        news_countries=news_countries,
        news_sectors=news_sectors,
        news_companies=news_companies,
        news_sources=news_sources,
        # Calendar (unified: Brazil incl. Portfolio + China)
        events=unified_events,
        event_months=event_months,
        # Economic Data (Brazil + China + Credit, merged client-side)
        china_datasets_json=json.dumps(CHINA_DATASETS),
        china_charts_json=json.dumps(china_charts),
        brazil_datasets_json=json.dumps(BRAZIL_DATASETS),
        brazil_charts_json=json.dumps(brazil_charts),
        credit_datasets_json=json.dumps(CREDIT_DATASETS),
        credit_charts_json=json.dumps(credit_charts),
        num_econ_datasets=len(CHINA_DATASETS) + len(BRAZIL_DATASETS) + len(CREDIT_DATASETS),
        # Other Datasets (China catalog browser)
        china_catalog_json=json.dumps(china_catalog),
        # Economic Data > Energy sub-page (ANP fuel-distribution data)
        fuel_data_json=json.dumps(fuel_data),
        # Economic Data > Pulp & Paper sub-page (FAOSTAT data + linked news)
        pulp_paper_json=json.dumps(pulp_paper_data),
        pulp_news=pulp_news,
        pulp_companies_json=json.dumps(pulp_companies),
        # Economic Data > Mining sub-page (BGS data + linked news)
        mining_json=json.dumps(mining_data),
        mining_news=mining_news,
        mining_companies_json=json.dumps(mining_companies),
        generated=datetime.now(BRASILIA_TZ).strftime("%Y-%m-%d %H:%M"),
        logo_data_uri=_logo_data_uri(),
    )
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    print("Loading unified news feed (Portfolio + Brazil + China + Credit + GDELT)...")
    unified_news, news_countries, news_sectors, news_companies, news_sources = load_unified_news()
    print(f"  {len(unified_news)} articles — {len(news_sectors)} sectors, {len(news_companies)} companies, {len(news_sources)} sources")

    print("Loading unified events calendar (Brazil incl. Portfolio + China)...")
    unified_events, event_months = load_unified_events()
    print(f"  {len(unified_events)} events")

    print("Fetching China chart data...")
    china_charts = fetch_china_charts()

    print("Fetching China dataset catalog...")
    china_catalog = fetch_china_catalog()

    print("Fetching Brazil macro charts (BCB)...")
    brazil_charts = fetch_brazil_charts()

    print("Fetching Credit market charts (BCB)...")
    credit_charts = fetch_credit_charts()

    print("Fetching cockpit market data...")
    cockpit = fetch_cockpit_data()

    print("Loading fuel-distribution data (ANP)...")
    fuel_data = load_fuel_data()
    print(f"  {len(fuel_data.get('E', []))} companies, {len(fuel_data.get('T', {}))} monthly series points")

    print("Loading pulp & paper data (FAOSTAT)...")
    pulp_paper_data = load_pulp_paper_data()
    print(f"  {len(pulp_paper_data.get('countries', {}))} countries, "
          f"{len(pulp_paper_data.get('years', []))} years")
    # Reuses the unified news feed already loaded above — brazil_scraper.py
    # already has a "Pulp & Paper" sector (Suzano/Klabin/Bracell/Eldorado,
    # celulose/kraft/tissue keywords), so this is a filter, not a new fetch.
    pulp_news = [a for a in unified_news if a.get("sector") == "Pulp & Paper"][:20]
    pulp_companies = fetch_pulp_companies()

    print("Loading mining data (BGS)...")
    mining_data = load_mining_data()
    print(f"  {len(mining_data.get('countries', {}))} countries, "
          f"{len(mining_data.get('years', []))} years")
    # Reuses the unified news feed already loaded above — brazil_scraper.py
    # already has a "Mining & Steel" sector, so this is a filter, not a new fetch.
    mining_news = [a for a in unified_news if a.get("sector") == "Mining & Steel"][:20]
    mining_companies = fetch_mining_companies()

    render(unified_news, news_countries, news_sectors, news_companies, news_sources,
           unified_events, event_months,
           china_charts, china_catalog,
           brazil_charts,
           credit_charts,
           cockpit,
           fuel_data,
           pulp_paper_data, pulp_news, pulp_companies,
           mining_data, mining_news, mining_companies)
    print("\ndashboard.html generated successfully.")


if __name__ == "__main__":
    main()

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent

MAX_NEWS_AGE_DAYS = 7   # keep articles up to 7 days old

NEWS_FILE          = ROOT / "data" / "news_cache.json"
EVENTS_FILE        = ROOT / "data" / "events.json"
CHINA_NEWS_FILE    = ROOT / "data" / "china_news_cache.json"
CHINA_EVENTS_FILE  = ROOT / "data" / "china_events.json"
BRAZIL_NEWS_FILE   = ROOT / "data" / "brazil_news_cache.json"
BRAZIL_EVENTS_FILE = ROOT / "data" / "brazil_events.json"
CREDIT_NEWS_FILE   = ROOT / "data" / "credit_news_cache.json"
OUTPUT_HTML        = ROOT / "dashboard.html"
TEMPLATE_FOLDER    = str(ROOT / "templates")
TEMPLATE_FILE      = "dashboard_template.html"

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


def parse_date(date_string):
    try:
        return parsedate_to_datetime(date_string)
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
    """Enforce 50% today / 50% yesterday split. Uses the pre-parsed 'datetime' field."""
    today_arts, yest_arts = [], []
    for a in articles:
        dt = a.get("datetime")
        if dt is None:
            continue
        age = (now_utc - dt).days
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
            r = requests.get(f"{CHINADATA_BASE}/data/{ds_id}",
                             headers=CHINADATA_HDR, timeout=15, verify=False)
            r.raise_for_status()
            result[ds_id] = r.json()["data"]
            print(f"  {ds_id}: {len(result[ds_id]['data'])} pts")
        except Exception as e:
            print(f"  {ds_id}: FAILED — {e}")
    return result


def fetch_china_catalog() -> list:
    """Fetch full list of all datasets available on chinadata.live."""
    try:
        r = requests.get(f"{CHINADATA_BASE}/datasets",
                         headers=CHINADATA_HDR, timeout=15, verify=False)
        r.raise_for_status()
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
            })
    now_utc = datetime.now(timezone.utc)
    rows.sort(
        key=lambda x: x["datetime"] if x["datetime"] else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    rows = balance_by_day(rows, now_utc, 500)  # enforce 50/50 across full feed
    return cache, rows


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
            r = requests.get(url, headers=BCB_HDR, timeout=20, verify=False)
            r.raise_for_status()
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
            r = requests.get(url, headers=BCB_HDR, timeout=20, verify=False)
            r.raise_for_status()
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
    ("Exchange Rates", ["brl_usd", "usd_cny", "brl_cny", "brl_eur"]),
    ("Commodities",    ["brent",   "iron_ore", "pix_bhkp", "pix_nbsk"]),
]


def _ck_make(label, unit, color, series):
    """Build a cockpit item dict with pre-formatted current value and change."""
    cur  = series[-1]["y"] if series else None
    prev = series[-2]["y"] if len(series) >= 2 else None
    if cur is None:
        return {"label": label, "unit": unit, "color": color,
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
    return {"label": label, "unit": unit, "color": color,
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


def _ck_fred(series_id, max_pts=60):
    """Fetch a FRED series via its public CSV endpoint (no API key needed)."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        r = requests.get(url, timeout=15, verify=False)
        r.raise_for_status()
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


def _ck_br10y():
    """BCB OLINDA series 12511 — ETTJ B-type ~10Y (IPCA-linked yield curve)."""
    today_ = date.today()
    start  = (today_ - timedelta(days=400)).strftime("%d/%m/%Y")
    end_   = today_.strftime("%d/%m/%Y")
    url    = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.12511/dados"
              f"?formato=json&dataInicial={start}&dataFinal={end_}")
    try:
        r = requests.get(url, headers=BCB_HDR, timeout=40, verify=False)
        r.raise_for_status()
        pts = []
        for pt in r.json():
            try:
                p = pt["data"].split("/")
                pts.append({"x": f"{p[2]}-{p[1]}-{p[0]}",
                             "y": round(float(pt["valor"].replace(",", ".")), 4)})
            except Exception:
                pass
        return pts
    except Exception as e:
        print(f"    BCB 12511: {e}")
        return []


def fetch_cockpit_data():
    import time as _time

    print("  Fetching cockpit market data...")
    data = {}

    yf_map = [
        ("sp500",   "^GSPC",    "S&P 500",          "pts",    "#3b82f6"),
        ("ibov",    "^BVSP",    "IBOVESPA",          "pts",    "#16a34a"),
        ("us10y",   "^TNX",     "US 10Y Treasury",   "%",      "#ef4444"),
        ("brl_usd", "USDBRL=X", "BRL / USD",         "BRL",    "#8b5cf6"),
        ("usd_cny", "USDCNY=X", "USD / CNY",         "CNY",    "#f59e0b"),
        ("brl_eur", "EURBRL=X", "EUR / BRL",         "BRL",    "#0284c7"),
        ("brent",   "BZ=F",     "Brent Crude",       "USD/bbl","#1e293b"),
    ]
    for key, ticker, label, unit, color in yf_map:
        print(f"    {label}...")
        data[key] = _ck_make(label, unit, color, _ck_yf(ticker))
        _time.sleep(0.3)

    # BRL/CNY: direct ticker, then compute from USDBRL + USDCNY as fallback
    print("    BRL / CNY...")
    brlcny = _ck_yf("BRLCNY=X")
    if not brlcny:
        brl_s = {p["x"]: p["y"] for p in data["brl_usd"]["data"]}
        cny_s = {p["x"]: p["y"] for p in data["usd_cny"]["data"]}
        brlcny = [{"x": d, "y": round(cny_s[d] / brl_s[d], 4)}
                  for d in sorted(set(brl_s) & set(cny_s))
                  if brl_s[d] and brl_s[d] != 0]
    data["brl_cny"] = _ck_make("BRL / CNY", "CNY", "#06b6d4", brlcny)

    # Brazil ~10Y via BCB
    print("    Brazil 10Y (BCB)...")
    data["br10y"] = _ck_make("Brazil 10Y (B-type)", "%", "#d97706", _ck_br10y())

    # Iron ore: yfinance TIO=F, then FRED PIORECRUSD as fallback
    print("    Iron Ore...")
    iron = _ck_yf("TIO=F")
    if not iron:
        iron = _ck_fred("PIORECRUSD", max_pts=52)
    data["iron_ore"] = _ck_make("Iron Ore 62% Fe", "USD/t", "#92400e", iron)

    # PIX pulp proxies via FRED (WPU0912 = PPI Pulp, Paper & Allied Products)
    print("    PIX BHKP / NBSK (FRED proxy)...")
    pulp = _ck_fred("WPU0912", max_pts=52)
    data["pix_bhkp"] = _ck_make("PIX BHKP Short Fibre *", "index", "#059669", pulp)
    data["pix_nbsk"] = _ck_make("PIX NBSK Long Fibre *",  "index", "#0d9488", pulp)

    return data


def render(rows, companies, sectors, providers, events, event_months,
           china_news, china_articles, china_events, china_event_months,
           china_charts, china_catalog,
           brazil_news, brazil_articles, brazil_events, brazil_event_months,
           brazil_charts,
           credit_news, credit_articles, credit_charts,
           cockpit):
    env      = Environment(loader=FileSystemLoader(TEMPLATE_FOLDER))
    template = env.get_template(TEMPLATE_FILE)
    html = template.render(
        # Cockpit
        cockpit=cockpit,
        cockpit_groups=COCKPIT_GROUPS,
        cockpit_json=json.dumps(cockpit),
        # Portfolio
        articles=rows,
        companies=companies,
        sectors=sectors,
        providers=providers,
        events=events,
        event_months=event_months,
        # China
        china_news=china_news,
        china_articles=china_articles,
        china_total=len(china_articles),
        china_events=china_events,
        china_event_months=china_event_months,
        china_datasets_json=json.dumps(CHINA_DATASETS),
        num_china_datasets=len(CHINA_DATASETS),
        china_charts_json=json.dumps(china_charts),
        china_catalog_json=json.dumps(china_catalog),
        # Brazil
        brazil_news=brazil_news,
        brazil_articles=brazil_articles,
        brazil_total=len(brazil_articles),
        brazil_events=brazil_events,
        brazil_event_months=brazil_event_months,
        brazil_datasets_json=json.dumps(BRAZIL_DATASETS),
        num_brazil_datasets=len(BRAZIL_DATASETS),
        brazil_charts_json=json.dumps(brazil_charts),
        # Credit
        credit_news=credit_news,
        credit_articles=credit_articles,
        credit_total=len(credit_articles),
        credit_datasets_json=json.dumps(CREDIT_DATASETS),
        num_credit_datasets=len(CREDIT_DATASETS),
        credit_charts_json=json.dumps(credit_charts),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    print("Loading portfolio news...")
    with open(NEWS_FILE, "r", encoding="utf-8") as f:
        news = json.load(f)
    rows, companies, sectors, providers = flatten_news(news)
    print(f"  {len(rows)} articles")

    print("Loading events...")
    events, event_months = load_events()
    print(f"  {len(events)} events")

    print("Loading China news...")
    china_news, china_articles = load_china_news()
    print(f"  {len(china_articles)} articles across {len(china_news)} sectors")

    print("Loading China events...")
    china_events, china_event_months = load_china_events()
    print(f"  {len(china_events)} China events")

    print("Fetching China chart data...")
    china_charts = fetch_china_charts()

    print("Fetching China dataset catalog...")
    china_catalog = fetch_china_catalog()

    print("Loading Brazil news...")
    brazil_news, brazil_articles = load_brazil_news()
    print(f"  {len(brazil_articles)} articles across {len(brazil_news)} sectors")

    print("Loading Brazil events...")
    brazil_events, brazil_event_months = load_brazil_events()
    print(f"  {len(brazil_events)} Brazil events")

    print("Fetching Brazil macro charts (BCB)...")
    brazil_charts = fetch_brazil_charts()

    print("Loading Credit news...")
    credit_news, credit_articles = load_credit_news()
    print(f"  {len(credit_articles)} articles across {len(credit_news)} sectors")

    print("Fetching Credit market charts (BCB)...")
    credit_charts = fetch_credit_charts()

    print("Fetching cockpit market data...")
    cockpit = fetch_cockpit_data()

    render(rows, companies, sectors, providers, events, event_months,
           china_news, china_articles, china_events, china_event_months,
           china_charts, china_catalog,
           brazil_news, brazil_articles, brazil_events, brazil_event_months,
           brazil_charts,
           credit_news, credit_articles, credit_charts,
           cockpit)
    print("\ndashboard.html generated successfully.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
events_generator.py
Generates events.json with:
  - Earnings dates       → yfinance (Yahoo Finance)
  - IPCA / IPCA-15 dates → IBGE open calendar API
  - COPOM meeting dates  → BCB annual schedule (hardcoded; update each January)

Run this script whenever you want to refresh the events calendar:
    python3 events_generator.py
"""

import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import certifi
import pandas as pd
import requests
import yfinance as yf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

ROOT            = Path(__file__).resolve().parent.parent
EXCEL_FILE      = ROOT / "data" / "portfolio.xlsx"
OUTPUT_FILE     = ROOT / "data" / "events.json"
LOOKAHEAD_DAYS  = 180   # include events up to ~6 months ahead

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/122 Safari/537.36"
    )
}

# ── COPOM meeting schedule ────────────────────────────────────────────────────
# The BCB publishes this calendar every January for the full year. It never
# changes mid-year. Update the list when BCB publishes next year's calendar.
# Each entry is (first_day, decision_day).
# Source: https://www.bcb.gov.br/monetariapolitica/copomreunioes
COPOM_SCHEDULE: dict[int, list[tuple[str, str]]] = {
    2026: [
        ("2026-01-28", "2026-01-29"),
        ("2026-03-18", "2026-03-19"),
        ("2026-05-06", "2026-05-07"),
        ("2026-06-17", "2026-06-18"),
        ("2026-07-29", "2026-07-30"),
        ("2026-09-16", "2026-09-17"),
        ("2026-11-04", "2026-11-05"),
        ("2026-12-09", "2026-12-10"),
    ],
    # Add 2027: [...] when BCB publishes next year's calendar.
}

# ── Company → Yahoo Finance / B3 ticker ──────────────────────────────────────
# Add new companies here when expanding the portfolio.
COMPANY_TICKERS: dict[str, str] = {
    "Petrobras":       "PETR4.SA",
    "Vale":            "VALE3.SA",
    "Itaú Unibanco":   "ITUB4.SA",
    "Banco do Brasil": "BBAS3.SA",
    "Bradesco":        "BBDC4.SA",
    "Embraer":         "EMBR3.SA",
    "Suzano":          "SUZB3.SA",
    "WEG":             "WEGE3.SA",
    "Gerdau":          "GGBR4.SA",
    "Ambev":           "ABEV3.SA",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_upcoming(d: date, today: date) -> bool:
    delta = (d - today).days
    return 0 <= delta <= LOOKAHEAD_DAYS


# ── 1. Earnings via yfinance ─────────────────────────────────────────────────

def fetch_earnings(companies: list[str]) -> list[dict]:
    today  = date.today()
    events = []

    for company in companies:
        symbol = COMPANY_TICKERS.get(company)
        if not symbol:
            print(f"    {company}: no ticker mapped, skipping")
            continue

        try:
            cal = yf.Ticker(symbol).calendar
            raw = cal.get("Earnings Date", []) if cal else []

            if not isinstance(raw, list):
                raw = [raw]

            future_dates = [
                d for d in raw
                if isinstance(d, date) and is_upcoming(d, today)
            ]

            if not future_dates:
                print(f"    {company} ({symbol}): no upcoming date on Yahoo Finance")
                continue

            next_date = min(future_dates)
            events.append({
                "date":        next_date.isoformat(),
                "type":        "earnings",
                "company":     company,
                "sector":      None,
                "title":       f"{company} – Earnings Release",
                "description": f"Quarterly results — {next_date.strftime('%B %Y')}",
            })
            print(f"    {company}: {next_date}")

        except Exception as e:
            print(f"    {company}: error — {e}")

        time.sleep(0.4)

    return events


# ── 2. COPOM from annual schedule ─────────────────────────────────────────────

def fetch_copom() -> list[dict]:
    today  = date.today()
    events = []

    for year, meetings in COPOM_SCHEDULE.items():
        for first_day_str, decision_day_str in meetings:
            first_day    = date.fromisoformat(first_day_str)
            decision_day = date.fromisoformat(decision_day_str)

            if is_upcoming(first_day, today):
                events.append({
                    "date":        first_day_str,
                    "type":        "economic",
                    "company":     None,
                    "sector":      None,
                    "title":       "COPOM – Reunião (1º dia)",
                    "description": "Reunião do Comitê de Política Monetária – Banco Central do Brasil",
                })

            if is_upcoming(decision_day, today):
                events.append({
                    "date":        decision_day_str,
                    "type":        "economic",
                    "company":     None,
                    "sector":      None,
                    "title":       "COPOM – Decisão da Selic",
                    "description": "Anúncio da taxa básica de juros pelo Banco Central do Brasil",
                })

    print(f"    {len(events)//2} upcoming meetings in schedule")
    return events


# ── 3. IPCA / IPCA-15 from IBGE calendar API ─────────────────────────────────

def fetch_ibge() -> list[dict]:
    today  = date.today()
    events = []

    IBGE_TARGETS = {
        "Índice Nacional de Preços ao Consumidor Amplo":    "IPCA",
        "Índice Nacional de Preços ao Consumidor Amplo 15": "IPCA-15",
        "Índice Nacional de Preços ao Consumidor":          "INPC",
    }

    try:
        r = requests.get(
            "https://servicodados.ibge.gov.br/api/v3/calendario/?tipo=pesquisa&qtd=500",
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
    except Exception as e:
        print(f"    IBGE API error: {e}")
        return []

    seen = set()

    for item in items:
        nome = item.get("nome_produto", "")
        label = None
        for full_name, short in IBGE_TARGETS.items():
            if full_name in nome:
                label = short
                break
        if not label:
            continue

        try:
            release = datetime.strptime(
                item["data_divulgacao"], "%d/%m/%Y %H:%M:%S"
            ).date()
        except (KeyError, ValueError):
            continue

        if not is_upcoming(release, today):
            continue

        key = f"{label}-{release}"
        if key in seen:
            continue
        seen.add(key)

        events.append({
            "date":        release.isoformat(),
            "type":        "economic",
            "company":     None,
            "sector":      None,
            "title":       f"{label} – {release.strftime('%B %Y')}",
            "description": "Divulgação pelo IBGE – Instituto Brasileiro de Geografia e Estatística",
        })

    events.sort(key=lambda x: x["date"])
    print(f"    {len(events)} upcoming releases found")
    return events


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    df = pd.read_excel(EXCEL_FILE)
    df = df[df["Company"].astype(str).str.strip() != "Company"]
    companies = df["Company"].astype(str).str.strip().tolist()

    print()
    print("=" * 60)
    print("Events Generator")
    print("=" * 60)

    all_events: list[dict] = []

    print("\n[ Earnings — Yahoo Finance / yfinance ]")
    all_events += fetch_earnings(companies)

    print("\n[ COPOM — BCB Annual Schedule ]")
    all_events += fetch_copom()

    print("\n[ IPCA / IBGE Calendar API ]")
    all_events += fetch_ibge()

    all_events.sort(key=lambda x: x["date"])

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_events, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 60)
    print(f"Done — {len(all_events)} events written to {OUTPUT_FILE}")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()

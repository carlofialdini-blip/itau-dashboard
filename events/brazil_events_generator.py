#!/usr/bin/env python3
"""
brazil_events_generator.py
Generates brazil_events.json with:

  1. IBGE macro release dates  — from IBGE calendar API
  2. COPOM meeting dates       — hardcoded annual schedule (same as events.json)
  3. Focus Report              — every Monday (BCB weekly market expectations survey)

Run:  python3 brazil_events_generator.py
"""

import calendar
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.net_utils import get_with_retry  # noqa: E402

OUTPUT_FILE    = ROOT / "data" / "brazil_events.json"
LOOKAHEAD_DAYS = 180
LOOKBACK_DAYS  = 7

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; dashboard-generator/1.0)"}

# ── COPOM schedule ────────────────────────────────────────────────────────────
# Source: Banco Central do Brasil — update each January when BCB publishes new calendar.
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
}

# ── IBGE product names to track ───────────────────────────────────────────────
IBGE_TARGETS = {
    "Índice Nacional de Preços ao Consumidor Amplo":    ("IPCA",              "Economy"),
    "Índice Nacional de Preços ao Consumidor Amplo 15": ("IPCA-15",           "Economy"),
    "Índice Nacional de Preços ao Consumidor":          ("INPC",              "Economy"),
    "Produto Interno Bruto":                            ("PIB (GDP)",          "Economy"),
    "Pesquisa Nacional por Amostra de Domicílios":      ("PNAD – Desemprego", "Labour"),
    "Pesquisa Industrial Mensal":                       ("Produção Industrial","Industry"),
    "Pesquisa Mensal de Comércio":                      ("Vendas no Varejo",  "Trade"),
    "Pesquisa Mensal de Serviços":                      ("Setor de Serviços", "Economy"),
}


def load_ibge_releases(today: date) -> list[dict]:
    """Fetch upcoming IBGE statistical release dates from the official calendar API."""
    try:
        r = get_with_retry(
            "https://servicodados.ibge.gov.br/api/v3/calendario/?tipo=pesquisa&qtd=500",
            headers=HEADERS, timeout=15,
        )
        items = r.json().get("items", [])
    except Exception as e:
        print(f"  IBGE API error: {e}")
        return []

    from datetime import datetime
    events = []
    seen   = set()

    for item in items:
        nome = item.get("nome_produto", "")
        label, sector = None, None
        for full_name, (short, sec) in IBGE_TARGETS.items():
            if full_name in nome:
                label, sector = short, sec
                break
        if not label:
            continue

        try:
            release = datetime.strptime(
                item["data_divulgacao"], "%d/%m/%Y %H:%M:%S"
            ).date()
        except (KeyError, ValueError):
            continue

        delta = (release - today).days
        if not (-LOOKBACK_DAYS <= delta <= LOOKAHEAD_DAYS):
            continue

        key = f"{label}-{release}"
        if key in seen:
            continue
        seen.add(key)

        events.append({
            "date":        release.isoformat(),
            "type":        "data",
            "sector":      sector,
            "company":     None,
            "title":       f"{label} — {release.strftime('%B %Y')}",
            "description": f"Divulgação pelo IBGE — Instituto Brasileiro de Geografia e Estatística.",
        })

    print(f"  {len(events)} IBGE releases found")
    return events


def load_copom(today: date) -> list[dict]:
    events = []
    for year, meetings in COPOM_SCHEDULE.items():
        for first_str, decision_str in meetings:
            for day_str, label, desc in [
                (first_str,    "COPOM – Reunião (1º dia)",
                 "1º dia da reunião do Comitê de Política Monetária — Banco Central do Brasil."),
                (decision_str, "COPOM – Decisão da Selic",
                 "Anúncio da nova taxa Selic pelo Banco Central do Brasil."),
            ]:
                ev_date = date.fromisoformat(day_str)
                delta   = (ev_date - today).days
                if -LOOKBACK_DAYS <= delta <= LOOKAHEAD_DAYS:
                    events.append({
                        "date":        day_str,
                        "type":        "economic",
                        "sector":      "Economy",
                        "company":     None,
                        "title":       label,
                        "description": desc,
                    })
    print(f"  {len(events)//2} upcoming COPOM meetings")
    return events


def load_focus_report(today: date) -> list[dict]:
    """BCB Focus Report — released every Monday (weekly market expectations survey)."""
    events = []
    d = today
    # Find next Monday (weekday 0)
    while d.weekday() != 0:
        d += timedelta(days=1)
    # Generate Mondays for the lookahead window
    while (d - today).days <= LOOKAHEAD_DAYS:
        events.append({
            "date":        d.isoformat(),
            "type":        "data",
            "sector":      "Economy",
            "company":     None,
            "title":       f"BCB Focus Report — {d.strftime('%d %b %Y')}",
            "description": "Relatório semanal do Banco Central com expectativas do mercado para IPCA, PIB, câmbio e Selic.",
        })
        d += timedelta(weeks=1)
    print(f"  {len(events)} Focus Report dates")
    return events


def main():
    today = date.today()
    print()
    print("=" * 60)
    print("Brazil Events Generator")
    print("=" * 60)

    print("\n[ IBGE release calendar ]")
    ibge = load_ibge_releases(today)

    print("\n[ COPOM meeting schedule ]")
    copom = load_copom(today)

    print("\n[ BCB Focus Report (Mondays) ]")
    focus = load_focus_report(today)

    all_events = ibge + copom + focus
    all_events.sort(key=lambda x: x["date"])

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_events, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 60)
    print(f"Done — {len(all_events)} events → {OUTPUT_FILE}")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()

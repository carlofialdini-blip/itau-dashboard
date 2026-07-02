#!/usr/bin/env python3
"""
china_events_generator.py
Generates china_events.json with:

  1. China macro data release dates  — pattern-based (NBS publishes on fixed schedules)
  2. PBOC Loan Prime Rate decisions  — 20th of every month
  3. Industry conferences            — annual schedule (update each January)

Run:  python3 china_events_generator.py
"""

import calendar
import json
from datetime import date, timedelta
from pathlib import Path

ROOT           = Path(__file__).resolve().parent.parent
OUTPUT_FILE    = ROOT / "data" / "china_events.json"
LOOKAHEAD_DAYS = 180   # ~6 months ahead
LOOKBACK_DAYS  = 7     # include events up to 7 days in the past


# ── Annual conference schedule ────────────────────────────────────────────────
# Update this dict each January when organisers confirm dates.
# Source: Fastmarkets, CNIA, CISA, Mining Indaba, etc.
CHINA_CONFERENCE_SCHEDULE: dict[int, list[dict]] = {
    2026: [
        # ── Pulp & Paper ─────────────────────────────────────────────────────
        {
            "month": 10, "day": 13,
            "sector": "Pulp & Paper",
            "title": "Fastmarkets Pulp & Paper Week 2026",
            "description": (
                "Global cellulose and paper market conference. "
                "Key pricing signals, demand forecasts for Brazilian producers (Suzano, Klabin)."
            ),
        },
        {
            "month": 11, "day": 18,
            "sector": "Pulp & Paper",
            "title": "China International Paper Industry Conference 2026",
            "description": "Annual gathering of China's paper and pulp industry; capacity, import quotas discussed.",
        },
        # ── Mining & Metals ───────────────────────────────────────────────────
        {
            "month": 9, "day": 14,
            "sector": "Mining",
            "title": "Denver Gold Forum 2026",
            "description": "Leading precious and base metals investor conference. Attended by major miners including Vale.",
        },
        {
            "month": 10, "day": 21,
            "sector": "Mining",
            "title": "China Mining Congress 2026",
            "description": (
                "China's largest mining and metals conference in Tianjin. "
                "Iron ore, copper, nickel and critical minerals — key signal for Vale and Gerdau."
            ),
        },
        {
            "month": 11, "day": 10,
            "sector": "Mining",
            "title": "Metal Bulletin Iron Ore & Steel Scrap Conference 2026",
            "description": "Annual iron ore pricing and seaborne trade conference. Direct relevance to Vale.",
        },
        # ── Industrial / Manufacturing ────────────────────────────────────────
        {
            "month": 9, "day": 15,
            "sector": "Industrial",
            "title": "China International Industry Fair 2026 (CIIF)",
            "description": "Premier manufacturing and industrial technology exhibition in Shanghai. Industrial automation focus.",
        },
        {
            "month": 10, "day": 14,
            "sector": "Industrial",
            "title": "China Steel Summit 2026",
            "description": "Annual summit on China's steel industry outlook. Production cuts, export policies and demand discussed.",
        },
        # ── Economic / Policy ─────────────────────────────────────────────────
        {
            "month": 7, "day": 15,
            "sector": "Economic",
            "title": "China Third Plenum Follow-up Briefing",
            "description": "CCP Central Committee economic policy briefings following the 2025 Third Plenum decisions.",
        },
        {
            "month": 12, "day": 10,
            "sector": "Economic",
            "title": "China Central Economic Work Conference",
            "description": "Annual top-level government conference that sets China's economic priorities for the following year.",
        },
    ],
    # Add 2027: [...] when organizers confirm dates.
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def last_day_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def first_business_day(year: int, month: int) -> date:
    """First Monday–Friday of the given month."""
    d = date(year, month, 1)
    while d.weekday() > 4:   # skip Sat/Sun
        d += timedelta(days=1)
    return d


def pub_month(ref_year: int, ref_month: int) -> tuple[int, int]:
    """Return (year, month) of the publication month (ref + 1)."""
    if ref_month == 12:
        return ref_year + 1, 1
    return ref_year, ref_month + 1


# ── Macro event computation ───────────────────────────────────────────────────

def compute_macro_events(today: date) -> list[dict]:
    """
    Calculate China NBS / PBOC macro data release dates.

    NBS typical schedule (data released for month M, published in month M+1):
      ─ Official PMI          : last calendar day of M
      ─ Caixin PMI            : 1st business day of M+1
      ─ CPI / PPI             : ~10th of M+1
      ─ Trade statistics      : ~13th of M+1
      ─ Industrial Prod / FAI : ~15th of M+1
      ─ PBOC LPR              : 20th of every month
      ─ GDP (quarterly)       : ~15th of Apr / Jul / Oct / Jan
    """
    events = []
    seen   = set()

    def add(ev_date: date, title: str, description: str, sector: str, ev_type: str = "data"):
        key = f"{ev_date}|{title}"
        if key in seen:
            return
        seen.add(key)
        delta = (ev_date - today).days
        if -LOOKBACK_DAYS <= delta <= LOOKAHEAD_DAYS:
            events.append({
                "date":        ev_date.isoformat(),
                "type":        ev_type,
                "sector":      sector,
                "company":     None,
                "title":       title,
                "description": description,
            })

    # Scan 8 months of reference months centred on today
    for offset in range(-1, 8):
        raw_month = today.month + offset
        ref_year  = today.year + (raw_month - 1) // 12
        ref_month = (raw_month - 1) % 12 + 1
        ref_label = date(ref_year, ref_month, 1).strftime("%B %Y")
        py, pm    = pub_month(ref_year, ref_month)

        try:
            # ── Official PMI (last day of reference month) ──────────────────
            add(last_day_of_month(ref_year, ref_month),
                f"China Official PMI – {ref_label}",
                "NBS Manufacturing and Non-Manufacturing PMI. Primary gauge of factory and services activity.",
                "Industrial")

            # ── Caixin PMI (1st business day of publication month) ──────────
            add(first_business_day(py, pm),
                f"Caixin China Manufacturing PMI – {ref_label}",
                "S&P Global / Caixin PMI — independent survey of Chinese factories, skewed to private sector.",
                "Industrial")

            # ── CPI / PPI (~10th of publication month) ─────────────────────
            add(date(py, pm, 10),
                f"China CPI / PPI – {ref_label}",
                "Consumer Price Index and Producer Price Index from NBS. Key for monetary policy and commodity pricing.",
                "Economic")

            # ── Trade Statistics (~13th of publication month) ───────────────
            add(date(py, pm, 13),
                f"China Trade Data – {ref_label}",
                "Monthly exports, imports and trade balance from General Administration of Customs.",
                "Economic")

            # ── Industrial Production / Fixed Asset Investment / Retail ──────
            add(date(py, pm, 15),
                f"China Industrial Production / FAI / Retail Sales – {ref_label}",
                "NBS monthly data on industrial output, fixed-asset investment and retail sales.",
                "Industrial")

            # ── PBOC Loan Prime Rate (20th of publication month) ────────────
            add(date(py, pm, 20),
                f"PBOC Loan Prime Rate (LPR) – {date(py, pm, 1).strftime('%B %Y')}",
                "Monthly LPR announcement by the People's Bank of China. Sets benchmark borrowing costs.",
                "Economic",
                ev_type="economic")

            # ── Quarterly GDP (~15th of Apr / Jul / Oct / Jan) ──────────────
            if pm in (1, 4, 7, 10):
                q_label  = {1: "Q4", 4: "Q1", 7: "Q2", 10: "Q3"}[pm]
                gdp_year = py if pm != 1 else py - 1
                add(date(py, pm, 16),
                    f"China GDP – {q_label} {gdp_year}",
                    "Quarterly GDP from NBS. Includes breakdown by sector (industry, services, agriculture).",
                    "Economic")

        except ValueError:
            continue   # skip impossible dates (e.g. month = 13 overflow)

    return events


# ── Conference loader ─────────────────────────────────────────────────────────

def load_conferences(today: date) -> list[dict]:
    events = []
    for year, items in CHINA_CONFERENCE_SCHEDULE.items():
        for item in items:
            try:
                ev_date = date(year, item["month"], item["day"])
            except ValueError:
                continue
            delta = (ev_date - today).days
            if -LOOKBACK_DAYS <= delta <= LOOKAHEAD_DAYS:
                events.append({
                    "date":        ev_date.isoformat(),
                    "type":        "conference",
                    "sector":      item.get("sector", ""),
                    "company":     None,
                    "title":       item["title"],
                    "description": item.get("description", ""),
                })
    return events


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    today = date.today()
    print()
    print("=" * 60)
    print("China Events Generator")
    print("=" * 60)

    print("\n[ Macro data release dates — NBS / PBOC pattern ]")
    macro = compute_macro_events(today)
    print(f"  {len(macro)} events computed")

    print("\n[ Industry conferences — annual schedule ]")
    confs = load_conferences(today)
    print(f"  {len(confs)} conferences")

    all_events = macro + confs
    all_events.sort(key=lambda x: x["date"])

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_events, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 60)
    print(f"Done — {len(all_events)} events → china_events.json")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()

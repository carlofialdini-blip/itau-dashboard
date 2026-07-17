#!/usr/bin/env python3
"""
update_dashboard.py — Daily dashboard refresh.

Run this twice a day. It re-fetches all news, market data, and events,
then rebuilds dashboard.html. When it's done, upload dashboard.html to
SharePoint (or send it directly) so your team can open it locally — the
file is fully self-contained, no server or install needed on their end.

If one data source fails (e.g. a network hiccup), the build continues
with the rest and keeps whatever cached data that source had before —
it does not abort the whole refresh.

Run:  python3 update_dashboard.py
"""

import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent

STEPS = [
    ("Portfolio news",    [sys.executable, str(ROOT / "scrapers" / "scraper.py")]),
    ("Portfolio news (GDELT)", [sys.executable, str(ROOT / "scrapers" / "gdelt_scraper.py")]),
    ("China news",        [sys.executable, str(ROOT / "scrapers" / "china_scraper.py")]),
    ("Brazil news",       [sys.executable, str(ROOT / "scrapers" / "brazil_scraper.py")]),
    ("Credit news",       [sys.executable, str(ROOT / "scrapers" / "credit_scraper.py")]),
    ("Portfolio events",  [sys.executable, str(ROOT / "events" / "events_generator.py")]),
    ("China events",      [sys.executable, str(ROOT / "events" / "china_events_generator.py")]),
    ("Brazil events",     [sys.executable, str(ROOT / "events" / "brazil_events_generator.py")]),
    ("Building dashboard", [sys.executable, str(ROOT / "core" / "generate_dashboard.py")]),
]


def run():
    print()
    print("=" * 55)
    print("  Updating Portfolio Intelligence dashboard")
    print("=" * 55)
    print()

    total   = len(STEPS)
    failed  = []

    for i, (label, cmd) in enumerate(STEPS, 1):
        print(f"  [{i}/{total}] {label}...")
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=600)
        if result.returncode != 0:
            failed.append(label)
            print(f"        FAILED — {label} (continuing with the rest):")
            tail = (result.stderr or result.stdout or "").strip()[-500:]
            for line in tail.splitlines():
                print(f"        {line}")

    print()
    print("=" * 55)
    if "Building dashboard" in failed:
        print("  Update FAILED — dashboard.html was not rebuilt.")
        print("  See the error above and re-run: python3 update_dashboard.py")
    else:
        print("  Update complete!")
        print()
        print(f"  dashboard.html -> {ROOT / 'dashboard.html'}")
        print("  Upload it to SharePoint / send it to your team.")
        if failed:
            print()
            print(f"  Note: {', '.join(failed)} failed this run — that data")
            print("  may be stale until the next successful update.")
    print("=" * 55)
    print()

    return 1 if "Building dashboard" in failed else 0


if __name__ == "__main__":
    sys.exit(run())

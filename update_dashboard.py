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
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent

# Per-step ceiling. Generous on purpose: the target machine sits behind a
# corporate proxy that inspects every request, and the heaviest steps (BGS
# mining ~4min, ANP/FAOSTAT/Comex bulk downloads of 20-100MB) are already
# slow on an unrestricted connection. A step that exceeds this is caught and
# reported, never allowed to abort the run.
STEP_TIMEOUT = 900

STEPS = [
    ("Portfolio news",    [sys.executable, str(ROOT / "scrapers" / "scraper.py")]),
    ("China news",        [sys.executable, str(ROOT / "scrapers" / "china_scraper.py")]),
    ("Brazil news",       [sys.executable, str(ROOT / "scrapers" / "brazil_scraper.py")]),
    ("Credit news",       [sys.executable, str(ROOT / "scrapers" / "credit_scraper.py")]),
    ("Fuel distribution data (ANP)", [sys.executable, str(ROOT / "scrapers" / "fuel_scraper.py")]),
    ("Pulp & paper data (FAOSTAT)", [sys.executable, str(ROOT / "scrapers" / "pulp_paper_scraper.py")]),
    ("Mining data (BGS)", [sys.executable, str(ROOT / "scrapers" / "mining_scraper.py")]),
    ("Agriculture data (CONAB/IBGE/MAPA/BCB)", [sys.executable, str(ROOT / "scrapers" / "agriculture_scraper.py")]),
    ("Trade data (MDIC Comex Stat)", [sys.executable, str(ROOT / "scrapers" / "comex_scraper.py")]),
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
        # Every failure mode of a child step must land here as "this step
        # failed, keep going" — that is this script's entire contract (see the
        # module docstring). A bare subprocess.run() breaks that contract two
        # ways: TimeoutExpired and OSError propagate out and kill the whole
        # refresh, taking the remaining steps (including "Building dashboard")
        # with them. The timeout is real and reachable on a slow corporate
        # proxy — the BGS mining scraper already takes ~4 minutes on a normal
        # connection, and the Comex step pulls 50-100MB CSVs.
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=STEP_TIMEOUT)
        except subprocess.TimeoutExpired:
            failed.append(label)
            print(f"        FAILED — {label} timed out after {STEP_TIMEOUT}s "
                  f"(continuing with the rest; this source keeps its last cached data)")
            continue
        except OSError as e:
            failed.append(label)
            print(f"        FAILED — {label} could not start: {e} (continuing with the rest)")
            continue
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

#!/usr/bin/env python3
"""
setup.py  —  First-time setup for Portfolio Intelligence Dashboard
Runs all scrapers, event generators, and builds dashboard.html.
After this completes, start the server with:  python3 server.py
"""

import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

STEPS = [
    ("Installing dependencies",         [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt"), "-q"]),
    ("Fetching portfolio news",          [sys.executable, str(ROOT / "scrapers"  / "scraper.py")]),
    ("Fetching China news",              [sys.executable, str(ROOT / "scrapers"  / "china_scraper.py")]),
    ("Fetching Brazil news",             [sys.executable, str(ROOT / "scrapers"  / "brazil_scraper.py")]),
    ("Fetching credit market news",      [sys.executable, str(ROOT / "scrapers"  / "credit_scraper.py")]),
    ("Generating portfolio events",      [sys.executable, str(ROOT / "events"    / "events_generator.py")]),
    ("Generating China events",          [sys.executable, str(ROOT / "events"    / "china_events_generator.py")]),
    ("Generating Brazil events",         [sys.executable, str(ROOT / "events"    / "brazil_events_generator.py")]),
    ("Building dashboard",               [sys.executable, str(ROOT / "core"      / "generate_dashboard.py")]),
]

def run():
    print()
    print("=" * 55)
    print("  Portfolio Intelligence — first-time setup")
    print("=" * 55)
    print()

    total = len(STEPS)
    for i, (label, cmd) in enumerate(STEPS, 1):
        print(f"  [{i}/{total}] {label}…")
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            print(f"\n  ERROR in step {i}:")
            print(result.stderr[-800:] if result.stderr else result.stdout[-800:])
            print("\n  Setup aborted. Fix the error above and re-run setup.py")
            sys.exit(1)
        time.sleep(0.5)

    print()
    print("=" * 55)
    print("  Setup complete!")
    print()
    print("  Start the dashboard:")
    print("    python3 server.py")
    print()
    print("  This opens http://localhost:8080 in your browser.")
    print("  Use the Refresh button in the header to update news.")
    print("=" * 55)
    print()

if __name__ == "__main__":
    run()

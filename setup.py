#!/usr/bin/env python3
"""
setup.py — One-time setup for a new machine.

Run this ONCE on any computer you'll personally run the dashboard from
(e.g. a new work laptop). It installs the Python dependencies and then
runs the first full data pull + build.

You do NOT send this file (or any other .py file) to your team — they
only ever need the generated dashboard.html, which is fully self-contained
and opens directly in a browser with no install and no server.

After this completes, refresh the dashboard going forward with:
    python3 update_dashboard.py

Run:  python3 setup.py
"""

import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent


def run():
    print()
    print("=" * 55)
    print("  Portfolio Intelligence — first-time setup")
    print("=" * 55)
    print()

    print("  Installing dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt"), "-q"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        print("\n  ERROR installing dependencies:")
        print(result.stderr[-800:] if result.stderr else result.stdout[-800:])
        print("\n  Setup aborted. Fix the error above and re-run setup.py")
        sys.exit(1)

    print("  Dependencies installed.")
    print()

    code = subprocess.call([sys.executable, str(ROOT / "update_dashboard.py")])
    if code != 0:
        sys.exit(code)

    print("  Setup complete! From now on, just run:")
    print("    python3 update_dashboard.py")
    print()
    print("  Do this twice a day, then send/upload the resulting")
    print("  dashboard.html to your team.")
    print("=" * 55)
    print()


if __name__ == "__main__":
    run()

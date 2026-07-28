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

import collections
import subprocess
import sys
import threading
import time
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

# How long a step may print nothing before the runner says it is still alive.
# Without this, a slow step is indistinguishable from a hung one: the portfolio
# news scraper issues ~30 Google News requests at up to 15s each, so several
# minutes of total silence is normal — and was reported as "stuck".
HEARTBEAT_SECONDS = 20

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


def run_step(cmd):
    """Run one step, echoing its output live. Returns (returncode, tail_lines).

    Deliberately Popen + a reader thread rather than subprocess.run(
    capture_output=True): that buffers everything until the child exits and
    then shows only a 500-char tail on failure, so a step that takes minutes
    looked completely frozen even though the scraper underneath was printing
    per-company progress the whole time.

    `-u` matters as much as the streaming does. A child Python writing to a
    pipe (not a TTY) block-buffers stdout, so without it the output still
    arrives in 4-8KB bursts at the end and the live echo buys nothing.

    Returncode of None means "timed out"; the caller reports it as a failed
    step, preserving this script's continue-past-failures contract.
    """
    proc = subprocess.Popen(
        [cmd[0], "-u"] + cmd[1:],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    tail = collections.deque(maxlen=40)
    last_output = [time.monotonic()]

    def pump():
        for line in proc.stdout:
            line = line.rstrip()
            last_output[0] = time.monotonic()
            if line:
                tail.append(line)
                print(f"        │ {line}", flush=True)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    started = time.monotonic()
    while True:
        try:
            proc.wait(timeout=1)
            break
        except subprocess.TimeoutExpired:
            pass
        now = time.monotonic()
        if now - started > STEP_TIMEOUT:
            proc.kill()
            reader.join(timeout=5)
            return None, list(tail)
        if now - last_output[0] > HEARTBEAT_SECONDS:
            print(f"        │ … still working ({int(now - started)}s elapsed)", flush=True)
            last_output[0] = now

    reader.join(timeout=5)
    return proc.returncode, list(tail)


def run():
    print()
    print("=" * 55)
    print("  Updating Portfolio Intelligence dashboard")
    print("=" * 55)
    print()

    total   = len(STEPS)
    failed  = []

    run_started = time.monotonic()

    for i, (label, cmd) in enumerate(STEPS, 1):
        print(f"  [{i}/{total}] {label}...", flush=True)
        step_started = time.monotonic()
        # Every failure mode of a child step must land here as "this step
        # failed, keep going" — that is this script's entire contract (see the
        # module docstring). OSError (the step's script is missing/unlaunchable)
        # and the timeout are both handled rather than allowed to propagate and
        # kill the whole refresh, taking the remaining steps — including
        # "Building dashboard" — with them. The timeout is real and reachable on
        # a slow corporate proxy: the BGS mining scraper already takes ~4 minutes
        # on a normal connection, and the Comex step pulls 50-100MB CSVs.
        try:
            code, tail = run_step(cmd)
        except OSError as e:
            failed.append(label)
            print(f"        FAILED — {label} could not start: {e} (continuing with the rest)")
            continue
        elapsed = int(time.monotonic() - step_started)
        if code is None:
            failed.append(label)
            print(f"        FAILED — {label} timed out after {STEP_TIMEOUT}s "
                  f"(continuing with the rest; this source keeps its last cached data)")
            continue
        if code != 0:
            failed.append(label)
            print(f"        FAILED — {label} after {elapsed}s (continuing with the rest):")
            for line in tail[-12:]:
                print(f"        {line}")
        else:
            print(f"        ✓ {label} ({elapsed}s)", flush=True)
        print(flush=True)

    total_elapsed = int(time.monotonic() - run_started)
    mins, secs = divmod(total_elapsed, 60)
    print("=" * 55)
    print(f"  Total run time: {mins}m {secs}s")
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

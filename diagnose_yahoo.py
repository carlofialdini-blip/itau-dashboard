#!/usr/bin/env python3
"""
diagnose_yahoo.py — one-off diagnostic, NOT part of the pipeline.

Run this on the bank machine (`python diagnose_yahoo.py`) and paste the full
output back. It checks, one layer at a time, exactly where the connection to
Yahoo Finance is actually breaking:

  1. DNS       — can the hostname even be resolved?
  2. TCP       — can a raw socket connect to port 443?
  3. HTTPS GET — does a plain requests.get(..., verify=False) succeed, and if
                 not, what's the real exception (cert error vs. connection
                 reset vs. timeout vs. something else)?
  4. yfinance  — does the actual fixed code path (get_yf_session(), the
                 verify=False fix already pushed) succeed end-to-end?

Each layer tells us something different:
  - DNS fails            -> the bank's DNS resolver itself is blocking/
                             not resolving this hostname (a firewall/policy
                             block, code can't work around this).
  - TCP fails             -> a firewall is dropping/rejecting the connection
                             outright (also a policy block, not a code fix).
  - HTTPS fails with a    -> exactly what the verify=False fix targets; if
    certificate error        this is what's happening, something is wrong
                             with how/whether that fix actually landed.
  - HTTPS fails with a    -> Yahoo (or a proxy in between) is actively
    403/429/999               blocking or rate-limiting requests from the
                             bank's shared egress IP -- a different problem
                             from TLS interception, and not fixable by
                             changing this project's code at all.
  - HTTPS succeeds but    -> same as above, just surfaced through yfinance's
    yfinance still fails      own error handling instead of a raw status code.
"""
import socket
import sys

HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com", "fc.yahoo.com"]

print("=" * 70)
print("1. DNS resolution")
print("=" * 70)
for host in HOSTS:
    try:
        ip = socket.gethostbyname(host)
        print(f"  OK   {host} -> {ip}")
    except Exception as e:
        print(f"  FAIL {host}: {type(e).__name__}: {e}")

print()
print("=" * 70)
print("2. Raw TCP connect on port 443")
print("=" * 70)
for host in HOSTS:
    try:
        s = socket.create_connection((host, 443), timeout=10)
        s.close()
        print(f"  OK   {host}:443")
    except Exception as e:
        print(f"  FAIL {host}:443 -- {type(e).__name__}: {e}")

print()
print("=" * 70)
print("3. Plain HTTPS GET (verify=False), no yfinance involved")
print("=" * 70)
try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    url = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 Chrome/122 Safari/537.36"}
    r = requests.get(url, headers=headers, timeout=15, verify=False)
    print(f"  Status: {r.status_code}")
    print(f"  Body (first 300 chars): {r.text[:300]}")
except Exception as e:
    print(f"  FAIL -- {type(e).__name__}: {e}")

print()
print("=" * 70)
print("4. yfinance via this project's actual fixed code path")
print("=" * 70)
try:
    sys.path.insert(0, "core")
    from net_utils import get_yf_session
    import yfinance as yf
    print(f"  yfinance version: {yf.__version__}")
    df = yf.download("AAPL", period="5d", interval="1d",
                      auto_adjust=True, progress=False,
                      session=get_yf_session())
    if df.empty:
        print("  Result: EMPTY dataframe (no exception raised, but no data either)")
    else:
        print(f"  Result: OK -- {len(df)} rows, last close: {df['Close'].iloc[-1]}")
except Exception as e:
    print(f"  FAIL -- {type(e).__name__}: {e}")

print()
print("=" * 70)
print("Done. Paste this whole output back.")
print("=" * 70)

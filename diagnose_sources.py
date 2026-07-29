#!/usr/bin/env python3
"""
diagnose_sources.py — one-off, READ-ONLY diagnostic for the two data sources
that are failing on the Itaú machine: Banco Central (BCB) and FAOSTAT
(the Pulp & Paper source).

Run it on the bank machine and paste the whole output back:

    python diagnose_sources.py

It writes nothing, changes nothing, and touches no part of the pipeline.
It does not import any project module on purpose — if something in this
repo were misconfigured, importing it would hide the very thing we are
trying to measure. Everything here is plain `requests` + `socket` + `ssl`.

Why layered: "no data" is the only symptom that reaches the dashboard, and
it looks identical whether the cause is DNS, a proxy block page, a
throttle, a timeout, or the server having a bad day. Each of those needs a
different response, and one of them (a proxy returning an HTML block page
with HTTP 200) sails straight past raise_for_status() and only dies later
at .json() — so the status code alone is not enough. This checks each
layer separately and prints what actually came back.
"""

import json
import os
import socket
import ssl
import sys
import time
from datetime import date
from urllib.parse import urlparse

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("requests/urllib3 not installed — run: pip install -r requirements.txt")
    sys.exit(1)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# The exact User-Agent the pipeline sends (core/net_utils.DEFAULT_HEADERS),
# duplicated here rather than imported so this file stays standalone.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 Chrome/122 Safari/537.36")

TODAY = date.today().strftime("%d/%m/%Y")
START = date.today().replace(year=date.today().year - 1).strftime("%d/%m/%Y")

BCB_TARGETS = [
    ("BCB OLINDA — the exact call the dashboard makes (Selic, series 432)",
     f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados"
     f"?dataInicial={START}&dataFinal={TODAY}&formato=json", "json"),
    ("BCB OLINDA — minimal query (last 1 point only)",
     "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/1?formato=json", "json"),
    ("BCB OLINDA — rural credit, series 22027 (the Agriculture KPI)",
     "https://api.bcb.gov.br/dados/serie/bcdata.sgs.22027/dados/ultimos/1?formato=json", "json"),
    ("BCB olinda.bcb.gov.br — DIFFERENT HOST, same institution (PTAX OData)",
     "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
     "Moedas?$top=5&$format=json", "json"),
    ("BCB www3.bcb.gov.br — legacy SGS host",
     "https://www3.bcb.gov.br/sgspub/", "any"),
    ("BCB main website — is the whole bcb.gov.br family blocked?",
     "https://www.bcb.gov.br/", "any"),
]

FAO_TARGETS = [
    ("FAOSTAT bulk zip — the exact file the Pulp & Paper scraper downloads",
     "https://bulks-faostat.fao.org/production/Forestry_E_All_Data_(Normalized).zip", "zip"),
    ("FAOSTAT bulk host — a plain path on the same host (host vs file test)",
     "https://bulks-faostat.fao.org/production/", "any"),
    ("FAO main website — is the whole fao.org family blocked?",
     "https://www.fao.org/faostat/en/", "any"),
]

# Controls: these are known to work on the bank machine, so if one of THESE
# fails too, the problem is the network in general, not these two sources.
CONTROL_TARGETS = [
    ("CONTROL — IBGE SIDRA (works today; used by Agriculture/Mining/Pulp)",
     "https://apisidra.ibge.gov.br/values/t/1612/n1/all/v/109/p/last%201", "any"),
    # FRED must NOT get the browser UA: core/generate_dashboard.py's _ck_fred()
    # sends a plain one because fredgraph.csv hangs to timeout on the
    # Chrome-spoofed UA (confirmed deterministically, see CLAUDE.md). Sending
    # the browser UA here would produce a false FAIL and send us chasing a
    # network problem that isn't there.
    ("CONTROL — FRED (works today; used by the Cockpit)",
     "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PALUMUSDM", "any",
     "itau-dashboard-diagnostic/1.0"),
]


def rule(title):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def check_env():
    rule("1. ENVIRONMENT")
    print(f"  python           : {sys.version.split()[0]}")
    print(f"  requests         : {requests.__version__}")
    print(f"  platform         : {sys.platform}")
    proxies_seen = False
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "no_proxy"):
        val = os.environ.get(var)
        if val:
            proxies_seen = True
            print(f"  {var:17}: {val}")
    if not proxies_seen:
        print("  proxy env vars   : (none set — requests will connect directly)")
    # What requests itself resolves, including any Windows system proxy
    try:
        env_proxies = requests.utils.get_environ_proxies("https://api.bcb.gov.br")
        print(f"  resolved proxies : {env_proxies or '(none)'}")
    except Exception as e:
        print(f"  resolved proxies : (could not determine: {e})")


def check_dns_tcp_tls(host, port=443):
    """DNS -> TCP -> TLS, each reported separately."""
    out = {}
    t0 = time.monotonic()
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        out["dns"] = f"OK  {', '.join(ips[:3])}  ({time.monotonic()-t0:.2f}s)"
    except Exception as e:
        out["dns"] = f"FAIL  {type(e).__name__}: {e}"
        out["tcp"] = "skipped (no DNS)"
        out["tls"] = "skipped (no DNS)"
        return out

    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=15) as sock:
            out["tcp"] = f"OK  ({time.monotonic()-t0:.2f}s)"
            t0 = time.monotonic()
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert_issuer = "unknown"
                try:
                    der = tls.getpeercert(binary_form=True)
                    cert_issuer = f"{len(der)} bytes"
                except Exception:
                    pass
                out["tls"] = (f"OK  {tls.version()}  cert={cert_issuer}  "
                              f"({time.monotonic()-t0:.2f}s)")
    except Exception as e:
        out.setdefault("tcp", f"FAIL  {type(e).__name__}: {e}")
        out.setdefault("tls", "skipped (no TCP)")
    return out


def check_http(label, url, expect, ua=None):
    print(f"\n--- {label}")
    print(f"    {url[:110]}")
    if ua:
        print(f"    (using non-browser UA: {ua})")
    t0 = time.monotonic()
    try:
        # stream=True so a huge file isn't pulled down just to test reachability
        r = requests.get(url, headers={"User-Agent": ua or UA}, timeout=45,
                         verify=False, stream=True)
    except Exception as e:
        print(f"    RESULT   : REQUEST FAILED after {time.monotonic()-t0:.1f}s")
        print(f"    ERROR    : {type(e).__name__}: {str(e)[:300]}")
        return ("FAIL", f"{type(e).__name__}")

    elapsed = time.monotonic() - t0
    ctype = r.headers.get("Content-Type", "?")
    clen = r.headers.get("Content-Length", "?")
    print(f"    HTTP     : {r.status_code} {r.reason}   ({elapsed:.1f}s)")
    print(f"    type/len : {ctype}  /  {clen}")

    # Read only the first chunk — enough to tell real data from a block page
    try:
        head = next(r.iter_content(2048), b"") or b""
    except Exception as e:
        print(f"    BODY     : could not read body: {type(e).__name__}: {e}")
        r.close()
        return ("FAIL", "body-read")
    finally:
        try:
            r.close()
        except Exception:
            pass

    snippet = head[:220].decode("utf-8", errors="replace").replace("\n", " ")
    print(f"    first    : {snippet}")

    verdict = "OK"
    note = ""
    looks_html = head.lstrip()[:1] in (b"<",) or b"<html" in head[:400].lower()

    if r.status_code >= 400:
        verdict, note = "FAIL", f"HTTP {r.status_code}"
        if b"MediaTypeBlocked" in head or "MediaTypeBlocked" in str(r.reason):
            note = "PROXY MEDIA-TYPE BLOCK"
    elif looks_html and expect in ("json", "zip"):
        # The dangerous case: 200 OK, but it's the proxy's block/login page.
        verdict, note = "FAIL", "HTML PAGE WHERE DATA EXPECTED (proxy block/login?)"
    elif expect == "json":
        try:
            json.loads(head.decode("utf-8", errors="replace"))
            note = "valid JSON"
        except Exception:
            # a truncated 2KB read of a large JSON array won't parse; that's fine
            if head.lstrip()[:1] in (b"[", b"{"):
                note = "starts as JSON (truncated read, fine)"
            else:
                verdict, note = "FAIL", "not JSON"
    elif expect == "zip":
        note = "real ZIP" if head[:2] == b"PK" else "NOT a zip payload"
        if head[:2] != b"PK":
            verdict = "FAIL"

    print(f"    VERDICT  : {verdict}  {note}")
    return (verdict, note)


def main():
    print("\nDIAGNOSTIC — BCB and FAOSTAT reachability")
    print(f"Run at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    check_env()

    rule("2. NETWORK LAYERS (DNS / TCP / TLS) — before any HTTP")
    for host in ("api.bcb.gov.br", "olinda.bcb.gov.br", "www.bcb.gov.br",
                 "bulks-faostat.fao.org", "apisidra.ibge.gov.br"):
        print(f"\n  {host}")
        for layer, result in check_dns_tcp_tls(host).items():
            print(f"    {layer.upper():4}: {result}")

    results = {}
    rule("3. BANCO CENTRAL (BCB)")
    for t in BCB_TARGETS:
        results[t[0]] = check_http(*t)

    rule("4. FAOSTAT (Pulp & Paper source)")
    for t in FAO_TARGETS:
        results[t[0]] = check_http(*t)

    rule("5. CONTROLS (these work today — if they fail, it's the network generally)")
    for t in CONTROL_TARGETS:
        results[t[0]] = check_http(*t)

    rule("6. SUMMARY  (copy this whole output back)")
    for label, (verdict, note) in results.items():
        mark = "OK  " if verdict == "OK" else "FAIL"
        print(f"  [{mark}] {label[:62]:64} {note}")

    print("\n  How to read this:")
    print("   - DNS/TCP/TLS all OK but HTTP fails  -> the proxy is filtering, not the network")
    print("   - 'HTML PAGE WHERE DATA EXPECTED'    -> proxy block/login page; needs an IT allowlist")
    print("   - 'PROXY MEDIA-TYPE BLOCK'           -> the FAOSTAT case; IT must allow that media type")
    print("   - a DIFFERENT BCB host returning OK  -> fixable in code, no IT ticket needed")
    print("   - controls failing too               -> a general network problem, not these sources")
    print()


if __name__ == "__main__":
    main()

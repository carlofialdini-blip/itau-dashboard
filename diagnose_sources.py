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

# The Pulp & Paper page has THREE upstream sources, not one. FAOSTAT supplies
# the world production/trade/concentration series, IBGE PIM-PF supplies the
# current-month Brazil index, and MDIC Comex supplies the Brazil wood-pulp
# export chart. Comex is included here because it is also the step that timed
# out on the bank machine (ReadTimeout at 120s), and it feeds Mining and
# Agriculture too — so whatever it shows is useful well beyond this page.
FAO_TARGETS = [
    ("PULP: FAOSTAT bulk zip — the exact file the scraper downloads",
     "https://bulks-faostat.fao.org/production/Forestry_E_All_Data_(Normalized).zip", "zip"),
    ("PULP: FAOSTAT bulk host — plain path, same host (host vs media-type test)",
     "https://bulks-faostat.fao.org/production/", "any"),
    ("PULP: FAO main website — is the whole fao.org family blocked?",
     "https://www.fao.org/faostat/en/", "any"),
    ("PULP: IBGE PIM-PF — the Brazil pulp/paper monthly index (SIDRA t/8888)",
     "https://apisidra.ibge.gov.br/values/t/8888/n1/all/v/12606/p/last%206/c544/129324", "json"),
    ("PULP+MINING+AGRI: MDIC Comex CSV — timed out at 120s on the bank machine",
     f"https://balanca.economia.gov.br/balanca/bd/comexstat-bd/ncm/EXP_{date.today().year}.csv", "any"),
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

    # TCP and TLS are wrapped separately on purpose. An earlier version wrapped
    # both in one try/except and, when TCP succeeded but the TLS handshake
    # failed, reported "TLS: skipped (no TCP)" — flatly contradicting the
    # "TCP: OK" printed a line above, and swallowing the TLS error, which is
    # the single most diagnostic thing here. TCP-OK-but-TLS-reset is the
    # signature of SNI filtering: the firewall completes the TCP handshake,
    # reads the hostname out of the TLS ClientHello, and resets on a match.
    t0 = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=15)
    except Exception as e:
        out["tcp"] = f"FAIL  {type(e).__name__}: {e}"
        out["tls"] = "skipped (TCP never connected)"
        return out
    out["tcp"] = f"OK  ({time.monotonic()-t0:.2f}s)"

    t0 = time.monotonic()
    try:
        ctx = ssl.create_default_context()
        # Certs are deliberately ignored, so a TLS failure here can NOT be a
        # certificate problem — it is the handshake itself being refused.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = b""
            try:
                der = tls.getpeercert(binary_form=True) or b""
            except Exception:
                pass
            out["tls"] = (f"OK  {tls.version()}  cert={len(der)} bytes  "
                          f"({time.monotonic()-t0:.2f}s)")
    except Exception as e:
        out["tls"] = (f"FAIL after {time.monotonic()-t0:.2f}s  "
                      f"{type(e).__name__}: {e}   "
                      f"<-- TCP was fine; handshake refused (certs are NOT checked here)")
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return out


def discover_proxies():
    """Find the proxy the BROWSER uses, which Python is not currently using.

    This matters because the browser reaches bcb.gov.br and Python does not,
    while `proxy env vars: (none set)` says Python is connecting directly.
    On corporate Windows the browser almost always goes through a proxy
    configured by a PAC file (registry value AutoConfigURL), and Python's
    urllib.getproxies() reads only the *static* registry proxy — it does not
    fetch or evaluate a PAC. So a machine can be fully proxied for browsing
    while Python sees "no proxy" and connects direct into a firewall that
    resets the connection.

    Returns [(source, proxy_url)] candidates to retry the failing hosts with.
    """
    found = []
    rule("3. PROXY DISCOVERY  (what the browser uses that Python isn't)")

    try:
        from urllib.request import getproxies
        gp = getproxies()
        print(f"  urllib.getproxies()        : {gp or '(none)'}")
        for scheme, val in (gp or {}).items():
            if scheme in ("http", "https") and val:
                found.append((f"urllib getproxies[{scheme}]", val))
    except Exception as e:
        print(f"  urllib.getproxies()        : error {e}")

    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            for name in ("ProxyEnable", "ProxyServer", "AutoConfigURL"):
                try:
                    val, _ = winreg.QueryValueEx(key, name)
                    print(f"  registry {name:18}: {val}")
                    if name == "ProxyServer" and val:
                        found.append(("registry ProxyServer", val))
                    if name == "AutoConfigURL" and val:
                        found.extend(_proxies_from_pac(str(val)))
                except FileNotFoundError:
                    print(f"  registry {name:18}: (not set)")
            winreg.CloseKey(key)
        except Exception as e:
            print(f"  registry read              : error {e}")

        try:
            import subprocess
            out = subprocess.run(["netsh", "winhttp", "show", "proxy"],
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20)
            for line in (out.stdout or "").splitlines():
                if line.strip():
                    print(f"  netsh winhttp              | {line.strip()}")
        except Exception as e:
            print(f"  netsh winhttp              : error {e}")
    else:
        print("  (registry/netsh checks are Windows-only; skipped on this platform)")

    # de-dupe, normalise to a URL requests will accept
    norm, seen = [], set()
    for src, val in found:
        v = val.strip()
        if not v:
            continue
        if "=" in v and "://" not in v:          # "http=host:port;https=host:port"
            for part in v.split(";"):
                if "=" in part:
                    sch, _, hp = part.partition("=")
                    if sch.strip() in ("http", "https") and hp.strip():
                        cand = hp.strip()
                        cand = cand if "://" in cand else "http://" + cand
                        if cand not in seen:
                            seen.add(cand); norm.append((src, cand))
            continue
        cand = v if "://" in v else "http://" + v
        if cand not in seen:
            seen.add(cand); norm.append((src, cand))

    print(f"\n  -> {len(norm)} candidate proxy(ies) to retry through: "
          f"{[c for _s, c in norm] or 'NONE FOUND'}")
    if not norm:
        print("     If the browser genuinely uses a proxy, get it from:")
        print("       Windows Settings > Network & Internet > Proxy")
        print("     or in the browser: chrome://net-internals/#proxy")
    return norm


def _proxies_from_pac(pac_url):
    """Fetch the PAC file and pull literal 'PROXY host:port' entries out of it.

    A PAC is JavaScript and evaluating it properly needs an interpreter, but
    in practice the reachable proxies appear as literal strings, which is
    enough to test connectivity here.
    """
    out = []
    print(f"  PAC file                   : fetching {pac_url}")
    try:
        r = requests.get(pac_url, timeout=20, verify=False)
        body = r.text
        print(f"  PAC fetch                  : HTTP {r.status_code}, {len(body)} chars")
        import re
        hosts = re.findall(r"PROXY\s+([A-Za-z0-9_.\-]+:\d+)", body)
        uniq = list(dict.fromkeys(hosts))
        print(f"  PAC proxies found          : {uniq[:8] or '(none parsed)'}")
        for h in uniq[:5]:
            out.append(("PAC file", h))
    except Exception as e:
        print(f"  PAC fetch                  : FAILED {type(e).__name__}: {e}")
    return out


def check_http(label, url, expect, ua=None, proxies=None):
    print(f"\n--- {label}")
    print(f"    {url[:110]}")
    if ua:
        print(f"    (using non-browser UA: {ua})")
    t0 = time.monotonic()
    try:
        # stream=True so a huge file isn't pulled down just to test reachability
        r = requests.get(url, headers={"User-Agent": ua or UA}, timeout=45,
                         verify=False, stream=True, proxies=proxies)
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

    proxy_candidates = discover_proxies()
    for arg in sys.argv[1:]:
        cand = arg if "://" in arg else "http://" + arg
        print(f"  + proxy supplied on the command line: {cand}")
        proxy_candidates.append(("command line", cand))

    results = {}
    rule("4. BANCO CENTRAL (BCB)  — direct, no proxy")
    for t in BCB_TARGETS:
        results[t[0]] = check_http(*t)

    rule("5. PULP & PAPER PAGE — all three of its sources (+ Comex, shared)")
    for t in FAO_TARGETS:
        results[t[0]] = check_http(*t)

    rule("6. CONTROLS (these work today — if they fail, it's the network generally)")
    for t in CONTROL_TARGETS:
        results[t[0]] = check_http(*t)

    # THE DECIDING TEST. The browser reaches bcb.gov.br and Python does not,
    # so retry the three failing sources through the proxy the browser uses.
    # If they answer here, this is a configuration fix in the pipeline and no
    # IT ticket is needed; if they fail here too, it is a real network block.
    rule("7. RETRY THROUGH THE BROWSER'S PROXY  <-- the deciding test")
    if not proxy_candidates:
        print("  No proxy candidate found, so nothing to retry through.")
        print("  Please check Windows Settings > Network & Internet > Proxy and")
        print("  tell me the address/port (or the 'Use setup script' URL) shown there.")
    else:
        retry = [
            ("BCB OLINDA (Selic, last 1)",
             "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/1?formato=json", "json"),
            ("FAOSTAT bulk zip",
             "https://bulks-faostat.fao.org/production/Forestry_E_All_Data_(Normalized).zip", "zip"),
            ("MDIC Comex CSV",
             f"https://balanca.economia.gov.br/balanca/bd/comexstat-bd/ncm/EXP_{date.today().year}.csv", "any"),
        ]
        for src, proxy in proxy_candidates:
            print(f"\n  ### via {proxy}   (discovered from: {src})")
            pmap = {"http": proxy, "https": proxy}
            for label, url, expect in retry:
                v, n = check_http(f"{label}  [via proxy]", url, expect, proxies=pmap)
                results[f"VIA PROXY {proxy} — {label}"] = (v, n)

    rule("8. SUMMARY  (copy this whole output back)")
    for label, (verdict, note) in results.items():
        mark = "OK  " if verdict == "OK" else "FAIL"
        print(f"  [{mark}] {label[:62]:64} {note}")

    print("\n  How to read this:")
    print("   - TCP OK but TLS FAIL                -> SNI filtering: the firewall reads the")
    print("                                           hostname from the TLS handshake and resets")
    print("                                           (it is NOT a certificate problem — certs")
    print("                                            are not checked by this script at all)")
    print("   - 'VIA PROXY ... ' rows OK           -> FIXABLE IN CODE. Point the pipeline at that")
    print("                                           proxy; no IT ticket needed.")
    print("   - 'VIA PROXY ... ' rows FAIL too     -> a real network block; needs IT.")
    print("   - 'PROXY MEDIA-TYPE BLOCK'           -> the FAOSTAT case; IT must allow that media")
    print("                                           type, unless it succeeds via the proxy above")
    print("   - controls failing too               -> a general network problem, not these sources")
    print()


if __name__ == "__main__":
    main()

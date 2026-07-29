#!/usr/bin/env python3
"""
net_utils.py — shared HTTP fetch helper with retry/backoff.

Corporate networks and free public APIs (Google News RSS, BCB, IBGE) are
prone to transient connection timeouts. A single failed request should not
zero out an entire sector/dataset — retry a few times with backoff first.
"""

import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/122 Safari/537.36"
    )
}

_YF_SESSION = None


def get_yf_session():
    """A verify=False session for yfinance, built on the SAME HTTP backend
    yfinance itself uses, shared by every yfinance call site
    (core/generate_dashboard.py's _ck_yf(), events/events_generator.py's
    fetch_earnings()).

    yfinance builds its own internal HTTP client and never inherited this
    project's verify=False corporate-proxy workaround -- get_with_retry()
    above applies it to every direct requests.get() call, but
    yf.download()/yf.Ticker() bypass net_utils entirely unless a session is
    explicitly passed in. On a home network this is invisible; on the Itaú
    corporate network -- which intercepts HTTPS via a proxy whose CA isn't
    in the certifi bundle, the exact reason verify=False exists everywhere
    else in this project -- every yfinance call fails its TLS handshake,
    which callers' own broad excepts silently convert to empty data ("Data
    unavailable").

    The backend must be curl_cffi, NOT plain requests. yfinance's own
    _http.new_session() returns curl_cffi.requests.Session(impersonate=
    "chrome") specifically because Yahoo fingerprints the TLS handshake
    (JA3/JA4) and HTTP/2 settings: a plain requests.Session cannot
    replicate that and Yahoo answers it with HTTP 429
    "Too Many Requests. Rate limited." on the very first call, regardless
    of how little traffic was actually sent. Handing yfinance a plain
    requests.Session to carry verify=False therefore traded the corporate-
    proxy failure for an immediate rate-limit failure -- verified live:
    identical yf.download("^GSPC") returned 0 rows with a requests.Session
    and 125 rows with a curl_cffi one. Setting verify=False on a curl_cffi
    session keeps both properties at once, which is the whole point.

    The plain-requests fallback only runs if curl_cffi isn't importable
    (yfinance depends on it, so this is unlikely); it mirrors what
    yfinance's own fallback does -- a realistic User-Agent, and an
    acknowledgement that Yahoo may rate-limit it. This is a shared,
    cross-cutting concern (unlike most of this project's deliberately-
    duplicated per-file logic) because both call sites must apply the
    identical fix, same reasoning as core/trusted_sources.py.
    """
    global _YF_SESSION
    if _YF_SESSION is None:
        try:
            from curl_cffi import requests as curl_requests
            _YF_SESSION = curl_requests.Session(impersonate="chrome", verify=False)
        except ImportError:
            _YF_SESSION = requests.Session()
            _YF_SESSION.headers.update(DEFAULT_HEADERS)
            _YF_SESSION.verify = False
    return _YF_SESSION


_PROXY_LOCK = threading.Lock()
_PROXY_CANDIDATES = None   # None = not discovered yet; [] = discovered, none found
_PROXY_WORKING = None      # the first candidate that actually worked


def _discover_system_proxies():
    """Find the proxy the machine's *browser* uses, which Python does not.

    On the Itaú machine every bcb.gov.br host failed from Python with a
    ConnectionError -- TCP connected and the TLS handshake was then reset,
    the signature of SNI filtering -- while the same URL loaded fine in the
    browser. The reason is that the browser is proxied by a PAC file and
    Python is not: urllib's getproxies() reads only the *static* registry
    proxy and never fetches or evaluates a PAC, so a fully-proxied machine
    looks unproxied to Python and connects direct into the firewall.

    Confirmed by diagnose_sources.py on the real machine: BCB returned valid
    JSON through the discovered proxy in the same run where every direct
    attempt failed.

    Deliberately discovered at runtime rather than hardcoded. The working
    proxy is internal Itaú infrastructure and does not belong in a GitHub
    repo, and a hardcoded value would be wrong on every other machine.
    Returns [] on a normal (unproxied) machine, which makes all of this a
    no-op there.
    """
    found = []

    env_override = os.environ.get("DASHBOARD_PROXY", "").strip()
    if env_override:
        found.append(env_override)

    try:
        from urllib.request import getproxies
        for scheme, val in (getproxies() or {}).items():
            if scheme in ("http", "https") and val:
                found.append(val)
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
                if server:
                    found.append(str(server))
            except FileNotFoundError:
                pass
            try:
                pac_url, _ = winreg.QueryValueEx(key, "AutoConfigURL")
                if pac_url:
                    # A PAC is JavaScript; evaluating it properly needs an
                    # interpreter, but the reachable proxies appear as literal
                    # "PROXY host:port" strings, which is enough to try.
                    r = requests.get(str(pac_url), timeout=10, verify=False)
                    found.extend(re.findall(r"PROXY\s+([A-Za-z0-9_.\-]+:\d+)", r.text))
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
        except Exception:
            pass

    out, seen = [], set()
    for val in found:
        v = str(val).strip()
        if not v:
            continue
        parts = ([p.partition("=")[2] for p in v.split(";") if "=" in p]
                 if ("=" in v and "://" not in v) else [v])
        for cand in parts:
            cand = cand.strip()
            if not cand:
                continue
            cand = cand if "://" in cand else "http://" + cand
            if cand not in seen:
                seen.add(cand)
                out.append(cand)
    return out


def _proxy_candidates():
    global _PROXY_CANDIDATES
    if _PROXY_CANDIDATES is None:
        with _PROXY_LOCK:
            if _PROXY_CANDIDATES is None:      # re-check inside the lock
                _PROXY_CANDIDATES = _discover_system_proxies()
                if _PROXY_CANDIDATES:
                    print(f"    (system proxy detected, will use as fallback: "
                          f"{', '.join(_PROXY_CANDIDATES)})")
    return _PROXY_CANDIDATES


def _try_via_proxy(url, headers, timeout, **kwargs):
    """Last resort after every direct attempt failed. Returns a response or None.

    Only ever runs on a request that has ALREADY failed outright, so it cannot
    slow down or alter anything that currently works -- the alternative
    outcome at this point is an exception and an empty chart.
    """
    global _PROXY_WORKING
    candidates = _proxy_candidates()
    if not candidates:
        return None
    # Whichever proxy worked last time goes first, so the 16 BCB series after
    # the first one don't re-probe every candidate.
    ordered = ([_PROXY_WORKING] + [c for c in candidates if c != _PROXY_WORKING]
               if _PROXY_WORKING else list(candidates))
    for proxy in ordered:
        try:
            r = requests.get(url, headers=headers or DEFAULT_HEADERS,
                             timeout=min(timeout, 45), verify=False,
                             proxies={"http": proxy, "https": proxy}, **kwargs)
            r.raise_for_status()
            if _PROXY_WORKING != proxy:
                _PROXY_WORKING = proxy
                print(f"    (reached {url.split('/')[2]} via proxy {proxy})")
            return r
        except Exception:
            continue
    return None


def get_with_retry(url, *, headers=None, timeout=30, retries=3, backoff=2.0, **kwargs):
    """GET with retries on connection/timeout errors and transient 5xx
    server errors. A 502/503/504 means the *target* server had a bad
    moment (confirmed live: BGS's ogcapi.bgs.ac.uk returned a bare 502 on
    a normal query) -- retrying is exactly right there. A 4xx is a real
    client-side problem (bad URL, not found, auth) that a retry can't
    fix, so those still fail fast on the first attempt rather than
    wasting the retry budget.

    Defaults (timeout=30, retries=3) are deliberately more patient than a
    normal script's would be: this pipeline's real deployment target is a
    corporate network that proxies and inspects every request, which adds
    latency on top of already-slow public endpoints, and which has produced
    real mid-transfer connection resets (WinError 10054 from CONAB/IBGE).
    Callers that know their endpoint is heavy still pass a larger explicit
    timeout (the ~20MB ANP and FAOSTAT downloads use 60-120s).

    Raises the last exception if every attempt fails, so callers keep their
    existing try/except handling unchanged.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers or DEFAULT_HEADERS,
                              timeout=timeout, verify=False, **kwargs)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status is None or status < 500:
                raise
            last_exc = e
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
        if attempt < retries:
            time.sleep(backoff * attempt)

    # Every direct attempt failed. Before giving up, try the machine's own
    # proxy -- on the deployment target the direct path is SNI-filtered for
    # bcb.gov.br while the browser's proxy reaches it fine, and that one
    # difference silently emptied 17 series across Overview, Credit and
    # Agriculture. This runs only after a failure that would otherwise raise,
    # so nothing that currently succeeds is affected, and it is a no-op on a
    # machine with no proxy configured.
    fallback = _try_via_proxy(url, headers, timeout, **kwargs)
    if fallback is not None:
        return fallback
    raise last_exc


# Concurrency cap for get_many(). Deliberately modest: these fetches are
# almost all Google News RSS, which has no published rate limit and has
# already bitten this project once (the GDELT lockout, and Yahoo's 429).
# 6 in flight is a large speedup over sequential without looking like a
# burst from what is, on the deployment target, a shared corporate IP.
DEFAULT_WORKERS = 6


def get_many(urls, *, headers=None, timeout=30, retries=3, backoff=2.0,
             workers=DEFAULT_WORKERS, on_result=None, **kwargs):
    """Fetch several URLs concurrently. Returns [(url, response|None, exc|None)]
    in the SAME ORDER as `urls`, regardless of completion order.

    Order matters: callers merge/dedupe/score the results afterwards, and a
    scraper whose article ordering changed run-to-run purely because of thread
    scheduling would produce a needlessly noisy cache diff every refresh.

    Only the network wait is parallelised. Callers keep their parsing,
    scoring, dedup and cache-merge logic sequential and single-threaded, so
    none of that has to become thread-safe and behaviour stays identical to
    the sequential version -- just faster.

    Each URL still goes through get_with_retry(), so the retry/backoff and
    verify=False behaviour is unchanged. A failure is reported in the tuple
    rather than raised, because these scrapers are all built to continue past
    an individual query failing.

    on_result(index, url, response, exc) is called as each fetch finishes,
    for progress output. It runs on a worker thread, so keep it to printing.
    """
    urls = list(urls)
    results = [None] * len(urls)

    def one(i_url):
        i, url = i_url
        try:
            r = get_with_retry(url, headers=headers, timeout=timeout,
                               retries=retries, backoff=backoff, **kwargs)
            out = (url, r, None)
        except Exception as e:                      # noqa: BLE001 - reported, not raised
            out = (url, None, e)
        results[i] = out
        if on_result:
            try:
                on_result(i, url, out[1], out[2])
            except Exception:                        # noqa: BLE001 - progress must never break a fetch
                pass
        return out

    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(urls)))) as pool:
        list(pool.map(one, enumerate(urls)))
    return results

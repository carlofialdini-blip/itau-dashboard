#!/usr/bin/env python3
"""
net_utils.py — shared HTTP fetch helper with retry/backoff.

Corporate networks and free public APIs (Google News RSS, BCB, IBGE) are
prone to transient connection timeouts. A single failed request should not
zero out an entire sector/dataset — retry a few times with backoff first.
"""

import time

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/122 Safari/537.36"
    )
}

_YF_SESSION = None


def get_yf_session():
    """A requests.Session with verify=False, shared by every yfinance call
    site (core/generate_dashboard.py's _ck_yf(), events/events_generator.py's
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
    unavailable"). This is a shared, cross-cutting concern (unlike most of
    this project's deliberately-duplicated per-file logic) because both
    call sites must apply the identical fix, same reasoning as
    core/trusted_sources.py.
    """
    global _YF_SESSION
    if _YF_SESSION is None:
        _YF_SESSION = requests.Session()
        _YF_SESSION.verify = False
    return _YF_SESSION


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
    raise last_exc

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


def get_with_retry(url, *, headers=None, timeout=15, retries=2, backoff=2.0, **kwargs):
    """GET with retries on connection/timeout errors.

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
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise last_exc

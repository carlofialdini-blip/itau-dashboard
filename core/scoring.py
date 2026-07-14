#!/usr/bin/env python3
"""
scoring.py — shared news-importance bucketing.

Every scraper computes its own relevance_score() (source trust, keyword
hits, etc.) to decide what clears its MIN_SCORE filter. This module turns
that raw score into one of three buckets, using the same thresholds on
every page so "High" means the same thing on Cockpit, Brazil, China, and
Credit.

Thresholds are calibrated against each scraper's realistic score range:
  portfolio scraper.py:  max ~13 (source 3 + name 3 + alias 2 + kw 3 + fin 2)
  brazil/china scrapers: max ~10 (source 3 + region 2 + kw 3 + terms 2)
  credit scraper.py:     max ~9  (source 3 + kw 4 + terms 2)
"""

HIGH_MIN   = 8
MEDIUM_MIN = 5


def importance_bucket(score: int) -> str:
    if score >= HIGH_MIN:
        return "high"
    if score >= MEDIUM_MIN:
        return "medium"
    return "low"

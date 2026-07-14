# CLAUDE.md — Portfolio Intelligence Dashboard

Persistent project memory. Read this first in any new session before making suggestions or changing code. If it looks stale against the actual repo, reconcile it before proceeding.

---

## 1. Project Overview

- **Name:** Portfolio Intelligence Dashboard (internal working name; header reads "Portfolio Intelligence / Market Monitor")
- **Owner:** Carlo Fialdini (vittorio.fialdini@gmail.com), works at Itaú
- **Purpose:** Aggregate news, market data, and economic indicators for a tracked portfolio of Brazilian/global companies, plus dedicated Brazil, China, and Credit-market views. Built for internal team distribution — not a live web app.
- **Distribution model:** Generated **twice a day** into a single static `dashboard.html` file, then uploaded to SharePoint / sent directly to teammates. Recipients just open the file in a browser — no server, no install, no network calls at view time. This shaped almost every architectural decision below.
- **Tech stack:** Python 3 (stdlib + a handful of libs, see §5), Jinja2 templating, vanilla JS, Plotly.js (via CDN in the template). No frontend build step, no npm/Node runtime dependency for the app itself, no database.
- **Package manager:** pip, `requirements.txt` at repo root.
- **Repo:** `git@github.com:carlofialdini-blip/itau-dashboard.git` (GitHub account `carlofialdini-blip`, distinct from `carlofialdini` — SSH key is tied to the `-blip` account).

---

## 2. Architecture

### Folder structure
```
update_dashboard.py       # ENTRY POINT — run twice a day, refreshes everything
setup.py                  # ENTRY POINT — one-time per-machine bootstrap (pip install + first run)
requirements.txt
dashboard.html             # generated output (gitignored)
assets/
  itau_logo.png            # 120x120 processed logo, embedded as base64 into dashboard.html
Itaú.jpeg                  # original source logo (3840x3840), kept for future reprocessing
data/                      # generated caches (gitignored except portfolio.xlsx)
  portfolio.xlsx           # source list of tracked companies — columns: Company, Sector, Keywords
  news_cache.json           china_news_cache.json
  brazil_news_cache.json    credit_news_cache.json
  events.json               china_events.json
  brazil_events.json
templates/
  dashboard_template.html   # single Jinja2 template for the entire static site (all 4 pages)
core/
  generate_dashboard.py     # main builder: reads caches, fetches live chart data, renders template
  net_utils.py               # shared get_with_retry() HTTP helper (retry + backoff)
scrapers/                   # Google News RSS scrapers, one per page
  scraper.py                 # portfolio companies (from portfolio.xlsx)
  brazil_scraper.py          china_scraper.py          credit_scraper.py
events/                     # event-calendar generators, one per page
  events_generator.py        # earnings (yfinance) + COPOM (hardcoded) + IPCA/IBGE (API)
  brazil_events_generator.py # IBGE calendar + COPOM + BCB Focus Report (weekly Mondays)
  china_events_generator.py  # NBS/PBOC release dates (pattern-computed, no network) + conferences (hardcoded)
```

### Data flow (what `update_dashboard.py` runs, in order)
1. **Scrapers** (`scrapers/*.py`) — query Google News RSS per sector/company, score relevance, merge with existing cache, keep a 7-day day-balanced window → write `data/*_cache.json`.
2. **Event generators** (`events/*.py`) — build forward-looking event calendars (mix of live APIs and hardcoded annual schedules) → write `data/*_events.json`.
3. **`core/generate_dashboard.py`** — reads all cache files + `portfolio.xlsx`, fetches *live* chart data (BCB OLINDA API for Brazil/Credit macro series, chinadata.live API for China datasets, yfinance + BCB + FRED for the Cockpit market overview), renders `templates/dashboard_template.html` via Jinja2 → `dashboard.html`. Every chart's data is embedded inline as JSON in a `<script>` block — the file has zero `fetch()`/XHR calls, so it opens correctly via `file://` with no server.
4. **`update_dashboard.py`** orchestrates 1–3 via `subprocess`, **continues past individual step failures** (a network hiccup in one scraper doesn't block the rebuild — that source just keeps its last-cached data), 600s timeout per step.
5. **`setup.py`** = `pip install -r requirements.txt` then delegates to `update_dashboard.py`. Run once per machine (Carlo's Mac, the work Windows PC) — never sent to teammates.

### Pages / tabs in `dashboard.html`
- **Cockpit** (renamed from "Portfolio"): tabs are **Market Data** (first, default-active — Equities/Interest Rates/Exchange Rates/Commodities cards), **News Feed**, **Events Calendar**.
- **Brazil**: News / Events Calendar / Economic Data (BCB charts).
- **China**: News / Events Calendar / Economic Data (chinadata.live charts) / All Datasets (searchable catalog).
- **Credit**: News / Economic Data (BCB credit-market charts).

### Shared chart UI (standardized across all 4 pages)
- Every chart renders with Plotly's native modebar **off** (`displayModeBar:false`). Each card instead has small icon buttons in its header: **PNG**, **CSV**, **Expand**.
- **Expand** opens one shared modal (`#chartModal` / `.chart-modal*` CSS, `chartExpand()` in the template's JS) used by every page — not a per-page component.
- Cockpit's FX cards (Exchange Rates group) are stat-only by design — big number + CSV download, no chart, no expand.
- Brazil/China/Credit's Economic Data grids render as **one flat grid per page** (no per-sector section breaks — see §4 for why); sector is shown via a colored left border + small tag per card instead.

---

## 3. Coding Standards

- **No comments explaining *what*** — code is expected to be self-descriptive via naming. Comments are reserved for non-obvious *why* (a workaround, a confirmed-bad external API, a subtle invariant).
- **Every scraper/event-generator file is self-contained**: each defines its own `HEADERS`, does its own `urllib3.disable_warnings()` + `sys.stdout.reconfigure()` at import time, computes `ROOT = Path(__file__).resolve().parent.parent`. This duplication is intentional — matches the project's "few files, each independently runnable" philosophy — except for genuinely cross-cutting concerns (HTTP retry logic), which live in `core/net_utils.py`.
- **All HTTP calls use `verify=False`** + `urllib3.disable_warnings(InsecureRequestWarning)` — required because the target corporate network (Itaú) intercepts HTTPS via a proxy with its own CA not in the `certifi` bundle. This is deliberate, not an oversight — do not "fix" it by re-adding cert verification without checking with Carlo first.
- **All HTTP calls go through `core.net_utils.get_with_retry()`** (2 attempts, backoff) rather than raw `requests.get()` — single-attempt calls were the root cause of a whole class of "0 articles fetched" bugs on flaky/corporate networks.
- **Windows compatibility:** every entry-point script calls `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` and every `subprocess.run()` passes `encoding="utf-8"` — Windows terminals default to CP1252 and choke on unicode (`→`, `—`, `…`) otherwise.
- **Timezone discipline:** all article/display timestamps must be in **Brasília time** (`America/Sao_Paulo`), not raw UTC from feed sources. Centralized in `parse_date()` in `core/generate_dashboard.py` — convert there, not at each call site.
- **No premature abstraction:** the 4 scrapers and 3 event generators are near-duplicates of each other by design (different queries/sectors) rather than parameterized into one generic module — this was a deliberate choice matching "few files, easy to reason about one at a time" over DRY-at-all-costs.
- **Testing:** none currently. No test suite, no CI. Verification during this project's development has been manual: run the actual scraper/generator, inspect real output counts, syntax-check generated JS with `node --check`.

---

## 4. Important Decisions

Chronological, most-recent last. Only decisions with lasting architectural consequence — see git log for the full incremental history.

- **Windows CP1252 fix** — `sys.stdout.reconfigure()` + `encoding="utf-8"` everywhere. *Why:* Windows terminal crashed on unicode output. *Impact:* every script touches this pattern at import time.
- **`verify=False` on all requests** — bypasses the corporate SSL proxy's untrusted CA. *Why:* certifi-based fixes didn't work; this is the bank's network doing TLS interception, confirmed by Carlo. *Impact:* `certifi` dependency removed entirely.
- **Fully static output, no server** — removed the refresh button and (later) deleted `core/server.py` entirely. *Why:* the real usage pattern is generate-then-email/upload, not "browse a running server." *Impact:* `dashboard.html` has zero `fetch()` calls; all chart/news data is baked in as inline JSON at generation time.
- **News window: 1 day → 7 days, fixed in two places** — raising `MAX_NEWS_AGE_DAYS` in `generate_dashboard.py` alone wasn't enough; each scraper had its *own* `MAX_AGE_DAYS=1` and a `balance_by_day()` that only bucketed "today" vs "yesterday", silently discarding anything older even after the render-time gate was widened. *Fix:* raised `MAX_AGE_DAYS` to 7 in all 4 scrapers + added a third "older" fallback bucket to `balance_by_day()` in 5 places (4 scrapers + `generate_dashboard.py`). *Lesson:* when a filter exists in multiple layers, check all of them.
- **Retry logic (`core/net_utils.get_with_retry`)** — single-attempt `requests.get()` calls were silently zeroing out entire sectors/datasets on transient timeouts. *Impact:* wired into every fetch site; bumped `update_dashboard.py`'s per-script subprocess timeout from 180s → 600s to give retries headroom without false-timeout aborts.
- **PIX BHKP/NBSK pulp proxies → Suzano/Klabin stocks** — FOEX's real pulp index is paywalled; the FRED series originally used (WPU0912, US PPI) was a meaningless proxy. *Fix:* replaced with `SUZB3.SA` / `KLBN11.SA` (B3-listed, liquid, fetchable via yfinance) as pragmatic proxies.
- **Brazil 10Y → Brazil Selic Rate** — BCB SGS series 12511 (ETTJ ~10Y) returns 502/timeout directly from BCB's own infrastructure, confirmed by testing other series which respond in <1s. *Fix:* swapped to series 432 (Selic policy rate, already proven reliable elsewhere in the codebase) and **relabeled honestly** rather than mislabel a different metric as a 10-year yield.
- **File reorg: root-level `update_dashboard.py` + `setup.py`, `core/server.py` deleted** — matches actual usage: Carlo runs `update_dashboard.py` twice daily, `setup.py` once per machine, teammates only ever receive `dashboard.html`. Also deleted `templates/china_template.html` (610 lines, unreferenced dead file from before China was folded into the unified template).
- **Cockpit Market Overview: above news → below news → own "Market Data" tab (first, default-active)** — iterated per direct user feedback; settled on tab pattern to match Brazil/China/Credit's existing tab-first convention.
- **Chart UI standardization** — disabled Plotly's native modebar everywhere (was overlapping tiny 100px Cockpit cards); unified every chart card to the same PNG/CSV/Expand icon-button row; generalized the Cockpit-only "mkt-modal" into one shared `.chart-modal` used by all 4 pages. Also fixed a bug where the modal plotted its chart *before* being shown (`display:none` at plot time → zero-width render) — fixed by show-then-wait-two-frames-then-plot.
- **Removed per-sector section headers in Economic Data grids** — each sector previously started its own CSS auto-fill grid row, leaving visible blank slots whenever a sector's dataset count didn't evenly fill a row. Flattened to one grid per page; sector now shown via a colored left border + small tag instead of a hard row break.
- **Itaú logo embedded as base64** — resized the original 3840×3840 source (`Itaú.jpeg`) down to 120×120 (`assets/itau_logo.png`) before embedding, to avoid bloating `dashboard.html` by ~500KB for a header icon.
- **Timestamps converted to Brasília time at the source** — Google News RSS publishes in UTC; `parse_date()` (the single choke point used by all 4 news feeds) now converts via `zoneinfo.ZoneInfo("America/Sao_Paulo")` immediately after parsing, so every downstream display (article time, header "Updated" stamp) is correct without touching each call site individually.
- **News importance badges (Low/Medium/High)** — every scraper already computed a `relevance_score()` to decide what clears `MIN_SCORE`, then discarded it (`del a["_score"]`). Kept it instead: `core/scoring.py` buckets it (`importance_bucket()`, thresholds High ≥8 / Medium ≥5 / Low below, same on every page), each scraper persists it as `importance_score` + `importance`, and the template shows it as a small badge on every news card. Sort order stays strictly recency-based per explicit instruction — importance is informational only, not a resort key. Thresholds were calibrated against real re-scraped data, not guessed: every bucket has meaningful representation on all 4 pages (see git log commit `1e731d9`).

---

## 5. Dependencies

From `requirements.txt`:
- **pandas** — reads `portfolio.xlsx` (company/sector/keyword list driving the portfolio scraper and events generator).
- **openpyxl** — Excel engine for pandas.
- **feedparser** — parses Google News RSS responses.
- **requests** — all HTTP calls (wrapped by `core/net_utils.get_with_retry`).
- **jinja2** — renders `templates/dashboard_template.html` → `dashboard.html`.
- **yfinance** — equities/FX/rate/commodity price series for the Cockpit market overview and portfolio earnings dates.
- **lxml** — feedparser's XML parser backend.

Not in requirements.txt but used once, out-of-band: **Pillow** — used interactively to resize the source `Itaú.jpeg` down to `assets/itau_logo.png`. Not a runtime dependency of the build pipeline; don't add it to `requirements.txt` unless a future feature needs image processing at build time.

Removed: **certifi** (was part of an earlier, unsuccessful attempt to fix the corporate-proxy SSL issue; superseded by `verify=False`).

Frontend: **Plotly.js** via CDN `<script>` tag in the template (not an npm dependency — there is no `package.json`, no Node build step for the app).

---

## 6. Environment

- **No environment variables required** — no API keys. All data sources used are public/no-auth: Google News RSS, BCB OLINDA API, IBGE calendar API, chinadata.live, FRED public CSV endpoint, Yahoo Finance (via yfinance).
- **External services relied on:** news.google.com (RSS), api.bcb.gov.br, servicodados.ibge.gov.br, chinadata.live, fred.stlouisfed.org, Yahoo Finance (via yfinance). All are free/public; none have documented SLAs — see §8 for the BCB series-12511 cautionary tale.
- **Commands:**
  - First-time setup (once per machine): `python3 setup.py`
  - Daily refresh (run twice a day): `python3 update_dashboard.py`
  - Individual pieces can also be run standalone for debugging, e.g. `python3 scrapers/brazil_scraper.py`, `python3 core/generate_dashboard.py` (uses whatever's already in `data/*.json`).
- **No build step, no test command, no deployment** — output is a single HTML file manually uploaded to SharePoint / sent to the team.

---

## 7. Current Progress

### Working
- All 4 news scrapers (portfolio, Brazil, China, Credit) — 7-day window, retry-hardened.
- All 3 event generators.
- Cockpit market overview (Equities, Interest Rates, Exchange Rates, Commodities) — 11 of 12 metrics live via yfinance/BCB; Brazil rate metric uses Selic (see decision above).
- Brazil/China/Credit Economic Data charts (BCB + chinadata.live).
- Standardized chart UI (PNG/CSV/Expand) across every page, shared modal.
- Brasília-time display for all article/header timestamps.
- Itaú logo in header.
- News importance badges (Low/Medium/High) on every card, all 4 pages, sort still strict recency.
- Root-level `update_dashboard.py` / `setup.py` workflow, verified end-to-end.
- `IDEAS.md` — prioritized backlog of future enhancements (Tier 1 item #1, importance scoring, now shipped).

### Known bugs / technical debt
- None currently open that I'm aware of — most recently reported issues (modal overlap, blank Economic Data grid slots, Brazil 10Y empty, wrong timezone) have been fixed and verified.
- No automated tests exist. All verification has been manual (run scripts, inspect counts, `node --check` on extracted JS).
- Original `Itaú.jpeg` (398KB, 3840×3840) is committed to the repo even though only the small processed `assets/itau_logo.png` is used by the build — kept intentionally as a reprocessing source, not dead weight to clean up.

### Next recommended tasks
- See `IDEAS.md` for the full prioritized backlog. Tier 1 item #1 (importance badges) is done; item #2 (Data Freshness / Staleness Indicators) is the next-cheapest, highest-value pick — the `last_updated` timestamp each scraper already writes is currently never read anywhere.
- Otherwise: wait for feedback from Carlo/his team once the dashboard is actually circulated, rather than building further ahead of confirmed need.

---

## 8. Known Constraints

- **BCB SGS series can silently 502** — confirmed with series 12511 (returned 502/timeout on every attempt while other series responded in <1s). Before wiring up a new BCB series, sanity-check it responds with a minimal `ultimos/1` query before trusting it in the pipeline.
- **FOEX pulp/paper index (PIX BHKP/NBSK) is paywalled** — no free public source exists; Suzano/Klabin stock prices are used as a labeled proxy, not the real index. If a real feed is ever licensed, this should be swapped back.
- **Google News RSS is not an official API** — no SLA, no guaranteed rate limits, prone to transient timeouts especially over corporate networks. Retry logic mitigates but does not eliminate this.
- **`dashboard.html` must remain a single self-contained file** — no external asset references, no `fetch()` calls. Any new feature that would require a live network call from the browser breaks the core distribution model (email/SharePoint a file, open with no server).
- **Corporate network intercepts HTTPS** — `verify=False` is required for the pipeline to run at all from the Itaú work machine. This is a known, accepted risk in this specific context (internal tool, non-sensitive public data sources only) — do not treat it as an oversight to silently "fix."
- **No test suite** — changes should be manually verified by actually running the affected script(s) and inspecting real output before considering a fix complete.

---

## 9. Project Rules

- **Never reintroduce a live server or a "Refresh" button in the UI** — the distribution model is generate-once, share-the-file. This was deliberately removed; don't add it back without explicit direction.
- **Never commit `dashboard.html` or `data/*.json`** — both are gitignored generated output. Only `data/portfolio.xlsx` (source data) is tracked.
- **Never relabel a data source as something it isn't** — if a metric can't be reliably fetched (e.g. Brazil 10Y), replace it with a real, correctly-labeled alternative rather than silently substituting different data under the original label.
- **Keep every chart's interaction pattern identical across pages** — PNG/CSV/Expand icon buttons, shared modal, `displayModeBar:false`. Don't introduce a page-specific chart UI variant without updating this file and reconciling the others.
- **Any new HTTP call must use `core.net_utils.get_with_retry()`**, not raw `requests.get()`.
- **Any new age/freshness filter on news articles must be checked in every layer it exists** — this codebase has been bitten once by a filter existing in both the scraper and the renderer with different values.
- **Don't add `certifi`-based cert verification back** without confirming with Carlo — it breaks on the corporate network.

---

## 10. Session Handoff

### Last completed task
Shipped news importance badges (Low/Medium/High) on every news card across all 4 pages — Tier 1 item #1 from `IDEAS.md`. Also created `IDEAS.md` itself this session (a prioritized backlog from Carlo's feature-idea list, reorganized into cost/value tiers, with a few items flagged as conflicting with his stated "depth over volume" goal).

### Files modified
- `core/scoring.py` (new) — shared `importance_bucket()`.
- `core/generate_dashboard.py` — pass `importance` through in all 4 news-loading functions.
- `scrapers/{scraper,brazil_scraper,china_scraper,credit_scraper}.py` — keep the relevance score instead of deleting it; compute and persist `importance`.
- `templates/dashboard_template.html` — badge CSS + markup on all 4 news-card blocks.
- `IDEAS.md` (new) — the backlog; item #1 now marked done.
- `CLAUDE.md` — this update.

### Current state
Dashboard pipeline is stable and fully verified as of commit `1e731d9` ("Add news importance badges (Low/Medium/High) to every news card"). All 4 news feeds now carry and display importance badges; verified against real re-scraped data (not just synthetic checks) that thresholds produce a sensible spread on every page and that recency sort is untouched. Everything from prior sessions (chart UI, Brasília timestamps, Itaú logo, static-file workflow) still holds.

### Next recommended task
`IDEAS.md` Tier 1 item #2 — Data Freshness / Staleness Indicators. The `last_updated` field every scraper/generator already writes to its cache file is currently never read by anything; surfacing it (an "as of HH:MM" per section, flagged if stale) closes a real visibility gap left by the retry/graceful-degradation work. Cheap, same shape as the work just finished (read a field that already exists, wire it through `generate_dashboard.py`, render it).

### Notes for future Claude
- This file (§1–9) is the durable context. Read it fully before touching code. `IDEAS.md` is the backlog — check it before proposing new features so you don't re-litigate a prioritization decision Carlo already made.
- The project has gone through several rounds of "user reports X is broken/inconsistent → root-cause it, don't just patch the symptom" — e.g. the news-age bug existed in 5 places, not 1; the BCB 12511 issue was confirmed via direct curl testing to be BCB's fault, not a network flakiness issue, before deciding to replace it. Keep that standard: verify root cause with actual tool calls (curl, run the script, inspect real output) before claiming a fix.
- Carlo is non-error-tolerant about financial data accuracy — when a data source is broken, prefer "clearly labeled fallback" or "honest N/A" over "quietly wrong number." This shaped the Brazil 10Y → Selic decision.
- The user has been iterating rapidly on UI/UX feedback in short messages (e.g. "put it below the news," then "actually make it a tab," then "make the tab first"). Expect continued fast iteration; keep changes scoped to exactly what's asked rather than over-building ahead of confirmed direction.
- When Carlo asks for discussion/planning before coding (as he did for `IDEAS.md`), respect that explicitly — produce the artifact requested, give a direct opinion when asked, but don't start implementing until he confirms scope. He confirmed scope for importance badges in a short back-and-forth (Low/Medium/High, every card, keep recency sort, same thresholds everywhere) before any code was written.

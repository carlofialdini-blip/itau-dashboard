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
  br_ettj_history.json      # accumulating Brazil 10Y yield history (ANBIMA), grows one point/day
templates/
  dashboard_template.html   # single Jinja2 template for the entire static site (5 pages)
core/
  generate_dashboard.py     # main builder: reads caches, fetches live chart data, renders template
  net_utils.py               # shared get_with_retry() HTTP helper (retry + backoff)
  scoring.py                  # shared importance_bucket() (Low/Medium/High)
scrapers/                   # Google News RSS scrapers, one per source (not per page — see below)
  scraper.py                 # portfolio companies (from portfolio.xlsx)
  brazil_scraper.py          china_scraper.py          credit_scraper.py
events/                     # event-calendar generators, one per source
  events_generator.py        # earnings (yfinance) + COPOM (hardcoded) + IPCA/IBGE (API)
  brazil_events_generator.py # IBGE calendar + COPOM + BCB Focus Report (weekly Mondays)
  china_events_generator.py  # NBS/PBOC release dates (pattern-computed, no network) + conferences (hardcoded)
```

### Data flow (what `update_dashboard.py` runs, in order)
1. **Scrapers** (`scrapers/*.py`) — query Google News RSS per sector/company, score relevance, merge with existing cache → write `data/*_cache.json`. **Freshness policy differs by source**: `brazil_scraper.py` is strict same-day-only (Brasília calendar date), with per-keyword querying (not one OR-mega-query — see §4) and score-based backfill to a 30-article-per-sector floor; `scraper.py`/`china_scraper.py`/`credit_scraper.py` keep a 7-day day-balanced window (today/yesterday/older fallback buckets).
2. **Event generators** (`events/*.py`) — build forward-looking event calendars (mix of live APIs and hardcoded annual schedules) → write `data/*_events.json`.
3. **`core/generate_dashboard.py`** — reads all cache files + `portfolio.xlsx`, fetches *live* chart data (BCB OLINDA API for Brazil/Credit macro series, chinadata.live API for China datasets, yfinance + BCB + ANBIMA for the Cockpit market overview), **merges all 4 news sources and all 3 event sources into two unified feeds** (`load_unified_news()`, `load_unified_events()` — see Architecture below), renders `templates/dashboard_template.html` via Jinja2 → `dashboard.html`. Every chart's data is embedded inline as JSON in a `<script>` block — the file has zero `fetch()`/XHR calls, so it opens correctly via `file://` with no server.
4. **`update_dashboard.py`** orchestrates 1–3 via `subprocess`, **continues past individual step failures** (a network hiccup in one scraper doesn't block the rebuild — that source just keeps its last-cached data), 600s timeout per step.
5. **`setup.py`** = `pip install -r requirements.txt` then delegates to `update_dashboard.py`. Run once per machine (Carlo's Mac, the work Windows PC) — never sent to teammates.

### Pages in `dashboard.html`
As of the July 2026 reorganization, the dashboard is organized by **content type**, not by source — one News page instead of four separate news feeds, one Economic Data page instead of three chart grids, one Calendar instead of three event lists. Scrapers/generators are still one-per-source internally (see Data flow above); the merging into unified pages happens once, in `core/generate_dashboard.py`, not in the underlying pipeline.

- **Cockpit** — Market Overview only (Equities/Interest Rates/Exchange Rates/Commodities cards). No tabs — this is the one page that stayed single-purpose, since "market data" doesn't fit the news/data/calendar shape the other three sources share.
- **News** — unified feed: Portfolio + Brazil + China + Credit articles, one list sorted strictly by recency (never resorted by importance). Filters: search box, **Country** (Brazil/China — Portfolio and Credit are both tagged Brazil since portfolio companies are B3-listed and credit news is the Brazilian credit market), **Sector** (one flat merged list across all 4 sources — Credit's categories like "Recuperação Judicial" sit in the same dropdown as Brazil's/China's sector names), **Company** (populated only from the ~10 portfolio-tracked companies — the only source with company-level data).
- **Economic Data** — unified chart grid: all Brazil (BCB) + China (chinadata.live) + Credit (BCB) dataset cards in one flat grid, client-side-tagged by source and filterable via pills (All/Brazil/China/Credit). The merge happens in the browser (`buildEconCharts()` in the template's JS combines the three `*_DATASETS` JS globals with a source tag) — no Python-side data model change was needed for this page.
- **Calendar** — unified event list: Brazil + China events, with Portfolio's (Cockpit's) earnings/COPOM events folded into the Brazil bucket. Filter: Country pills (All/Brazil/China).
- **Other Datasets** — the China dataset catalog browser (`buildCatalog()`), relocated to its own top-level page. Contents unchanged, stays China-only.

### Shared chart UI (standardized across every chart on every page)
- Every chart renders with Plotly's native modebar **off** (`displayModeBar:false`). Each card instead has small icon buttons in its header: **PNG**, **CSV**, **Expand**.
- **Expand** opens one shared modal (`#chartModal` / `.chart-modal*` CSS, `chartExpand()` in the template's JS) used by every chart — not a per-page component.
- Cockpit's FX cards (Exchange Rates group) are stat-only by design — big number + CSV download, no chart, no expand.
- The Economic Data page's chart grid is **one flat grid** for all 3 sources combined (no per-sector or per-source section breaks — see §4 for why); sector is shown via a colored left border + small tag per card, source via the pill filter, instead of hard row/page breaks.

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
- **Brazil 10Y: 12511 (broken) → Selic (wrong metric) → ANBIMA ETTJ (correct, real)** — first swapped the broken BCB series 12511 for the Selic policy rate as an honest-but-different fallback; Carlo rejected that outright ("the values of the brazilian interest is not actually the future curve but actually the selic, which is not the same thing"). Found ANBIMA's free daily ETTJ download (`https://www.anbima.com.br/informacoes/est-termo/CZ-down.asp?Dt_Ref=DD/MM/YYYY`) — a real, market-derived nominal yield curve, verified against independent reporting (fetched 14.44% vs. investing.com's reported 14.43% for the same day). The free endpoint serves one date per request and only retains a few months of history, so `fetch_br10y()` accumulates a local `data/br_ettj_history.json` (same merge/append pattern as the news caches): a short backfill on first run, then just the newest day appended each subsequent run.
- **File reorg: root-level `update_dashboard.py` + `setup.py`, `core/server.py` deleted** — matches actual usage: Carlo runs `update_dashboard.py` twice daily, `setup.py` once per machine, teammates only ever receive `dashboard.html`. Also deleted `templates/china_template.html` (610 lines, unreferenced dead file from before China was folded into the unified template).
- **Cockpit Market Overview: above news → below news → own "Market Data" tab (first, default-active)** — iterated per direct user feedback; settled on tab pattern to match Brazil/China/Credit's existing tab-first convention.
- **Chart UI standardization** — disabled Plotly's native modebar everywhere (was overlapping tiny 100px Cockpit cards); unified every chart card to the same PNG/CSV/Expand icon-button row; generalized the Cockpit-only "mkt-modal" into one shared `.chart-modal` used by all 4 pages. Also fixed a bug where the modal plotted its chart *before* being shown (`display:none` at plot time → zero-width render) — fixed by show-then-wait-two-frames-then-plot.
- **Removed per-sector section headers in Economic Data grids** — each sector previously started its own CSS auto-fill grid row, leaving visible blank slots whenever a sector's dataset count didn't evenly fill a row. Flattened to one grid per page; sector now shown via a colored left border + small tag instead of a hard row break.
- **Itaú logo embedded as base64** — resized the original 3840×3840 source (`Itaú.jpeg`) down to 120×120 (`assets/itau_logo.png`) before embedding, to avoid bloating `dashboard.html` by ~500KB for a header icon.
- **Timestamps converted to Brasília time at the source** — Google News RSS publishes in UTC; `parse_date()` (the single choke point used by all 4 news feeds) now converts via `zoneinfo.ZoneInfo("America/Sao_Paulo")` immediately after parsing, so every downstream display (article time, header "Updated" stamp) is correct without touching each call site individually.
- **News importance badges (Low/Medium/High)** — every scraper already computed a `relevance_score()` to decide what clears `MIN_SCORE`, then discarded it (`del a["_score"]`). Kept it instead: `core/scoring.py` buckets it (`importance_bucket()`, thresholds High ≥8 / Medium ≥5 / Low below, same on every page), each scraper persists it as `importance_score` + `importance`, and the template shows it as a small badge on every news card. Sort order stays strictly recency-based per explicit instruction — importance is informational only, not a resort key. Thresholds were calibrated against real re-scraped data, not guessed: every bucket has meaningful representation on all 4 pages (see git log commit `1e731d9`).
- **Brazil scraper: 7-day window → strict same-day, generic keywords → technical per-sector keywords** — Carlo wanted Brazil news restricted to today only (Brasília calendar date) with analyst-level search terms per sector instead of generic ones, while still hitting a 30-article floor per sector. Discovered via direct testing that Google News RSS ranks results by *relevance*, not recency, and caps each query at ~100 entries — a single broad OR-query per sector returned **zero** today-dated articles out of 100 results, because the query's relevance budget got spent on one dominant sub-topic. Fixed by querying each keyword *separately* (one request per term, not one OR-mega-query), which surfaced real same-day results the mega-query missed entirely. Confirmed empirically that some niche sectors (Pulp & Paper, Energy) genuinely don't have 30 same-day articles most of the time — the 30-floor is best-effort with score-based backfill (same-day candidates only, never reaching into other days), not a guarantee, and that's a real content-volume ceiling, not a bug.
- **Major reorg: 4 source-pages → 5 content-type pages** — Cockpit/Brazil/China/Credit (each reimplementing its own News/Events/Charts tabs) replaced with Cockpit/News/Economic Data/Calendar/Other Datasets, each internally filterable instead of hard-partitioned by source. Planned via `EnterPlanMode` given the scope (touches nearly all of `templates/dashboard_template.html` and `core/generate_dashboard.py`'s render pipeline); full plan and the resolved ambiguities (country tagging, no separate "topic" axis, Calendar scope) are in the approved plan — see git log commit for this reorg for the plan file reference. Key resolutions: Sector is one flat merged list (Credit's categories sit alongside Brazil's/China's, no separate "topic" filter); Portfolio and Credit news/events are tagged `country="Brazil"` (Portfolio companies are all B3-listed, Credit news is the Brazilian credit market); Portfolio's COPOM entries are deduplicated against Brazil's (both generators independently schedule COPOM with byte-identical titles) rather than shown twice. Scrapers/event-generators themselves are untouched — the merge happens once, in two new functions in `core/generate_dashboard.py` (`load_unified_news()`, `load_unified_events()`), keeping each source independently refreshable.

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

- **No environment variables required** — no API keys. All data sources used are public/no-auth: Google News RSS, BCB OLINDA API, IBGE calendar API, chinadata.live, FRED public CSV endpoint, ANBIMA's free ETTJ download, Yahoo Finance (via yfinance).
- **External services relied on:** news.google.com (RSS), api.bcb.gov.br, servicodados.ibge.gov.br, chinadata.live, fred.stlouisfed.org, anbima.com.br, Yahoo Finance (via yfinance). All are free/public; none have documented SLAs — see §8 for the BCB series-12511 cautionary tale.
- **Commands:**
  - First-time setup (once per machine): `python3 setup.py`
  - Daily refresh (run twice a day): `python3 update_dashboard.py`
  - Individual pieces can also be run standalone for debugging, e.g. `python3 scrapers/brazil_scraper.py`, `python3 core/generate_dashboard.py` (uses whatever's already in `data/*.json`).
- **No build step, no test command, no deployment** — output is a single HTML file manually uploaded to SharePoint / sent to the team.

---

## 7. Current Progress

### Working
- All 4 news scrapers (portfolio, Brazil, China, Credit) — Brazil is strict same-day with technical per-sector keywords, the other 3 keep a 7-day window; all retry-hardened.
- All 3 event generators.
- Cockpit market overview (Equities, Interest Rates, Exchange Rates, Commodities) — 12 of 12 metrics live via yfinance/BCB/ANBIMA; Brazil rate metric is the real ANBIMA 10Y nominal yield curve (not Selic, not a proxy).
- **5-page reorg live**: Cockpit / News (unified 4-source feed) / Economic Data (unified 3-source chart grid) / Calendar (unified Brazil+China events) / Other Datasets (China catalog). Old Brazil/China/Credit source-pages no longer exist.
- Standardized chart UI (PNG/CSV/Expand) across every chart, shared modal.
- Brasília-time display for all article/header timestamps.
- Itaú logo in header.
- News importance badges (Low/Medium/High) on every card, sort still strict recency.
- Root-level `update_dashboard.py` / `setup.py` workflow, verified end-to-end.
- `IDEAS.md` — prioritized backlog of future enhancements (Tier 1 item #1, importance scoring, shipped).

### Known bugs / technical debt
- None currently open that I'm aware of — verified the 5-page reorg structurally (page containers, JS syntax, unified row/event counts, COPOM dedup) but **has not been visually reviewed in an actual browser** — this environment can't render one. Recommend Carlo click through all 5 pages/filters before this goes to the team.
- No automated tests exist. All verification has been manual (run scripts, inspect counts, `node --check` on extracted JS).
- Original `Itaú.jpeg` (398KB, 3840×3840) is committed to the repo even though only the small processed `assets/itau_logo.png` is used by the build — kept intentionally as a reprocessing source, not dead weight to clean up.

### Next recommended tasks
- Get Carlo's visual sign-off on the reorg (see above) before anything else — this changed the whole navigation model of a document his team opens directly.
- See `IDEAS.md` for the full prioritized backlog. Tier 1 item #1 (importance badges) is done; item #2 (Data Freshness/Staleness) was proposed and **rejected by Carlo — no perceived value**, don't re-suggest it. Next candidates: #3 ("What's New Since Previous Update"), #4 (Analyst Notes), #5 (Macro Calendar Importance) — awaiting Carlo's pick.
- Otherwise: wait for feedback from Carlo/his team once the dashboard is actually circulated, rather than building further ahead of confirmed need.

---

## 8. Known Constraints

- **BCB SGS series can silently 502** — confirmed with series 12511 (returned 502/timeout on every attempt while other series responded in <1s). Before wiring up a new BCB series, sanity-check it responds with a minimal `ultimos/1` query before trusting it in the pipeline.
- **ANBIMA's free ETTJ download only retains a few months of history** — `CZ-down.asp?Dt_Ref=DD/MM/YYYY` returns real data for roughly the last ~3-4 months and empty responses further back (confirmed by testing: mid-March 2026 onward worked, January/2025 dates didn't, from a "today" of July 2026). This is why `fetch_br10y()` accumulates its own history file rather than trying to bulk-backfill — there's nothing further back to backfill from, and it would be needlessly hammering ANBIMA's server to try.
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
- **Don't reintroduce per-source pages** (a standalone "Brazil page," "China page," etc.) — the dashboard is organized by content type (News / Economic Data / Calendar / Cockpit / Other Datasets), each filterable by country/sector/company internally. This was a deliberate, explicitly-requested reorg away from per-source pages; don't revert to the old pattern without Carlo asking for it again.

---

## 10. Session Handoff

### Last completed task
Three pieces of work this session, in order:
1. Fixed Brazil 10Y for real: swapped the Selic-as-fallback (which Carlo rejected — "not actually the future curve") for ANBIMA's real daily nominal yield curve, verified against independent reporting.
2. Redesigned `brazil_scraper.py`: strict same-day-only filtering (was 7-day) plus a technical, analyst-level keyword list per sector (was generic terms) — required switching from one OR-mega-query per sector to one request per keyword after discovering Google News RSS ranks by relevance and caps each query at ~100 results, which was silently returning zero today-dated articles for broad queries.
3. **Major reorg**: 4 source-pages (Cockpit/Brazil/China/Credit, each with its own News/Events/Charts tabs) → 5 content-type pages (Cockpit/News/Economic Data/Calendar/Other Datasets), each internally filterable by country/sector/company instead of hard-partitioned by source. Planned via `EnterPlanMode` first given the scope; see §4 for the resolved design decisions.

### Files modified
- `core/generate_dashboard.py` — `fetch_br10y()` + ANBIMA ETTJ helpers (replacing `_ck_br_selic()`); two new functions `load_unified_news()` and `load_unified_events()`; `render()`/`main()` signatures updated for the unified data model.
- `scrapers/brazil_scraper.py` — same-day Brasília filtering, per-keyword querying, technical keyword lists, score-based backfill to a 30/sector floor.
- `templates/dashboard_template.html` — the bulk of the reorg: new page-switcher, new News/Economic Data/Calendar/Other Datasets page markup, `buildEconCharts()` replacing 3 separate chart-builders, unified news-filtering JS replacing 4 separate ones, `pfTab`/`brTab`/`cnTab`/`crTab` removed entirely.
- `CLAUDE.md`, `IDEAS.md` — this update.
- One-off: force-added and pushed a single `dashboard.html` snapshot at Carlo's direct request (commit `dc1cff1`) — **not** a standing policy, `.gitignore` still excludes it, future regenerations won't auto-commit unless asked again.

### Current state
Reorg is implemented and structurally verified (page containers correct, old ones gone, JS syntax-checks clean, unified news count 700 = sum of all 4 sources, events count 136, COPOM confirmed appearing exactly once per date/title — not duplicated). **Not yet visually reviewed in a real browser** — this environment can't render one. Everything from prior sessions (chart UI standardization, Brasília timestamps, Itaú logo, importance badges, static-file workflow) still holds; the reorg reuses those components (shared modal, badge CSS, chart action buttons) rather than replacing them.

### Next recommended task
Get Carlo to actually open the new `dashboard.html` in a browser and click through all 5 pages/filters before it goes to his team — this changed the navigation model of a document people open directly, and structural verification from this environment is not a substitute for seeing the real layout. After that: back to `IDEAS.md` — Tier 1 item #2 was rejected, waiting on Carlo's pick among #3/#4/#5 or a Tier 2 item.

### Notes for future Claude
- This file (§1–9) is the durable context. Read it fully before touching code. `IDEAS.md` is the backlog — check it before proposing new features so you don't re-litigate a prioritization decision Carlo already made.
- The project has gone through several rounds of "user reports X is broken/inconsistent → root-cause it, don't just patch the symptom" — e.g. the news-age bug existed in 5 places, not 1; the BCB 12511 issue was confirmed via direct curl testing to be BCB's fault; the Brazil scraper's "not enough articles" issue was confirmed via direct testing to be Google News' relevance-ranking behavior, not a scoring bug. Keep that standard: verify root cause with actual tool calls (curl, run the script, inspect real output) before claiming a fix.
- Carlo is non-error-tolerant about financial data accuracy — when a data source is broken or wrong, prefer "clearly labeled fallback" or "honest N/A" over "quietly wrong number," and don't stop at the first plausible-looking fix if he says it's still not right (Brazil 10Y took three iterations: broken BCB series → Selic, which he rejected as the wrong metric entirely → the real ANBIMA yield curve).
- The user has been iterating rapidly on UI/UX feedback in short messages, and separately has asked for big-picture planning before coding when the change is large (used `EnterPlanMode` for the 5-page reorg, asked clarifying questions on the ambiguous parts, got explicit sign-off on the plan before writing any code). Match the weight of the response to the weight of the ask — quick fixes get quick fixes, "this is a big reorganization" gets a real plan.
- When Carlo asks for discussion/planning before coding, respect that explicitly — produce the artifact requested, give a direct opinion when asked, but don't start implementing until he confirms scope.

# Feature Ideas & Future Enhancements

Backlog for the Portfolio Intelligence Dashboard. Nothing here is scheduled — these are candidates, ordered by a rough cost/value read, to be picked off individually as Carlo decides. See `CLAUDE.md` for the architecture these ideas must fit inside.

**Stated goal for this backlog:** depth and quality on existing features over new surface area. Several items below are flagged as working against that goal even though they were on the original list — noted explicitly, not silently dropped.

## Constraints (unchanged from the original list)

- Preserve the static-HTML-generation architecture — one self-contained file, no server.
- No browser-side API requests. No `fetch()` in the generated page, ever.
- Must keep working on the corporate network (`verify=False`, no new external dependencies that require auth/API keys reachable only off-network).
- Prefer Python-based generation over client-side logic.
- Avoid unnecessary dependencies.
- **Corollary for this round specifically:** anything that reads as "AI-generated narrative" (Executive Brief, Investment Narrative, Analyst interpretation) must be built as rules-based/templated Python text generation over data already in hand — not a call to an external LLM API. That would add an API key, a new dependency, and a new failure mode this project has spent a lot of effort removing from the news/data pipeline. Worth confirming this reading with Carlo before starting Tier 2.

---

## Tier 1 — Nearly free, finishes work that's already half-built

These either reuse data the pipeline already computes and throws away, or are a few hours of Python with zero new infrastructure.

### 1. Surface the News Importance Score *(from "News Importance Score")*
**Not a new idea — this already exists and is being deleted.** All four scrapers compute a `relevance_score()` per article (source trust, keyword hits, sector match) to decide what clears the `MIN_SCORE` bar, then explicitly discard it:
```python
del a["_score"]   # scraper.py:229, brazil_scraper.py:236, china_scraper.py:209, credit_scraper.py:255
```
Keep it instead, rescale it to something presentable (e.g. 1–5 stars or Low/Med/High), and surface it as a small badge on each news card. Zero new fetching, zero new dependencies — this is the single cheapest item in the whole list.

### 2. Data Freshness / Staleness Indicators *(my addition — not on the original list)*
The reliability work from a few sessions ago made every source degrade gracefully on failure — if `credit_scraper.py` times out, the dashboard silently keeps showing yesterday's cached credit news with no visible signal that anything's stale. That's the right failure mode for the pipeline, but it's invisible to the reader. Cheap fix: each scraper/generator already writes a `last_updated` timestamp into its cache file (currently written but never read anywhere — confirmed, `CLAUDE.md` §7). Surface it: a small "as of HH:MM" per section, and if a source's `last_updated` is more than one refresh cycle old, flag it visibly (e.g. a muted amber dot). This directly closes a gap the retry/resilience work introduced and is exactly "depth on existing features," not new surface area.

### 3. "What's New Since Previous Update" *(from the original list)*
The merge logic in every scraper already diffs `new_articles` against `existing_cache` by link. Before overwriting the cache, it's a few lines to count what's actually new this run vs. carried over, and pass that count into the template as "14 new articles, 2 new events since this morning's update." Cheap, and it makes the twice-daily cadence feel alive rather than just "the file changed."

### 4. Analyst Notes *(from the original list)*
A single `data/notes.md` (or short JSON) that Carlo edits by hand, rendered verbatim at the top of the Cockpit page if non-empty. No generation logic at all — just a read-and-render. This is the cheapest way to get real human judgment into the dashboard, and it pairs naturally with #6 below (the templated brief can say "see Analyst Notes for context" rather than trying to synthesize commentary itself).

### 5. Macro Calendar Importance *(from the original list)*
Events already carry a `type` field (`economic`, `data`, `earnings`, `conference`) and in some generators a hardcoded identity (COPOM decision vs. COPOM first day, Focus Report, etc.). A small static lookup table (`{"COPOM – Decisão da Selic": "Critical", "BCB Focus Report": "Medium", ...}`) gets you the Critical/High/Medium/Low scale for free — no new data, just a mapping.

---

## Tier 2 — Real synthesis work, but pure Python over data already in hand

Higher effort than Tier 1, but still zero new external data sources — these are presentation/synthesis layers on top of what's already being fetched.

### 6. Daily Brief *(merges "Executive Morning Brief" + "Today's Story" — same feature at two granularities, no reason to build both)*
A templated (not LLM-generated — see Constraints) summary assembled from data already computed: top-scored article (once #1 exists), biggest single-day mover across the Cockpit market cards, and the day's Critical/High events (once #5 exists). This is the highest-value Tier 2 item precisely because it has no new dependencies once #1 and #5 exist — it's a template that reads already-computed fields. Sequence it after #1 and #5, not before.

### 7. Source Classification *(from the original list)*
Every scraper already maintains a `TRUSTED_SOURCES` set. Tag each with a category (Wire / Bank Research / Government / Financial Press) in a static dict and show it as a small label next to the source name. An afternoon of work, purely additive to data that exists.

### 8. China Pulse *(merges "China Pulse" + "China Risk Meter" — a risk meter is just a pulse indicator with a traffic-light skin on the same inputs)*
A compact scorecard (PMI, property, exports, credit, industrial production) built entirely from series already being fetched from chinadata.live for the China Economic Data tab. This is a second view of existing data, not a new source — genuinely "depth," matches the stated goal well.

### 9. Relationship Mapping *(merges "Relationship Mapping" + "China → Brazil Impact" — same idea, one is the general mechanism and the other is naming the specific Brazil use case)*
A small hardcoded set of chains (`"China PMI" → "Steel Demand" → "Iron Ore" → "Vale"`) shown as an annotation wherever the relevant data already appears — same pattern as the existing hardcoded COPOM schedule or conference calendar, just editorial content instead of dates. Genuinely useful connective tissue between the Brazil, China, and Cockpit pages that currently don't reference each other at all.

### 10. Improved Filtering *(from the original list, narrowed)*
The news feed already has company/sector/source filters wired up client-side. Adding a date-range filter and, once #1 exists, an importance-threshold filter, is extending an existing UI pattern rather than building a new one. Cheap once #1 lands; skip until then.

---

## Tier 3 — Valuable, but needs Carlo's domain input, not just code

The code to support these is straightforward; the blocker is that someone has to decide the actual values (China exposure %, FX sensitivity, etc.) for ~10 portfolio companies. Worth scoping the display/schema now, but the real work is a data-entry pass, not engineering.

### 11. Portfolio Exposure Metadata *(merges "Portfolio Exposure Metadata" + "Additional Portfolio Metadata" — same idea stated twice)*
Extend `portfolio.xlsx` with columns like China Exposure, Commodity Exposure, FX Sensitivity. Once populated, these are just new tag/filter fields — cheap in code, but Carlo (or someone on the team) has to actually fill them in per company.

### 12. Portfolio Risk Overview *(from the original list)*
Directly derived from #11 — "highest China exposure," "highest FX sensitivity" are one-line aggregations once the exposure fields exist. Sequence after #11, not before.

### 13. Company Intelligence View *(from the original list)*
A per-company page aggregating its news, earnings date, and (once relevant) its exposure tags and any Relationship Mapping chains it appears in (#9). Natural capstone once #9 and #11 exist; not worth building standalone first since most of what it'd show doesn't exist yet.

---

## Tier 4 — Real value, but expensive — good later-stage work once the above is solid

### 14. Historical Context *(merges "Historical Event Library" + "Historical Market Reactions" — a reaction library implies the event library exists first)*
Genuinely valuable for decision support ("last 3 times BCB cut 50bp, here's what USD/BRL did in the following week") but this is a real curation project — researching and hand-verifying historical events and their market reactions, then maintaining it going forward. Don't underestimate this as "just a data file." Worth doing once the daily-use features are solid, not before.

### 15. Economic Heatmaps *(from the original list)*
Visually appealing but this is new UI surface area (a new component type, color-scaling logic) more than it's depth on something existing. Given the explicit "not more volume" goal, I'd rank this below everything in Tiers 1–3, which all reinforce existing sections instead of adding new ones.

---

## Tier 5 — Flagged for discussion: conflicts with stated goals, or no clean path within constraints

Not dropped — surfaced, because building these as originally described would work against what was asked for.

### Additional News Sources — conflicts with "not more volume"
PBOC, NBS, MOFCOM, Xinhua, IMF, OECD: most don't have a clean RSS/JSON feed (would mean scraping HTML, a maintenance burden every time a source's markup changes), and this is explicitly *more sources* when the ask was *more depth on current sources*. If pursued at all, I'd scope it to whichever one or two of these actually have a stable feed — not all six.

### China Macro Surprise Tracker — no free data source identified
Needs consensus/expected values (Previous / Expected / Actual / Surprise), and none of the currently-used sources (chinadata.live, BCB, IBGE) publish consensus estimates — that's normally a Bloomberg/Trading Economics paid feed. Don't see a path within "avoid unnecessary dependencies" unless a free consensus source turns up.

### China Watch List / Daily Watch List / Market Watch Panel — redundant with the existing Cockpit Market Data tab
These three (from three different sections of the original list) all describe roughly "a short list of assets to watch today" — which the Cockpit's Market Data tab already *is* (Equities, Rates, FX, Commodities, all with current values and change indicators). Building a fourth version of this as a new panel is volume, not depth. If the actual want is "make it obvious what moved most," that's a one-line addition to the *existing* tab (sort/highlight by |% change|), not a new section.

### Investment Narrative — merge into Daily Brief (#6), don't build separately
As described this is Daily Brief with more ambition (macro → commodity → sector → company → portfolio, as connected prose). Without an LLM that's a templating exercise, and Relationship Mapping (#9) is what actually supplies the "connected" part. Building this as a separate feature from #6 and #9 would mean three overlapping systems doing variations of the same synthesis.

### Dashboard Personalization — architecturally awkward for a single static file
True personalization implies either multiple generated files per audience or a login/config system — both break the "one file, email it" distribution model this whole project is built around. A cheap, honest version is possible (client-side collapsible sections via `localStorage`, still a single static file, no server) but that's a UI convenience, not personalization. Worth being explicit about which one is actually wanted before building either.

### Improved Search — too vague as stated
Every page already has a working search box scoped to that page's news feed. "Expand search across News, Companies, Indicators, Events" would mean a cross-page global search, which is a real feature but needs a concrete use case to scope (what does someone actually type, and what should come back?) before it's buildable. Parking until there's a specific gap to point at.

---

## Suggested sequencing

1. **Tier 1, in order.** All five are cheap, additive, and several unlock Tier 2 items (Daily Brief needs #1 and #5; Improved Filtering needs #1).
2. **Tier 2, in order** — 6 through 9 are genuinely good and cheap once Tier 1 lands; 10 is a small follow-on to 1.
3. **Pause for a data-entry pass** before Tier 3 — #11 needs Carlo (or the team) to actually fill in exposure data; no point scaffolding #12/#13 first.
4. **Tier 4 and the Tier 5 items** — revisit once the above is live and there's real usage feedback on what's actually missing, rather than building ahead of confirmed need.

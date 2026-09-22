# Metals dashboard — persistent checklist

Keep this current. Sections: Decisions · Done · Open issues · Next steps.

## Decisions (with why)
- 2026-09-21 **Stack: Python 3.12 + FastAPI + SQLite + APScheduler; vanilla JS + Chart.js (CDN).** Python and most
  deps already on the machine, no node build, one process = API + worker (single writer, jobs serialized).
  `run.py --web` / `--worker` split them when hosting. Hosting path: same process in a container behind a reverse proxy.
- 2026-09-21 **OpenBB: study only.** AGPL, 81 packages; its CFTC provider wraps the same free Socrata API we call
  directly; no COMEX inventory provider; FRED still needs a key through it.
- 2026-09-21 **pycot: study only.** Unmaintained since 2024, matches contracts by *name* string, downloads annual
  zips. We query Socrata by contract code (gold 088691, silver 084691; verified live, history to 1986).
- 2026-09-21 **fredapi: optional** (only when `FRED_API_KEY` is set; gives ALFRED vintages). Default is the keyless
  fredgraph.csv via `curl` (fail-fast after 2 timeouts). It is intermittent from this machine: 0/5 batches one run,
  all batches the next. Treasury par curves + derived breakevens + NY Fed EFFR are the always-on fallback.
- 2026-09-21 **trafilatura adopted but not yet called** (installed, pinned 2.2.0). News stores feed metadata + short
  excerpt only; no article bodies fetched (terms/copyright). Use trafilatura only for explicitly reviewed domains.
- 2026-09-21 **StakTrakr: adopt feed + UI ideas, not code.** Spot every 20 min, dealer retail every 30 min (index +
  ≤14 detail requests). Labelled "aggregated spot"; futures stay primary.
- 2026-09-21 **Research repos: study only; reproduced as hypotheses H1–H3** (see docs/methods.md).
- 2026-09-21 **Instrument labelling / no splicing.** Continuous futures record the contract each day and flag rolls;
  contract months stored individually; ETF, spot-aggregate, tokenized gold are separate series.
- 2026-09-21 **Briefing is deterministic.** Weekly/lagged series (COT, ETF ounces, curve, Fed balance sheet, broad
  dollar, policy rate) are *context*, never a same-day explanation. LLM narrative = paid decision for Tom.
- 2026-09-21 **COMEX warehouse stocks via manual drop** (`data/inbox/`); cmegroup.com returns "IP blocked" to all
  non-browser clients here. Parser validated with a synthetic workbook; real-file layout still to be confirmed.
- 2026-09-21 **Codex (GPT-5.6-Sol) plan review adopted in part**: immutable raw snapshots + checksums, value revision
  history, schema_version + migrations list, briefings keyed by date + generation time + inputs hash, single-writer
  worker separable from the web process, roll-day flags, "implied net metal change" wording, realized-vol term
  structure, per-card provenance. Not adopted: PostgreSQL/Alembic (overkill for one user), dropping yfinance
  (no free alternative for intraday futures; treated as a fallible adapter with status surfacing).

## Done
- 2026-09-21 Environment inspected; free sources probed live (docs/sources.md); repo assessment (docs/repo-assessment.md).
- 2026-09-21 Stage 1 prices: GC/SI continuous from 2000, 5-min bars, 8 contract months each, front OI, related markets.
- 2026-09-21 Stage 2 macro: Treasury curves 2003→, derived breakevens, NY Fed EFFR/SOFR, FRED (when reachable), BLS API.
- 2026-09-21 Stage 3 positioning/flows: COT 4 families ×2 metals (1986→), SLV/IAU ounces + shares, curve + carry state.
- 2026-09-21 Stage 4 news: 19 feeds, 669 items first pull, dedupe/cluster/score, filters (metal/topic/source/kind/date).
- 2026-09-21 Stage 5 briefing (archived, versioned, diffed), driver board, events (FOMC page + seed to Dec 2026),
  COMEX parser + inbox, dealer premiums (26 products).
- 2026-09-21 Stage 6 fundamentals seeded from WGC (FY2025, Q1–Q2 2026) and Silver Institute (2025, 2026F) with URLs;
  research page H1–H3; 7 unit tests green; all 20 API endpoints smoke-tested on the running server.

- 2026-09-21 "At a glance" plain-English summary box on every tab (`app/analytics/snapshots.py`, `/api/snapshot/{tab}`),
  rule-based from the tab's own data. Static asset links carry `?v=` stamps: bump them in `index.html` when app.js/styles.css change.

- 2026-09-21 One-file HTML snapshot export (`app/export.py`, header button, `/api/export`, `/exports/` browsable) for
  emailing the dashboard. Project pushed to private GitHub repo `tommygiek-dot/metals-dashboard`.

## Open issues
- **GLD ounces:** old `GLD_US_archive_EN.csv` now serves a PDF; issuer page is JS-rendered. Find the real data URL in
  a browser network tab, then add to `etf_holdings.FUNDS`. Gold ETF read = IAU only until then.
- **FRED intermittent** without a key. Ask Tom for a free key (1 minute at fred.stlouisfed.org) → `.env`.
- **CME calendar dates** (GC/SI option expiry, first notice) need a browser session; not in the events list.
- **COMEX parser on a real file** untested (no file yet). First drop may need layout tweaks.
- **BLS 2027 / BEA 2027 release dates** not published yet; seed ends Dec 2026 (ISM dates are generated, unconfirmed).
- **No consensus figures** (no free licensed source); events say "consensus unavailable".
- **No options IV/skew** (no free source); realized vol only.
- **Research H3** shows 60.9% out-of-sample hit rate at |I|≥10 (235 overlapping signals, no costs). Overlap inflates
  the count; treat as a hypothesis to test with non-overlapping windows before believing it.
- Google News feed items can carry an off-target publisher name when the "- Publisher" suffix isn't standard.

## Next steps
1. Tom: FRED key (optional), drop COMEX xls files, glance at each tab and say what's confusing.
2. Recover GLD holdings URL; add GLD to the ETF card.
3. Non-overlapping-window version of H3; add gold rolling-vol term structure chart.
4. Task Scheduler entry so `python run.py --worker` starts at login (web can stay on demand).
5. Optional Claude-API narrative layer over the deterministic briefing (paid; ask Tom).

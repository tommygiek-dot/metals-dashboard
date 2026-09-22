# Metals dashboard — architecture and implementation plan (2026-09-21)

## Goal
Personal gold & silver intelligence dashboard: today's move, what changed, candidate explanations with
evidence, disagreeing signals, positioning/flows/physical, upcoming events. Research only, no trading.

## Architecture (single Python process, local)
```
metals/
  app/
    main.py            FastAPI app: JSON API + serves static SPA; starts APScheduler
    db.py              SQLite (WAL) schema + helpers.  Tables:
                         series (id, name, instrument_type, unit, source, cadence, notes)
                         observations (series_id, ts_utc, value, as_of/ingested_at, meta json)
                         articles (url_hash, url, title, publisher, author, published_at, event_at,
                                   ingested_at, summary, excerpt, kind[official|reporting|opinion|forecast],
                                   metals[], topics[], geo[], cluster_id, score, content_stored bool)
                         clusters (id, canonical_article, member_count, first_seen)
                         cot (report_type, contract_code, report_date, fields...)
                         events (id, title, category, scheduled_at, source_url, importance)
                         briefings (id, date, json body)  -- archived daily
                         driver_state (driver_id, date, json)
                         fundamentals (metric, period, value, source, published_at)  -- annual/quarterly
    collectors/        one module per source, each: fetch() -> normalized rows; idempotent upserts
        prices_yf.py   GC=F/SI=F continuous + contract months (curve) + OI via info; DXY, ^TNX, HG, CL, SPX, VIX,
                       GLD, SLV, IAU, TIP. 5m intraday for metals during session; daily for all.
        spot_stak.py   StakTrakr spot + dealer retail (premiums module)
        fred.py        DFII10, DGS10, DGS2, T10YIE, T5YIE, DTWEXBGS, DTWEXAFEGS, DFF, VIXCLS, DCOILWTICO, ...
                       via curl w/ retries; optional fredapi if FRED_API_KEY set
        treasury.py    daily nominal + real yield curve CSVs (fallback + curve)
        cftc.py        Socrata: legacy fut-only (6dca-aqww), legacy combined (jun7-fc8e), disaggregated fut-only
                       (72hh-3qpy), disagg combined (kh3c-gbw2); codes 088691 / 084691
        etf_holdings.py IAU + SLV ounces-in-trust (page scrape, 1/day); GLD when URL found
        comex_stocks.py parse Gold_Stocks.xls / Silver_stocks.xls from data/inbox (manual drop)
        news_rss.py    feedparser over curated feed list; trafilatura for metadata/excerpt where permitted
        bls.py         CPI / unemployment via BLS API v1 (25/day)
        calendar.py    FOMC page parse + static seed json for CPI/PCE/NFP/GDP dates
    analytics/
        changes.py     period changes (1d/1w/1m/YTD/1y), ratio, realized vol, session detection
        drivers.py     driver board rules: each driver -> stance (support/pressure/mixed/insufficient),
                       mechanism text, evidence strength, freshness, catalyst-vs-condition
        flows.py       ETF flow = Δounces (not ΔAUM); COT net positioning + percentile context
        curve.py       futures curve, calendar spreads, roll notes
        research.py    reproducible hypotheses (rolling corr gold~DFII10, gold~DXY beta, silver fair-value
                       indicator) with in-sample vs out-of-sample windows
        briefing.py    deterministic daily briefing: facts / interpretations / evidence / counterevidence /
                       unknowns / upcoming; diff vs previous briefing; archived
        news_rank.py   dedupe (url canon + title simhash), cluster, score
    scheduler.py       APScheduler jobs w/ per-source cadence (respecting limits)
    static/            index.html + app.js + styles.css (dark mode, responsive), Chart.js from CDN (vendor later)
  data/                metals.sqlite, inbox/ (manual CME xls), seeds/
  docs/                plan, sources, repo assessment, methods (rollover, instrument definitions)
  tests/               collectors parse fixtures; analytics unit tests
  run.py               `python run.py` -> uvicorn on 127.0.0.1:8765
```
Config: `config.toml` (display tz, feeds, cadences) + `.env` for optional keys (FRED_API_KEY, ANTHROPIC_API_KEY).

## Stages (each leaves something usable)
1. Core store + price collectors + overview page (prices, changes, ratio, related markets, intraday/daily/multiyear
   charts, instrument labels, session status). Backfill daily history.
2. Macro: FRED/Treasury series; driver board v1 (dollar, nominal, real, breakevens, Fed policy rate, oil, copper,
   equities, VIX).
3. Positioning & flows: CFTC COT (both report families), ETF holdings & flows, futures curve/OI.
4. News: RSS collection, dedupe/cluster, ranking, filters; official-release feeds (Fed, CFTC, BEA, BLS API).
5. Briefing engine + archive + diff; events calendar; physical: COMEX inventory parser, dealer premiums.
6. Fundamentals (annual/quarterly, clearly separated): WGC/Silver Institute headline stats seeded from their
   publications; research hypotheses page; tests; hosting notes (Docker).

## Source limits honoured
yfinance: ~1 req/s polite, intraday 5m every 5 min in session only; StakTrakr: 20/30 min per stale_after;
FRED: daily; CFTC: weekly (Fri 15:30 ET + retry); iShares pages: daily; RSS: 30 min; BLS v1: <=25/day.

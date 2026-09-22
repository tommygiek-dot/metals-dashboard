# Metals — personal gold & silver intelligence dashboard (created Sep 21, 2026)

Research and monitoring only; no automated trading. USD per troy ounce. Local-first, one Python process.

## What it is
A daily-use dashboard answering: what gold and silver are doing today, what changed since yesterday,
which developments may explain it (with evidence), which signals disagree, what positioning / ETF holdings /
physical markets show, and what events are coming. Ten tabs: Overview · What matters today (archived
briefings + diff) · Drivers · Positioning & flows · Physical · News · Events · Fundamentals · Research · Status.

## Layout
- `run.py` — start everything (`python run.py`; `--web`, `--worker`, `--job NAME` also work). Serves http://127.0.0.1:8765.
- `app/main.py` FastAPI JSON API + static SPA (`app/static/`). `app/scheduler.py` job registry + APScheduler worker
  (single writer). `app/db.py` SQLite schema (migrations list, revision history, raw-snapshot index).
- `app/collectors/` one module per source: `prices_yf` (futures, contract months, related markets, 5-min bars),
  `fred` (curl, fail-fast; optional key), `treasury` (par curves + derived breakevens), `nyfed` (EFFR/SOFR),
  `cftc` (COT, 4 report families, by contract code), `etf_holdings` (SLV/IAU ounces), `spot_stak` (spot + dealer
  retail), `news_rss` (feeds → metadata only), `bls`, `calendar` (FOMC page + `data/seeds/events_seed.json`),
  `comex_stocks` (manual drop: `data/inbox/`).
- `app/analytics/`: `changes`, `drivers` (driver board rules), `briefing` (deterministic, archived, diffed),
  `flows` (COT/ETF), `curve`, `physical`, `news_rank` (dedupe/cluster/score), `research` (H1–H3), `fundamentals`, `calendar_view`.
- `data/` — `metals.sqlite` (git-ignored), `raw/` archived payloads, `seeds/` (events, fundamentals with source URLs), `inbox/`.
- `docs/` — `plan.md`, `sources.md` (what was verified, limits), `repo-assessment.md`, `methods.md` (definitions).
- `CHECKLIST.md` — persistent decisions / done / open issues / next steps. Keep it current.
- `tests/` — `python -m pytest -q`.

## How to run
`python run.py` from this folder (deps: `pip install -r requirements.txt`). First start backfills prices from 2000,
Treasury curves, COT history and news; the UI is usable within ~2 minutes. Optional `.env` keys: `FRED_API_KEY`
(free; makes FRED reliable — the keyless endpoint times out from this machine), `CFTC_APP_TOKEN`, `ANTHROPIC_API_KEY`
(not wired; briefing is deterministic). COMEX warehouse stocks: download Gold_Stocks.xls / Silver_stocks.xls from
cmegroup.com in a browser and drop them in `data/inbox/`.

## State (2026-09-21)
Stages 1–6 built and verified against live sources (see CHECKLIST). Open: GLD holdings URL, FRED reliability
without a key, CME calendar dates (option expiry / first notice) need a browser session.

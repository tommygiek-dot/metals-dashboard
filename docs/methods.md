# Methods and definitions

## Instruments (never merged into one series)
| Label in UI | What it is | Series |
|---|---|---|
| COMEX front-month futures | Yahoo's continuous `GC=F` / `SI=F`: the most active COMEX contract, delayed. Not spot. | `gold_fut_cont`, `silver_fut_cont` (+ `_5m`) |
| Contract month | One listed COMEX contract (e.g. GCZ26), daily close and volume | `fut_GCZ26` … |
| ETF proxy | Share price of GLD / SLV / IAU; equity hours, embeds fees | `gld`, `slv`, `iau` |
| Aggregated spot (StakTrakr) | Third-party aggregate of spot quotes, ~20-min cadence, no methodology published | `stak_spot_*` |
| Tokenized gold | PAXG/USD from crypto venues, 24/7 | `paxg` |
| Benchmark (LBMA) | Not collected (licensed). | — |

## Rollover
Yahoo switches the continuous symbol to the next contract on its own (undocumented) schedule. Each daily row
stores the contract in force (`meta.contract`); when it changes the row is flagged `meta.roll = true` with
`roll_from`. History is **not** back-adjusted: a gap on a roll day is a contract change, not a market move.
The curve page shows the individual months so the roll gap can be read directly.

## "Today", "since yesterday"
For every series: latest stored daily value vs the previous stored daily value **of the same series**.
During a session Yahoo's current daily bar updates intraday, so "1 day" is a live change; after the close it is
close-to-close. 1w/1m/3m/1y use the last value on or before the calendar reference date; YTD uses the last
value on or before Dec 31 of the prior year. Cross-market comparisons are only made on daily data.

## Sessions
COMEX Globex metals: Sunday 6:00 pm – Friday 5:00 pm ET with a 5–6 pm ET daily break; exchange holidays are not
modelled (data simply stops updating). US equities: 9:30–16:00 ET weekdays. Display time zone is set in
`config.toml`; all storage is UTC (daily series keyed by date).

## Positioning (CFTC COT)
Four report families stored separately, never spliced: legacy futures-only, legacy combined, disaggregated
futures-only, disaggregated combined. Contracts selected by CFTC code (gold 088691, silver 084691).
`report_date` is the Tuesday the positions are as of; release is Friday ~3:30 pm ET; `retrieved_at` is when we
fetched it. Percentiles are computed within one family only. "Managed money" ≈ hedge funds / CTAs.

## ETF holdings
Ounces in trust scraped from the issuer page (SLV, IAU). Δounces is reported as **implied net metal change**,
never as investor flow or as ΔAUM (which moves with price). Shares outstanding are stored when the page has them.

## Driver board
Rule-based. Each driver gets a stance (support / pressure / mixed / insufficient) for gold and silver from the
sign and size of its latest change; the real-yield and dollar rules are downgraded to "mixed" when the 60-session
rolling relationship (Research tab) is weak, because the historical sign is not assumed to hold. Class:
catalyst (moved enough today), condition (slow-moving), background (structural). Stale data is demoted.

## Briefing
Deterministic: facts → developments (top news clusters + catalysts) → per-metal interpretation using
calibrated verbs ("consistent with" = strong active relationship and a same-day move; "may partly reflect" =
weaker; "no supported explanation" otherwise) → evidence links → conflicts → unknowns → upcoming → diff vs the
previous briefing. Keyed by display-date + generation time with an inputs hash, so re-runs with new inputs
are new versions and old ones remain readable.

## News
Feed metadata + link + a short feed-provided excerpt only. Google News items are headline + publisher only.
Dedupe on canonical URL; clustering on normalized-title hash or token Jaccard ≥ 0.6 within 48 h. A cluster's
"independent publishers" count is what the briefing treats as source breadth.

## Fundamentals
Annual/quarterly figures with period, publication date and source URL (WGC / Metals Focus, Silver Institute).
Displayed on their own tab and in the driver board only as *background*; never as a daily explanation.

## Research hypotheses
H1 rolling 60-session correlation of gold returns with Δ10y real yield (regime means 2008–21 / 2022–23 / 2024–);
H2 rolling beta to DXY returns; H3 silver fair-value indicator fitted in-sample 2018-04…2021-03 with betas frozen
and scored out-of-sample from 2022 by 15-session hit rate. All are explanatory fits, not forecasts.

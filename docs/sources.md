# Data sources — what was verified live on 2026-09-21, limits, and labels

| Data | Source | Access | Cadence used | Notes / label shown in UI |
|---|---|---|---|---|
| Gold/silver futures, continuous | yfinance `GC=F`, `SI=F` | free, no key | 5-min bars during session; daily EOD | "COMEX front-month (Yahoo continuous)". Yahoo rolls to the next active contract itself; we record which contract `GC=F` points at (`underlyingSymbol`, e.g. GCZ26) each day so rolls are visible. Not a spot price. |
| Contract months (curve) | yfinance `GCZ26.CMX`, `GCG27.CMX`… `SIZ26.CMX`… | free | daily | Settlement/last per month; volume; OI for front month via `info.openInterest`. |
| Related markets | yfinance `DX-Y.NYB` (ICE DXY), `^TNX`, `HG=F`, `CL=F`, `^GSPC`, `^VIX`, `TIP` | free | daily (+intraday for DXY) | DXY ≠ Fed trade-weighted index (that comes from FRED `DTWEXBGS`). |
| ETF proxies | yfinance `GLD`, `SLV`, `IAU`, `PAXG-USD` | free | daily | Labelled "ETF proxy" / "tokenized gold". |
| Third-party spot | `api.staktrakr.com/data/v2/spot/latest.json`; `/spot/xau/YYYY/MM/DD.json` | free, no key, no published terms | every 20 min (`stale_after`) | "Aggregated spot (StakTrakr)". |
| Dealer retail prices | `api.staktrakr.com/data/v2/retail/{slug}/latest.json` | free | every 30 min | Premium = (median retail − spot)/spot, per product size. |
| FRED macro | `fred.stlouisfed.org/graph/fredgraph.csv?id=…` | free, no key, undocumented | daily | Flaky with Python HTTP stacks; fetched via `curl` with retries. Optional `FRED_API_KEY` → `fredapi`. Series: DFII10 (10y real), DGS10, DGS2, T10YIE, T5YIE, DTWEXBGS (broad TWI), DTWEXAFEGS, DFF, VIXCLS, DCOILWTICO. |
| Treasury yield curves | home.treasury.gov daily-treasury-rates CSV (nominal + real) | free | daily | Fallback and full curve (5/7/10/20/30y real). |
| CFTC COT | `publicreporting.cftc.gov/resource/{6dca-aqww,jun7-fc8e,72hh-3qpy,kh3c-gbw2}.json` | free, optional app token | weekly (Fri ~15:30 ET) | Contract codes 088691 (gold), 084691 (silver). Futures-only and futures+options stored as separate report types. |
| ETF holdings (ounces) | iShares product pages (SLV 239855, IAU 239561) | free page scrape | daily | GLD: old CSV URL now serves a PDF — open issue. Flows = Δounces, not ΔAUM. |
| COMEX warehouse stocks | cmegroup.com `Gold_Stocks.xls`, `Silver_stocks.xls` | **IP-blocked for scripts** | manual | Drop the files in `data/inbox/`; parser reads registered/eligible by depository. Labelled with the report date in the file. |
| BLS CPI / unemployment | `api.bls.gov/publicAPI/v1` | free, 25 calls/day, no key | on release days | bls.gov HTML is 403 to scripts. |
| Official feeds | Fed press_all / press_monetary / speeches / h41; CFTC press RSS; BEA RSS; ECB; BoE | free RSS | 30 min | kind = official. |
| News feeds | CNBC gold, WSJ markets, FT commodities (headline-only), Mining.com, Metals Focus, WGC, Silver Institute, Google News searches (gold, silver, site:reuters.com, site:bloomberg.com) | free RSS | 30 min | Google News items are headline + publisher only; never fetch paywalled bodies. |
| Dead / blocked | Kitco RSS (404), metals.live, gold-api.com (down), IMF RSS (403), LBMA RSS (404), Treasury press RSS (404), US Mint (403) | — | — | Not used. |

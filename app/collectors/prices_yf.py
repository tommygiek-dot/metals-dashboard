"""Prices via yfinance (Yahoo Finance). A replaceable, fallible adapter — not an authoritative feed.

Series written
  gold_fut_cont / silver_fut_cont      daily OHLCV of Yahoo's continuous front-month (GC=F / SI=F).
                                       meta.contract records which contract Yahoo pointed at that day, so
                                       roll days are visible (meta.roll = true when it changes).
  gold_fut_5m / silver_fut_5m          5-minute bars of the same continuous symbol (intraday view).
  fut_<TICKER>                         individual contract months (GCZ26.CMX ...) daily close + volume.
  gold_front_oi / silver_front_oi      daily open interest of the front contract from Yahoo's quote summary.
  dxy, us10y, copper_fut, wti_fut, spx, vix, tip_etf, gld, slv, iau, paxg  daily closes (+ dxy 5m).
"""
from __future__ import annotations
import logging
import math
from datetime import datetime, timezone, timedelta

import pandas as pd
import yfinance as yf

from .. import db

log = logging.getLogger(__name__)
PARSER_VERSION = "1"

CONT = {
    "gold_fut_cont":   ("GC=F", "Gold futures, COMEX front-month (Yahoo continuous)", "future_continuous", "USD/oz"),
    "silver_fut_cont": ("SI=F", "Silver futures, COMEX front-month (Yahoo continuous)", "future_continuous", "USD/oz"),
}
RELATED = {
    "dxy":        ("DX-Y.NYB", "US Dollar Index (ICE DXY)", "index", "index"),
    "us10y":      ("^TNX", "US 10-year Treasury yield (CBOE index)", "index", "pct"),
    "copper_fut": ("HG=F", "Copper futures, COMEX front-month (Yahoo continuous)", "future_continuous", "USD/lb"),
    "wti_fut":    ("CL=F", "WTI crude futures, NYMEX front-month (Yahoo continuous)", "future_continuous", "USD/bbl"),
    "spx":        ("^GSPC", "S&P 500 index", "index", "index"),
    "vix":        ("^VIX", "CBOE VIX", "index", "index"),
    "tip_etf":    ("TIP", "iShares TIPS Bond ETF (price)", "etf", "USD"),
    "gld":        ("GLD", "SPDR Gold Shares (ETF proxy, price)", "etf", "USD"),
    "slv":        ("SLV", "iShares Silver Trust (ETF proxy, price)", "etf", "USD"),
    "iau":        ("IAU", "iShares Gold Trust (ETF proxy, price)", "etf", "USD"),
    "paxg":       ("PAXG-USD", "PAX Gold (tokenized gold, USD)", "spot_aggregated", "USD/oz"),
}
INTRADAY = {"gold_fut_5m": "GC=F", "silver_fut_5m": "SI=F", "dxy_5m": "DX-Y.NYB"}

GOLD_MONTHS = "GJMQVZ"     # Feb Apr Jun Aug Oct Dec (active COMEX gold months)
SILVER_MONTHS = "HKNUZ"    # Mar May Jul Sep Dec
MONTH_CODES = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6, "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}


def _ensure_all():
    for sid, (tk, name, typ, unit) in {**CONT, **RELATED}.items():
        db.ensure_series(sid, name, typ, unit, f"yfinance:{tk}", "daily",
                         "Yahoo Finance; delayed/indicative; continuous futures use Yahoo's own roll.")
    for sid, tk in INTRADAY.items():
        db.ensure_series(sid, f"{tk} 5-minute bars", "future_continuous" if "=F" in tk else "index",
                         "USD/oz" if "G" in sid or "silver" in sid else "index", f"yfinance:{tk}", "intraday",
                         "Yahoo Finance 5-minute bars, delayed; kept ~60 days.")
    db.ensure_series("gold_front_oi", "Gold front-month open interest", "future_contract", "contracts", "yfinance:GC=F info", "daily",
                     "Open interest from Yahoo quote summary for the contract GC=F currently points at. Indicative only.")
    db.ensure_series("silver_front_oi", "Silver front-month open interest", "future_contract", "contracts", "yfinance:SI=F info", "daily",
                     "Open interest from Yahoo quote summary for the contract SI=F currently points at. Indicative only.")


def _rows_from_history(h: pd.DataFrame, daily: bool) -> list[tuple]:
    rows = []
    if h is None or h.empty:
        return rows
    for idx, r in h.iterrows():
        close = r.get("Close")
        if close is None or (isinstance(close, float) and math.isnan(close)):
            continue
        ts = idx.strftime("%Y-%m-%d") if daily else idx.tz_convert("UTC").isoformat(timespec="seconds")
        meta = {k.lower(): (None if pd.isna(r.get(k)) else float(r.get(k))) for k in ("Open", "High", "Low", "Volume") if k in r}
        rows.append((ts, float(close), meta))
    return rows


def _fetch(tk: str, period: str, interval: str) -> pd.DataFrame:
    h = yf.Ticker(tk).history(period=period, interval=interval, auto_adjust=False, actions=False)
    if h is not None and not h.empty and h.index.tz is None:
        h.index = h.index.tz_localize("UTC")
    return h


def backfill_daily(period: str = "max") -> int:
    _ensure_all()
    n = 0
    for sid, (tk, *_rest) in {**CONT, **RELATED}.items():
        try:
            h = _fetch(tk, period, "1d")
            rows = _rows_from_history(h, daily=True)
            n += db.upsert_observations(sid, rows)
            log.info("backfill %s (%s): %d rows", sid, tk, len(rows))
        except Exception as e:  # noqa: BLE001
            log.warning("backfill %s failed: %s", sid, e)
    return n


def update_daily() -> int:
    """Last ~10 days of daily bars for every series, plus the front-month contract/OI snapshot."""
    _ensure_all()
    n = 0
    for sid, (tk, name, typ, unit) in {**CONT, **RELATED}.items():
        try:
            h = _fetch(tk, "1mo", "1d")
            rows = _rows_from_history(h, daily=True)
            if typ == "future_continuous":   # gold, silver, copper, WTI: record the contract and flag rolls
                rows = _tag_contract(sid, tk, rows)
            n += db.upsert_observations(sid, rows)
        except Exception as e:  # noqa: BLE001
            log.warning("update_daily %s failed: %s", sid, e)
    n += update_front_info()
    return n


def _tag_contract(sid: str, tk: str, rows: list[tuple]) -> list[tuple]:
    """Attach the contract Yahoo currently maps the continuous symbol to; mark a roll when it changes."""
    try:
        info = yf.Ticker(tk).info or {}
        contract = info.get("underlyingSymbol")
    except Exception:  # noqa: BLE001
        contract = None
    if not contract or not rows:
        return rows
    prev = db.latest(sid)
    prev_contract = (prev or {}).get("meta", {}) or {}
    prev_contract = prev_contract.get("contract")
    out = []
    for ts, v, m in rows:
        m = dict(m or {})
        existing = db.q1("SELECT meta FROM observations WHERE series_id=? AND ts=?", (sid, ts))
        if existing and existing["meta"] and '"contract"' in existing["meta"]:
            # keep the contract recorded on the day it was first seen
            import json
            m["contract"] = json.loads(existing["meta"]).get("contract")
        else:
            m["contract"] = contract
            if prev_contract and prev_contract != contract:
                m["roll"] = True
                m["roll_from"] = prev_contract
        out.append((ts, v, m))
    return out


def update_front_info() -> int:
    n = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for sid, tk in (("gold_front_oi", "GC=F"), ("silver_front_oi", "SI=F")):
        try:
            info = yf.Ticker(tk).info or {}
            oi = info.get("openInterest")
            meta = {"contract": info.get("underlyingSymbol"), "short_name": info.get("shortName"),
                    "expire": datetime.fromtimestamp(info["expireDate"], tz=timezone.utc).strftime("%Y-%m-%d") if info.get("expireDate") else None,
                    "quote_time": datetime.fromtimestamp(info["regularMarketTime"], tz=timezone.utc).isoformat(timespec="seconds") if info.get("regularMarketTime") else None,
                    "last": info.get("regularMarketPrice"), "prev_close": info.get("regularMarketPreviousClose"),
                    "volume": info.get("regularMarketVolume")}
            if oi is not None:
                n += db.upsert_observations(sid, [(today, float(oi), meta)])
        except Exception as e:  # noqa: BLE001
            log.warning("front info %s failed: %s", tk, e)
    return n


def update_intraday() -> int:
    n = 0
    for sid, tk in INTRADAY.items():
        try:
            h = _fetch(tk, "5d", "5m")
            rows = _rows_from_history(h, daily=False)
            n += db.upsert_observations(sid, rows)
        except Exception as e:  # noqa: BLE001
            log.warning("intraday %s failed: %s", sid, e)
    # prune intraday older than 60 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    with db.tx() as c:
        for sid in INTRADAY:
            c.execute("DELETE FROM observations WHERE series_id=? AND ts<?", (sid, cutoff))
    return n


def contract_tickers(metal: str, n_months: int = 8, today: datetime | None = None) -> list[str]:
    """Next n listed contract months as Yahoo tickers, e.g. GCZ26.CMX."""
    today = today or datetime.now(timezone.utc)
    root, months = ("GC", GOLD_MONTHS) if metal == "gold" else ("SI", SILVER_MONTHS)
    out, y, m = [], today.year, today.month
    while len(out) < n_months:
        for code in months:
            mm = MONTH_CODES[code]
            if (y, mm) >= (today.year, today.month):
                out.append(f"{root}{code}{str(y)[-2:]}.CMX")
                if len(out) >= n_months:
                    break
        y += 1
    return out


def update_curve() -> int:
    """Daily close/volume for each listed contract month (the futures curve)."""
    n = 0
    for metal in ("gold", "silver"):
        for tk in contract_tickers(metal):
            sid = f"fut_{tk.split('.')[0]}"
            db.ensure_series(sid, f"{'Gold' if metal=='gold' else 'Silver'} futures {tk.split('.')[0]}", "future_contract", "USD/oz",
                             f"yfinance:{tk}", "daily", "Individual COMEX contract month; Yahoo daily close and volume.")
            try:
                h = _fetch(tk, "1mo", "1d")
                rows = _rows_from_history(h, daily=True)
                for r in rows:
                    r[2]["metal"] = metal
                    r[2]["contract"] = tk.split(".")[0]
                n += db.upsert_observations(sid, rows)
            except Exception as e:  # noqa: BLE001
                log.warning("curve %s failed: %s", tk, e)
    return n

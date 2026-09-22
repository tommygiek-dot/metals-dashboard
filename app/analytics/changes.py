"""Period changes, gold/silver ratio, realized volatility, session status, quote provenance.

Definitions (shown in the UI's method notes):
  * "Today" for a futures series = last daily close available vs the previous daily close of the SAME series.
    Yahoo's daily bar for the current session updates intraday, so during a session "today" is a live change.
  * 1w / 1m / 3m / YTD / 1y changes compare the latest close with the last close on or before the reference date.
  * Realized volatility = annualized stdev of daily log returns over N sessions (N = 10, 21, 63).
  * Gold/silver ratio = gold close / silver close on the same date, same instrument family (continuous futures).
"""
from __future__ import annotations
import math
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

from .. import db
from ..config import DISPLAY_TZ

ET = ZoneInfo("America/New_York")


def is_globex_metals_open(now: datetime | None = None) -> dict:
    """COMEX/Globex metals: Sun 18:00 ET -> Fri 17:00 ET, daily maintenance 17:00-18:00 ET.
    Exchange holidays are not modelled here (data simply will not update)."""
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    wd, hm = now.weekday(), now.hour * 60 + now.minute
    if wd == 5:
        open_ = False
    elif wd == 6:
        open_ = hm >= 18 * 60
    elif wd == 4:
        open_ = hm < 17 * 60
    else:
        open_ = not (17 * 60 <= hm < 18 * 60)
    return {"open": open_, "session": "COMEX Globex (electronic)", "now_et": now.isoformat(timespec="minutes"),
            "rule": "Sun 6pm-Fri 5pm ET, daily break 5-6pm ET; exchange holidays not modelled"}


def is_us_equity_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    hm = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 30 <= hm < 16 * 60


def _daily(series_id: str, since: str | None = None) -> list[dict]:
    return db.get_series(series_id, since=since)


def _close_on_or_before(rows: list[dict], d: str) -> dict | None:
    best = None
    for r in rows:
        if r["ts"] <= d:
            best = r
        else:
            break
    return best


def period_changes(series_id: str, tz: str = DISPLAY_TZ) -> dict:
    rows = _daily(series_id, since=(date.today() - timedelta(days=400)).isoformat())
    if not rows:
        return {"series_id": series_id, "available": False}
    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    last_d = datetime.strptime(last["ts"][:10], "%Y-%m-%d").date()
    refs = {
        "1d": prev["ts"] if prev else None,
        "1w": (last_d - timedelta(days=7)).isoformat(),
        "1m": (last_d - timedelta(days=30)).isoformat(),
        "3m": (last_d - timedelta(days=91)).isoformat(),
        "ytd": f"{last_d.year - 1}-12-31",
        "1y": (last_d - timedelta(days=365)).isoformat(),
    }
    out = {"series_id": series_id, "available": True, "last": last["value"], "last_ts": last["ts"],
           "meta": last.get("meta"), "ingested_at": last.get("ingested_at"), "changes": {}}
    for k, ref in refs.items():
        base = _close_on_or_before(rows, ref) if ref else None
        if base and base["value"]:
            out["changes"][k] = {"abs": last["value"] - base["value"], "pct": (last["value"] / base["value"] - 1) * 100,
                                 "base_ts": base["ts"], "base": base["value"]}
        else:
            out["changes"][k] = None
    return out


def realized_vol(series_id: str, windows=(10, 21, 63)) -> dict:
    rows = _daily(series_id, since=(date.today() - timedelta(days=200)).isoformat())
    vals = [r["value"] for r in rows if r["value"]]
    rets = [math.log(vals[i] / vals[i - 1]) for i in range(1, len(vals)) if vals[i - 1] > 0]
    out = {}
    for w in windows:
        if len(rets) >= w:
            seg = rets[-w:]
            m = sum(seg) / w
            var = sum((x - m) ** 2 for x in seg) / (w - 1)
            out[f"{w}d"] = math.sqrt(var) * math.sqrt(252) * 100
        else:
            out[f"{w}d"] = None
    return out


def ratio_series(limit_days: int = 3650) -> list[dict]:
    since = (date.today() - timedelta(days=limit_days)).isoformat()
    g = {r["ts"]: r["value"] for r in _daily("gold_fut_cont", since)}
    s = {r["ts"]: r["value"] for r in _daily("silver_fut_cont", since)}
    out = []
    for ts in sorted(set(g) & set(s)):
        if g[ts] and s[ts]:
            out.append({"ts": ts, "value": g[ts] / s[ts]})
    return out


def ratio_context() -> dict:
    rs = ratio_series()
    if not rs:
        return {"available": False}
    vals = [r["value"] for r in rs]
    cur = vals[-1]
    def pct_rank(window):
        seg = vals[-window:] if len(vals) >= window else vals
        return 100.0 * sum(1 for v in seg if v <= cur) / len(seg)
    def stats(window):
        seg = vals[-window:] if len(vals) >= window else vals
        return {"min": min(seg), "max": max(seg), "mean": sum(seg) / len(seg), "n": len(seg)}
    return {"available": True, "value": cur, "ts": rs[-1]["ts"],
            "prev": vals[-2] if len(vals) > 1 else None,
            "percentile_1y": pct_rank(252), "percentile_5y": pct_rank(1260), "percentile_10y": pct_rank(2520),
            "stats_1y": stats(252), "stats_5y": stats(1260), "stats_10y": stats(2520)}


def quote_provenance(series_id: str, now: datetime | None = None) -> dict:
    """Source, instrument, quote timestamp, age and a delay label for a card header."""
    meta = db.series_meta(series_id) or {}
    last = db.latest(series_id)
    now = now or datetime.now(timezone.utc)
    age_min = None
    quote_ts = None
    if last:
        quote_ts = last["ts"]
        try:
            if len(quote_ts) == 10:  # daily
                ing = datetime.fromisoformat(last["ingested_at"])
                age_min = (now - ing).total_seconds() / 60
            else:
                age_min = (now - datetime.fromisoformat(quote_ts)).total_seconds() / 60
        except Exception:  # noqa: BLE001
            pass
    return {"series_id": series_id, "name": meta.get("name"), "instrument_type": meta.get("instrument_type"),
            "unit": meta.get("unit"), "source": meta.get("source"), "cadence": meta.get("cadence"),
            "notes": meta.get("notes"), "quote_ts": quote_ts, "ingested_at": (last or {}).get("ingested_at"),
            "age_minutes": age_min, "delay_label": "delayed/indicative (Yahoo Finance)" if (meta.get("source") or "").startswith("yfinance") else "as published",
            "contract": ((last or {}).get("meta") or {}).get("contract")}


def to_display_tz(iso: str, tz: str = DISPLAY_TZ) -> str:
    try:
        d = datetime.fromisoformat(iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:  # noqa: BLE001
        return iso

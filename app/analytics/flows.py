"""Positioning (CFTC COT) and ETF holdings views."""
from __future__ import annotations
import json
from datetime import date, timedelta

from .. import db
from ..collectors.cftc import CONTRACTS

# field groups per report family (Socrata column names)
LEGACY = {
    "noncomm_long": "noncomm_positions_long_all", "noncomm_short": "noncomm_positions_short_all", "noncomm_spread": "noncomm_postions_spread_all",
    "comm_long": "comm_positions_long_all", "comm_short": "comm_positions_short_all",
    "nonrept_long": "nonrept_positions_long_all", "nonrept_short": "nonrept_positions_short_all", "open_interest": "open_interest_all",
}
DISAGG = {
    "prod_long": "prod_merc_positions_long", "prod_short": "prod_merc_positions_short",
    "swap_long": "swap_positions_long_all", "swap_short": "swap__positions_short_all", "swap_spread": "swap__positions_spread_all",
    "mm_long": "m_money_positions_long_all", "mm_short": "m_money_positions_short_all", "mm_spread": "m_money_positions_spread",
    "other_long": "other_rept_positions_long", "other_short": "other_rept_positions_short",
    "nonrept_long": "nonrept_positions_long_all", "nonrept_short": "nonrept_positions_short_all", "open_interest": "open_interest_all",
}


def _get(d: dict, key: str):
    v = d.get(key)
    if v is None:
        # Socrata column names vary slightly between datasets (double underscores, typos); try loose match
        k2 = key.replace("__", "_")
        for k in d:
            if k.replace("__", "_") == k2:
                return d[k]
    return v


def cot_history(metal: str, report_type: str, weeks: int = 260) -> list[dict]:
    code = CONTRACTS[metal]
    rows = db.q("SELECT report_date, market_name, data, retrieved_at FROM cot WHERE report_type=? AND contract_code=? ORDER BY report_date DESC LIMIT ?",
                (report_type, code, weeks))
    out = []
    fields = DISAGG if report_type.startswith("disagg") else LEGACY
    for r in reversed(rows):
        d = json.loads(r["data"])
        rec = {"report_date": r["report_date"], "retrieved_at": r["retrieved_at"]}
        for k, col in fields.items():
            rec[k] = _get(d, col)
        if report_type.startswith("disagg"):
            if rec.get("mm_long") is not None and rec.get("mm_short") is not None:
                rec["mm_net"] = rec["mm_long"] - rec["mm_short"]
            if rec.get("prod_long") is not None and rec.get("prod_short") is not None:
                rec["prod_net"] = rec["prod_long"] - rec["prod_short"]
            if rec.get("swap_long") is not None and rec.get("swap_short") is not None:
                rec["swap_net"] = rec["swap_long"] - rec["swap_short"]
            comm_like = (rec.get("prod_net") or 0) + (rec.get("swap_net") or 0)
            rec["comm_like_net"] = comm_like
        else:
            if rec.get("noncomm_long") is not None and rec.get("noncomm_short") is not None:
                rec["noncomm_net"] = rec["noncomm_long"] - rec["noncomm_short"]
            if rec.get("comm_long") is not None and rec.get("comm_short") is not None:
                rec["comm_net"] = rec["comm_long"] - rec["comm_short"]
        out.append(rec)
    return out


def _pct_rank(vals: list[float], cur: float) -> float | None:
    vals = [v for v in vals if v is not None]
    return 100.0 * sum(1 for v in vals if v <= cur) / len(vals) if vals else None


def cot_view(metal: str = "gold", report_type: str = "disagg_fut", weeks: int = 260) -> dict:
    hist = cot_history(metal, report_type, weeks)
    if not hist:
        return {"available": False, "metal": metal, "report_type": report_type}
    key = "mm_net" if report_type.startswith("disagg") else "noncomm_net"
    cur, prev = hist[-1], (hist[-2] if len(hist) > 1 else None)
    series = [h.get(key) for h in hist]
    ctx = {
        "key": key, "value": cur.get(key), "change_wow": (cur.get(key) - prev.get(key)) if prev and cur.get(key) is not None and prev.get(key) is not None else None,
        "percentile_1y": _pct_rank(series[-52:], cur.get(key)) if cur.get(key) is not None else None,
        "percentile_3y": _pct_rank(series[-156:], cur.get(key)) if cur.get(key) is not None else None,
        "percentile_5y": _pct_rank(series, cur.get(key)) if cur.get(key) is not None else None,
        "open_interest": cur.get("open_interest"),
        "oi_change_wow": (cur.get("open_interest") - prev.get("open_interest")) if prev and cur.get("open_interest") and prev.get("open_interest") else None,
        "report_date": cur["report_date"], "retrieved_at": cur["retrieved_at"],
    }
    # long/short participation of the speculative group as share of OI
    if report_type.startswith("disagg") and cur.get("open_interest"):
        ctx["mm_long_share_pct"] = 100.0 * (cur.get("mm_long") or 0) / cur["open_interest"]
        ctx["mm_short_share_pct"] = 100.0 * (cur.get("mm_short") or 0) / cur["open_interest"]
    label = {"legacy_fut": "Legacy, futures only", "legacy_futopt": "Legacy, futures + options combined",
             "disagg_fut": "Disaggregated, futures only", "disagg_futopt": "Disaggregated, futures + options combined"}[report_type]
    return {"available": True, "metal": metal, "report_type": report_type, "label": label, "contract_code": CONTRACTS[metal],
            "contract": "COMEX gold (100 oz)" if metal == "gold" else "COMEX silver (5,000 oz)",
            "context": ctx, "history": hist,
            "notes": "Positions are as of the Tuesday report date; released Fridays ~3:30pm ET. Report families are not comparable and are never spliced. "
                     "Managed money (disaggregated) ≈ hedge funds/CTAs; non-commercial (legacy) is broader and includes swap dealers' spec books."}


def etf_view(days: int = 365) -> dict:
    since = (date.today() - timedelta(days=days)).isoformat()
    out = {"funds": {}, "notes": "Implied net metal change = Δ ounces in trust. Not a cash-flow figure and not Δ AUM (which moves with price)."}
    for sid in ("slv_oz", "iau_oz", "gld_oz"):
        meta = db.series_meta(sid)
        if not meta:
            continue
        rows = db.get_series(sid, since=since)
        if not rows:
            out["funds"][sid] = {"available": False, "series": meta}
            continue
        last = rows[-1]
        def delta(n):
            if len(rows) > n:
                return last["value"] - rows[-1 - n]["value"]
            return None
        # find value ~30 days back by date
        def delta_days(nd):
            target = (date.fromisoformat(last["ts"]) - timedelta(days=nd)).isoformat()
            base = None
            for r in rows:
                if r["ts"] <= target:
                    base = r
            return (last["value"] - base["value"]) if base else None
        out["funds"][sid] = {"available": True, "series": meta, "last": last, "delta_1obs": delta(1), "delta_7d": delta_days(7),
                             "delta_30d": delta_days(30), "n_obs": len(rows),
                             "points": [{"t": r["ts"], "v": r["value"]} for r in rows]}
    return out

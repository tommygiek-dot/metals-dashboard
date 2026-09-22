"""Futures curve snapshot: each listed contract month's latest close, volume, spreads vs the front, and the
roll state of Yahoo's continuous symbol."""
from __future__ import annotations
from datetime import date, timedelta

from .. import db
from ..collectors.prices_yf import contract_tickers, MONTH_CODES


def _contract_date(sym: str) -> str:
    # GCZ26 -> 2026-12
    code, yy = sym[2], sym[3:5]
    return f"20{yy}-{MONTH_CODES[code]:02d}"


def curve_snapshot() -> dict:
    out = {}
    for metal in ("gold", "silver"):
        pts = []
        for tk in contract_tickers(metal, 8):
            sym = tk.split(".")[0]
            last = db.latest(f"fut_{sym}")
            if not last:
                continue
            pts.append({"contract": sym, "month": _contract_date(sym), "ts": last["ts"], "close": last["value"],
                        "volume": (last.get("meta") or {}).get("volume")})
        # reference = the ACTIVE contract (highest volume), not a thin expiring front month
        front = max(pts, key=lambda p: p.get("volume") or 0) if pts else None
        for p in pts:
            p["active"] = front is not None and p["contract"] == front["contract"]
            p["spread_vs_front"] = (p["close"] - front["close"]) if front else None
            p["spread_pct"] = ((p["close"] / front["close"] - 1) * 100) if front and front["close"] else None
        # annualized carry from the active contract to the next month after it (contango/backwardation gauge)
        carry = None
        if front:
            later = [p for p in pts if p["month"] > front["month"]]
            if later and front["close"]:
                nxt = later[0]
                y1, m1 = (int(x) for x in front["month"].split("-"))
                y2, m2 = (int(x) for x in nxt["month"].split("-"))
                months = (y2 - y1) * 12 + (m2 - m1)
                if months > 0:
                    carry = (nxt["close"] / front["close"] - 1) * (12 / months) * 100
        cont = db.latest(f"{metal}_fut_cont")
        oi = db.latest(f"{metal}_front_oi")
        rolls = db.q("SELECT ts, meta FROM observations WHERE series_id=? AND meta LIKE '%\"roll\": true%' ORDER BY ts DESC LIMIT 5", (f"{metal}_fut_cont",))
        out[metal] = {"points": pts, "annualized_carry_pct": carry,
                      "state": ("contango" if carry and carry > 0 else "backwardation" if carry and carry < 0 else None),
                      "continuous_contract": ((cont or {}).get("meta") or {}).get("contract"),
                      "front_oi": oi, "recent_rolls": rolls,
                      "roll_method": "Yahoo's GC=F/SI=F switch to the next active contract on Yahoo's own schedule (undocumented). We record the contract each day and flag the switch; history is NOT back-adjusted, so a price gap on roll days is a contract change, not a market move."}
    return out

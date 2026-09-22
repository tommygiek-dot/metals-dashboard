"""Driver board: for each driver, the latest observation, its change, a stance for gold and silver, the
plain-English mechanism, evidence strength, freshness, and whether it is a catalyst (moved today), a
condition (slow-moving state) or background (structural). Deterministic rules over stored data.

Stance vocabulary: support | pressure | mixed | insufficient.  Rules are hypotheses, not laws: the sign of
the gold–real-yield and gold–dollar relationships is checked against the rolling correlation from research.py
and the stance is downgraded to "mixed" when the recent relationship has been unstable.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, date

from .. import db
from ..config import ANALYTICS_VERSION
from .changes import period_changes
from . import flows, research


def _fresh(ts: str | None, cadence: str) -> tuple[str, int | None]:
    if not ts:
        return "missing", None
    try:
        d = date.fromisoformat(ts[:10])
    except ValueError:
        return "unknown", None
    age = (date.today() - d).days
    limit = {"daily": 3, "intraday": 1, "weekly": 10, "monthly": 45, "quarterly": 120, "annual": 400}.get(cadence, 5)
    return ("fresh" if age <= limit else "stale"), age


def _obs(sid: str) -> dict:
    pc = period_changes(sid)
    meta = db.series_meta(sid) or {}
    fresh, age = _fresh(pc.get("last_ts"), meta.get("cadence", "daily"))
    return {"series_id": sid, "name": meta.get("name"), "source": meta.get("source"), "unit": meta.get("unit"), "available": pc.get("available", False),
            "last": pc.get("last"), "last_ts": pc.get("last_ts"), "changes": pc.get("changes", {}), "freshness": fresh, "age_days": age}


def _first_available(*ids: str) -> dict | None:
    for s in ids:
        if db.series_meta(s) and db.latest(s):
            return _obs(s)
    return None


def _stance_from_change(chg: float | None, sign_if_up: str, thresh: float) -> str:
    if chg is None:
        return "insufficient"
    if abs(chg) < thresh:
        return "mixed"
    return sign_if_up if chg > 0 else ("pressure" if sign_if_up == "support" else "support")


def driver_board(persist: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    R = research.research_view()
    h1 = R["h1_gold_vs_real_yield"]; h2 = R["h2_gold_vs_dxy"]
    corr_ry = (h1.get("latest") or {}).get("v") if h1.get("available") else None
    beta_dxy = (h2.get("latest") or {}).get("v") if h2.get("available") else None
    drivers = []

    # --- 1. Real yields ---
    ry = _first_available("dfii10", "ust_real_10y")
    if ry:
        c1 = (ry["changes"].get("1d") or {}).get("abs"); c1w = (ry["changes"].get("1w") or {}).get("abs")
        relation_ok = corr_ry is not None and corr_ry < -0.15
        st = _stance_from_change(c1, "pressure", 0.03)
        if st != "insufficient" and not relation_ok:
            st = "mixed"
        drivers.append({"id": "real_yields", "name": "US 10y real yield (TIPS)", "group": "macro", "kind": "catalyst" if c1 is not None and abs(c1) >= 0.05 else "condition",
                        "observation": ry, "delta_1d": c1, "delta_1w": c1w, "unit": "pct pts",
                        "stance_gold": st, "stance_silver": st,
                        "mechanism": "Gold pays no yield; a higher inflation-adjusted Treasury yield raises the opportunity cost of holding it. The link was strong 2008-2021 and has been looser since 2022.",
                        "evidence": {"strength": "strong" if relation_ok else "weak", "recent_corr_60d": corr_ry, "note": "60-session correlation of gold returns with Δreal yield; < −0.15 counts as an active relationship."},
                        "freshness": ry["freshness"]})
    # --- 2. Nominal yields ---
    ny = _first_available("dgs10", "ust_10y", "us10y")
    if ny:
        c1 = (ny["changes"].get("1d") or {}).get("abs")
        drivers.append({"id": "nominal_yields", "name": "US 10y nominal yield", "group": "macro", "kind": "catalyst" if c1 is not None and abs(c1) >= 0.06 else "condition",
                        "observation": ny, "delta_1d": c1, "delta_1w": (ny["changes"].get("1w") or {}).get("abs"), "unit": "pct pts",
                        "stance_gold": _stance_from_change(c1, "pressure", 0.04), "stance_silver": _stance_from_change(c1, "pressure", 0.04),
                        "mechanism": "Higher nominal yields tighten financial conditions and support the dollar; the effect on metals runs mostly through real yields, so read alongside breakevens.",
                        "evidence": {"strength": "moderate"}, "freshness": ny["freshness"]})
    # --- 3. Breakevens ---
    be = _first_available("t10yie", "ust_be_10y")
    if be:
        c1 = (be["changes"].get("1d") or {}).get("abs")
        drivers.append({"id": "breakevens", "name": "10y breakeven inflation", "group": "macro", "kind": "condition",
                        "observation": be, "delta_1d": c1, "delta_1w": (be["changes"].get("1w") or {}).get("abs"), "unit": "pct pts",
                        "stance_gold": _stance_from_change(c1, "support", 0.03), "stance_silver": _stance_from_change(c1, "support", 0.03),
                        "mechanism": "Rising inflation expectations lower real yields for a given nominal yield and raise demand for inflation hedges. Breakevens also carry liquidity and risk premia, so small moves are noise.",
                        "evidence": {"strength": "moderate"}, "freshness": be["freshness"]})
    # --- 4. Dollar ---
    dx = _first_available("dxy")
    if dx:
        p1 = (dx["changes"].get("1d") or {}).get("pct")
        relation_ok = beta_dxy is not None and beta_dxy < -0.2
        st = _stance_from_change(p1, "pressure", 0.25)
        if st != "insufficient" and not relation_ok:
            st = "mixed"
        drivers.append({"id": "dollar", "name": "US dollar (ICE DXY)", "group": "macro", "kind": "catalyst" if p1 is not None and abs(p1) >= 0.5 else "condition",
                        "observation": dx, "delta_1d": p1, "delta_1w": (dx["changes"].get("1w") or {}).get("pct"), "unit": "%",
                        "stance_gold": st, "stance_silver": st,
                        "mechanism": "Metals are priced in dollars; a weaker dollar makes them cheaper for non-dollar buyers. DXY is euro-heavy — the Fed's broad trade-weighted index (FRED DTWEXBGS) is the wider measure.",
                        "evidence": {"strength": "strong" if relation_ok else "weak", "recent_beta_60d": beta_dxy}, "freshness": dx["freshness"]})
    tw = _first_available("dtwexbgs")
    if tw:
        drivers.append({"id": "broad_dollar", "name": "Broad trade-weighted dollar (Fed)", "group": "macro", "kind": "condition", "observation": tw,
                        "delta_1d": (tw["changes"].get("1d") or {}).get("pct"), "delta_1w": (tw["changes"].get("1w") or {}).get("pct"), "unit": "%",
                        "stance_gold": _stance_from_change((tw["changes"].get("1w") or {}).get("pct"), "pressure", 0.4), "stance_silver": _stance_from_change((tw["changes"].get("1w") or {}).get("pct"), "pressure", 0.4),
                        "mechanism": "Same channel as DXY but weighted by trade, including China, Mexico and Canada. Published with a lag; a condition, not a same-day catalyst.",
                        "evidence": {"strength": "moderate"}, "freshness": tw["freshness"]})
    # --- 5. Policy rate ---
    ff = _first_available("dff", "nyfed_effr")
    if ff:
        drivers.append({"id": "policy_rate", "name": "Fed funds effective rate", "group": "macro", "kind": "condition", "observation": ff,
                        "delta_1d": (ff["changes"].get("1d") or {}).get("abs"), "delta_1w": (ff["changes"].get("1m") or {}).get("abs"), "unit": "pct pts",
                        "stance_gold": "mixed", "stance_silver": "mixed",
                        "mechanism": "The level of policy rates sets short real yields and carry; what moves metals day to day is the *expected path* (FOMC statements, speeches, data), which this dashboard tracks through the events list and official feeds rather than a market-implied path (no free reliable source).",
                        "evidence": {"strength": "context"}, "freshness": ff["freshness"]})
    # --- 6. Oil ---
    oil = _first_available("wti_fut", "dcoilwtico")
    if oil:
        p1 = (oil["changes"].get("1d") or {}).get("pct")
        drivers.append({"id": "oil", "name": "WTI crude", "group": "macro", "kind": "catalyst" if p1 is not None and abs(p1) >= 2.5 else "condition", "observation": oil,
                        "delta_1d": p1, "delta_1w": (oil["changes"].get("1w") or {}).get("pct"), "unit": "%",
                        "stance_gold": "mixed", "stance_silver": "mixed",
                        "mechanism": "Oil feeds inflation expectations (supportive) but also mining costs and, when driven by geopolitics, safe-haven demand. Direction is ambiguous without the cause of the move.",
                        "evidence": {"strength": "weak"}, "freshness": oil["freshness"]})
    # --- 7. Copper (silver industrial proxy) ---
    cu = _first_available("copper_fut")
    if cu:
        p1 = (cu["changes"].get("1d") or {}).get("pct")
        drivers.append({"id": "copper", "name": "Copper (industrial cycle proxy)", "group": "silver-industrial", "kind": "catalyst" if p1 is not None and abs(p1) >= 2 else "condition", "observation": cu,
                        "delta_1d": p1, "delta_1w": (cu["changes"].get("1w") or {}).get("pct"), "unit": "%",
                        "stance_gold": "mixed", "stance_silver": _stance_from_change(p1, "support", 0.7),
                        "mechanism": "Roughly half of silver demand is industrial; copper is the market's read on the industrial cycle and China. Gold has little direct link.",
                        "evidence": {"strength": "moderate"}, "freshness": cu["freshness"]})
    # --- 8. Equities / VIX ---
    vx = _first_available("vix", "vixcls"); sp = _first_available("spx")
    if vx:
        c1 = (vx["changes"].get("1d") or {}).get("abs")
        drivers.append({"id": "risk", "name": "Equity volatility (VIX)", "group": "risk", "kind": "catalyst" if c1 is not None and abs(c1) >= 2 else "condition", "observation": vx,
                        "delta_1d": c1, "delta_1w": (vx["changes"].get("1w") or {}).get("abs"), "unit": "pts",
                        "stance_gold": "mixed", "stance_silver": _stance_from_change(c1, "pressure", 1.5),
                        "mechanism": "A volatility spike can lift gold as a haven, but forced deleveraging sells everything liquid, gold included (March 2020). Silver trades more like a risk asset. Direction depends on whether the stress is a liquidity event.",
                        "evidence": {"strength": "weak", "spx_1d_pct": (sp or {}).get("changes", {}).get("1d", {}) and (sp["changes"]["1d"] or {}).get("pct")}, "freshness": vx["freshness"]})
    # --- 9. Credit stress / liquidity ---
    hy = _first_available("bamlh0a0hym2")
    if hy:
        c1w = (hy["changes"].get("1w") or {}).get("abs")
        drivers.append({"id": "credit", "name": "US high-yield credit spread", "group": "risk", "kind": "condition", "observation": hy,
                        "delta_1d": (hy["changes"].get("1d") or {}).get("abs"), "delta_1w": c1w, "unit": "pct pts",
                        "stance_gold": "mixed", "stance_silver": _stance_from_change(c1w, "pressure", 0.15),
                        "mechanism": "Widening spreads signal financial stress: eventually supportive for gold via expected easing, negative for silver via growth. Slow-moving context.",
                        "evidence": {"strength": "weak"}, "freshness": hy["freshness"]})
    fed_bs = _first_available("walcl")
    if fed_bs:
        drivers.append({"id": "fed_balance_sheet", "name": "Fed balance sheet (total assets)", "group": "liquidity", "kind": "background", "observation": fed_bs,
                        "delta_1d": None, "delta_1w": (fed_bs["changes"].get("1m") or {}).get("pct"), "unit": "% (1m)",
                        "stance_gold": "mixed", "stance_silver": "mixed",
                        "mechanism": "Liquidity backdrop. Weekly data; treat as background, never as a same-day explanation.",
                        "evidence": {"strength": "context"}, "freshness": fed_bs["freshness"]})
    # --- 10. Positioning (COT) ---
    for metal in ("gold", "silver"):
        cv = flows.cot_view(metal, "disagg_fut", 260)
        if cv.get("available"):
            ctx = cv["context"]
            pct = ctx.get("percentile_3y")
            wow = ctx.get("change_wow")
            if pct is None:
                st = "insufficient"
            elif pct >= 85:
                st = "mixed"   # crowded long: supportive trend, but vulnerable to liquidation
            elif pct <= 15:
                st = "mixed"   # washed-out: room to rebuild, but trend is weak
            else:
                st = "support" if (wow or 0) > 0 else "pressure" if (wow or 0) < 0 else "mixed"
            fresh, age = _fresh(ctx.get("report_date"), "weekly")
            d = {"id": f"cot_{metal}", "name": f"Managed-money net position, COMEX {metal} (disaggregated, futures only)", "group": "positioning", "kind": "condition",
                 "observation": {"available": True, "last": ctx.get("value"), "last_ts": ctx.get("report_date"), "unit": "contracts", "source": "CFTC COT via Socrata",
                                 "name": cv["label"], "freshness": fresh, "age_days": age},
                 "delta_1d": None, "delta_1w": wow, "unit": "contracts (w/w)", "extra": {"percentile_1y": ctx.get("percentile_1y"), "percentile_3y": pct, "open_interest": ctx.get("open_interest"), "oi_change_wow": ctx.get("oi_change_wow")},
                 "stance_gold": st if metal == "gold" else None, "stance_silver": st if metal == "silver" else None,
                 "mechanism": "Speculative net length shows how crowded the trade is. Extreme longs (top ~15% of 3 years) raise liquidation risk on bad news; extreme shorts leave room for short-covering rallies. Positions are as of Tuesday and released Friday, so this is context, not a daily catalyst.",
                 "evidence": {"strength": "moderate"}, "freshness": fresh}
            drivers.append(d)
    # --- 11. ETF holdings ---
    ev = flows.etf_view(60)
    for sid, metal in (("iau_oz", "gold"), ("slv_oz", "silver")):
        f = ev["funds"].get(sid)
        if f and f.get("available"):
            d7 = f.get("delta_7d"); last = f["last"]
            pct7 = (d7 / (last["value"] - d7) * 100) if d7 is not None and last["value"] and (last["value"] - d7) else None
            fresh, age = _fresh(last["ts"], "daily")
            d = {"id": f"etf_{metal}", "name": f"{'IAU' if metal=='gold' else 'SLV'} ounces in trust (implied net metal change)", "group": "flows", "kind": "condition",
                 "observation": {"available": True, "last": last["value"], "last_ts": last["ts"], "unit": "ounces", "source": f["series"]["source"], "name": f["series"]["name"], "freshness": fresh, "age_days": age},
                 "delta_1d": f.get("delta_1obs"), "delta_1w": d7, "unit": "ounces", "extra": {"delta_7d_pct": pct7, "delta_30d": f.get("delta_30d"), "n_obs": f.get("n_obs")},
                 "stance_gold": (_stance_from_change(pct7, "support", 0.3) if metal == "gold" else None), "stance_silver": (_stance_from_change(pct7, "support", 0.3) if metal == "silver" else None),
                 "mechanism": "Creations add metal to the trust, redemptions remove it. Δounces is an implied net metal change — not investor cash flow and not AUM (which moves with price). One fund per metal here (GLD source pending), so it is a partial read.",
                 "evidence": {"strength": "moderate" if (f.get("n_obs") or 0) >= 5 else "weak (few observations yet)"}, "freshness": fresh}
            drivers.append(d)
    # --- 12. Curve / physical ---
    from .curve import curve_snapshot
    cs = curve_snapshot()
    for metal in ("gold", "silver"):
        c = cs.get(metal, {})
        carry = c.get("annualized_carry_pct")
        if carry is not None:
            st = "mixed"
            drivers.append({"id": f"curve_{metal}", "name": f"COMEX {metal} curve (front spread, annualized)", "group": "physical", "kind": "condition",
                            "observation": {"available": True, "last": carry, "last_ts": (c["points"][0]["ts"] if c.get("points") else None), "unit": "% p.a.", "source": "yfinance contract months", "name": f"{metal} calendar spread", "freshness": "fresh", "age_days": 0},
                            "delta_1d": None, "delta_1w": None, "unit": "% p.a.", "extra": {"state": c.get("state"), "front": c["points"][0]["contract"] if c.get("points") else None},
                            "stance_gold": st if metal == "gold" else None, "stance_silver": st if metal == "silver" else None,
                            "mechanism": "Contango near the funding rate is normal. A spread collapsing toward backwardation signals near-term tightness or a lease-rate spike; that is unusual for gold and worth checking against inventories and delivery notices before drawing conclusions.",
                            "evidence": {"strength": "context"}, "freshness": "fresh"})
    out = {"generated_at": now.isoformat(timespec="seconds"), "analytics_version": ANALYTICS_VERSION, "drivers": drivers,
           "legend": {"support": "current evidence leans supportive", "pressure": "current evidence leans negative", "mixed": "effects offset, relationship unstable, or extreme positioning cuts both ways", "insufficient": "no fresh data"},
           "classes": {"catalyst": "moved enough today to be a candidate explanation", "condition": "slow-moving state that shapes the tape", "background": "structural; never explains a daily move"}}
    if persist:
        d = date.today().isoformat()
        with db.tx() as c:
            for dr in drivers:
                c.execute("INSERT OR REPLACE INTO driver_state(driver_id,date,generated_at,body) VALUES(?,?,?,?)", (dr["id"], d, out["generated_at"], json.dumps(dr, default=str)))
    return out

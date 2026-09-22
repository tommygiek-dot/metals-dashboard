"""Reproducible research hypotheses (from the reference repos), re-run on our own stored data.

H1  Gold vs 10y real yield: rolling 60-session correlation of daily gold log-returns with daily changes in the
    10y TIPS real yield. Reference: Gold-Regime-Shift claims the pre-2022 negative link weakened after Jan 2022.
    Data: gold_fut_cont + dfii10 (FRED) or ust_real_10y (Treasury) when FRED is absent.
H2  Gold vs dollar: rolling 60-session beta of gold returns on DXY returns.
H3  Silver fair-value indicator (silver-commodity-market-timing): OLS of Δsilver on Δbreakeven and Δ10y nominal
    fitted IN-SAMPLE 2018-04..2021-03; betas frozen; 15-session indicator applied OUT-OF-SAMPLE 2022→today, scored
    by hit rate on the sign of the next 15-session silver return. Reference reports adj R² ≈ 0.10 in-sample.
Everything here is explanatory fit, not a forecast, and every window is labelled in-sample / out-of-sample.
"""
from __future__ import annotations
import math
from datetime import date

from .. import db


def _aligned(a_id: str, b_id: str, since: str) -> tuple[list[str], list[float], list[float]]:
    a = {r["ts"]: r["value"] for r in db.get_series(a_id, since=since) if r["value"] is not None}
    b = {r["ts"]: r["value"] for r in db.get_series(b_id, since=since) if r["value"] is not None}
    ds = sorted(set(a) & set(b))
    return ds, [a[d] for d in ds], [b[d] for d in ds]


def _corr(x, y):
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def _beta(x, y):
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return sxy / sxx if sxx > 0 else None


def rolling_relation(price_id: str, factor_id: str, window: int = 60, since: str = "2007-01-01", factor_is_return: bool = False,
                     stat: str = "corr") -> dict:
    ds, p, f = _aligned(price_id, factor_id, since)
    if len(ds) < window + 2:
        return {"available": False, "reason": f"need {window + 2} aligned observations, have {len(ds)}"}
    rp = [math.log(p[i] / p[i - 1]) for i in range(1, len(p))]
    rf = [(math.log(f[i] / f[i - 1]) if factor_is_return and f[i - 1] > 0 and f[i] > 0 else f[i] - f[i - 1]) for i in range(1, len(f))]
    out = []
    fn = _corr if stat == "corr" else _beta
    for i in range(window, len(rp) + 1):
        v = fn(rf[i - window:i], rp[i - window:i]) if stat == "beta" else fn(rp[i - window:i], rf[i - window:i])
        if v is not None:
            out.append({"t": ds[i], "v": v})
    def avg(lo, hi):
        seg = [o["v"] for o in out if lo <= o["t"] < hi]
        return sum(seg) / len(seg) if seg else None
    return {"available": True, "window": window, "stat": stat, "points": out[-2600:],
            "regime_means": {"2008-2021": avg("2008-01-01", "2022-01-01"), "2022-2023": avg("2022-01-01", "2024-01-01"),
                             "2024-now": avg("2024-01-01", "2999-01-01"), "last_250": (sum(o["v"] for o in out[-250:]) / len(out[-250:])) if out else None},
            "latest": out[-1] if out else None}


def silver_fair_value() -> dict:
    be = "t10yie" if db.latest("t10yie") else "ust_be_10y"
    nom = "dgs10" if db.latest("dgs10") else "ust_10y"
    ds, s, b = _aligned("silver_fut_cont", be, "2018-01-01")
    ds2, s2, nn = _aligned("silver_fut_cont", nom, "2018-01-01")
    common = sorted(set(ds) & set(ds2))
    if len(common) < 300:
        return {"available": False, "reason": "insufficient aligned data", "inputs": [be, nom]}
    sm = dict(zip(ds, s)); bm = dict(zip(ds, b)); nm = dict(zip(ds2, nn))
    rows = [(d, sm[d], bm[d], nm[d]) for d in common]
    # in-sample fit 2018-04-01 .. 2021-03-31 on daily changes: dP = a + b1*dI + b2*dR
    ins = [r for r in rows if "2018-04-01" <= r[0] <= "2021-03-31"]
    def diffs(seg):
        return [(seg[i][0], seg[i][1] - seg[i - 1][1], seg[i][2] - seg[i - 1][2], seg[i][3] - seg[i - 1][3]) for i in range(1, len(seg))]
    dins = diffs(ins)
    if len(dins) < 100:
        return {"available": False, "reason": "in-sample window too short"}
    # 2-variable OLS via normal equations
    import statistics as st
    y = [r[1] for r in dins]; x1 = [r[2] for r in dins]; x2 = [r[3] for r in dins]
    n = len(y); my, m1, m2 = st.mean(y), st.mean(x1), st.mean(x2)
    s11 = sum((a - m1) ** 2 for a in x1); s22 = sum((a - m2) ** 2 for a in x2); s12 = sum((a - m1) * (b - m2) for a, b in zip(x1, x2))
    s1y = sum((a - m1) * (c - my) for a, c in zip(x1, y)); s2y = sum((a - m2) * (c - my) for a, c in zip(x2, y))
    det = s11 * s22 - s12 * s12
    if det == 0:
        return {"available": False, "reason": "singular design"}
    b1 = (s1y * s22 - s2y * s12) / det
    b2 = (s2y * s11 - s1y * s12) / det
    a0 = my - b1 * m1 - b2 * m2
    resid = [c - (a0 + b1 * u + b2 * v) for c, u, v in zip(y, x1, x2)]
    sst = sum((c - my) ** 2 for c in y); sse = sum(r * r for r in resid)
    r2 = 1 - sse / sst if sst else None
    adj = 1 - (1 - r2) * (n - 1) / (n - 3) if r2 is not None else None
    # out-of-sample indicator: I = 100*(exp[b1*dI15 + b2*dR15 - ln(P_t/P_t-15)] - 1); threshold ±10 (reference), also ±5
    oos = [r for r in rows if r[0] >= "2022-01-01"]
    hits = {10: [0, 0], 5: [0, 0]}
    for i in range(15, len(oos) - 15):
        p0, p1 = oos[i - 15][1], oos[i][1]
        if p0 <= 0 or p1 <= 0:
            continue
        di = oos[i][2] - oos[i - 15][2]; dr = oos[i][3] - oos[i - 15][3]
        ind = 100 * (math.exp(b1 * di / p1 + b2 * dr / p1 - math.log(p1 / p0)) - 1)
        fwd = oos[i + 15][1] / p1 - 1
        for thr in hits:
            if ind >= thr:
                hits[thr][1] += 1; hits[thr][0] += 1 if fwd > 0 else 0
            elif ind <= -thr:
                hits[thr][1] += 1; hits[thr][0] += 1 if fwd < 0 else 0
    return {"available": True, "inputs": {"breakeven": be, "nominal": nom, "silver": "silver_fut_cont"},
            "in_sample": {"window": "2018-04-01..2021-03-31", "n": n, "beta_breakeven": b1, "beta_nominal": b2, "intercept": a0, "r2": r2, "adj_r2": adj,
                          "reference": "csatzky: β≈+0.2 / −0.1, adj R²≈0.10 (levels of XAG spot; ours uses futures)"},
            "out_of_sample": {"window": "2022-01-01..today", "hit_rate_thr10": (hits[10][0] / hits[10][1]) if hits[10][1] else None, "signals_thr10": hits[10][1],
                              "hit_rate_thr5": (hits[5][0] / hits[5][1]) if hits[5][1] else None, "signals_thr5": hits[5][1],
                              "read": "A hit rate near 50% means the in-sample fit carried no out-of-sample signal."}}


def research_view() -> dict:
    real_id = "dfii10" if db.latest("dfii10") else "ust_real_10y"
    return {
        "h1_gold_vs_real_yield": {**rolling_relation("gold_fut_cont", real_id, 60, "2007-01-01", False, "corr"), "factor": real_id,
                                  "hypothesis": "Rolling 60-session correlation of gold returns with Δ10y real yield was reliably negative 2008-2021 and has weakened toward zero since 2022.",
                                  "source": "Entrap-Io/Gold-Regime-Shift (in-sample OLS, regime split at 2022-01 chosen a priori)"},
        "h2_gold_vs_dxy": {**rolling_relation("gold_fut_cont", "dxy", 60, "2007-01-01", True, "beta"), "factor": "dxy",
                           "hypothesis": "Rolling 60-session beta of gold returns to DXY returns; historically negative, magnitude varies by regime."},
        "h3_silver_fair_value": silver_fair_value(),
        "caveats": ["Correlations and betas are descriptive; both legs often react to the same news.", "Continuous futures include roll gaps.",
                    "All conclusions are hypotheses to re-test, not rules."],
    }

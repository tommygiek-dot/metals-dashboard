"""Plain-English "at a glance" text for each tab. Deterministic, written from the same data the tab shows.
Each function returns a list of short sentences; the UI joins them into one paragraph."""
from __future__ import annotations
from datetime import datetime, timezone, date

from .. import db
from .changes import period_changes, ratio_context, realized_vol, is_globex_metals_open, to_display_tz


def _pct(v, d=1):
    return "n/a" if v is None else f"{v:+.{d}f}%"


def _n(v, d=0):
    return "n/a" if v is None else f"{v:,.{d}f}"


def _word_move(p):
    if p is None:
        return "has no fresh data"
    a = abs(p)
    size = "is flat" if a < 0.3 else "is slightly" if a < 1 else "is" if a < 2.5 else "is sharply"
    if a < 0.3:
        return size
    return f"{size} {'up' if p > 0 else 'down'}"


def overview() -> list[str]:
    out = []
    s = is_globex_metals_open()
    parts = []
    for metal, sid in (("Gold", "gold_fut_cont"), ("Silver", "silver_fut_cont")):
        pc = period_changes(sid)
        if not pc.get("available"):
            parts.append(f"{metal} has no price data yet.")
            continue
        c1 = (pc["changes"].get("1d") or {}).get("pct")
        c1w = (pc["changes"].get("1w") or {}).get("pct")
        ytd = (pc["changes"].get("ytd") or {}).get("pct")
        parts.append(f"{metal} {_word_move(c1)} today at ${_n(pc['last'], 2)} ({_pct(c1)}), {_pct(c1w)} on the week and {_pct(ytd)} this year.")
    out += parts
    rc = ratio_context()
    if rc.get("available"):
        pct5 = rc["percentile_5y"]
        rel = "silver is expensive relative to gold by five-year standards" if pct5 < 25 else "gold is expensive relative to silver by five-year standards" if pct5 > 75 else "the two are in their normal range against each other"
        out.append(f"One ounce of gold buys {rc['value']:.1f} ounces of silver, so {rel}.")
    dx = period_changes("dxy")
    if dx.get("available"):
        p = (dx["changes"].get("1d") or {}).get("pct")
        tail = f", which normally works {'against' if p > 0 else 'in favour of'} the metals" if p is not None and abs(p) >= 0.3 else ", so it is not the story today"
        out.append(f"The dollar {_word_move(p)} ({_pct(p, 2)}){tail}.")
    out.append(f"The futures market is {'open' if s['open'] else 'closed'} right now, and the prices here are delayed Yahoo quotes, not official settlements.")
    return out


def today() -> list[str]:
    from .briefing import get_briefing
    b = get_briefing()
    if not b.get("available"):
        return ["No briefing has been generated yet."]
    out = [f"This is the briefing for {b['date']}, built from the data and headlines collected up to {to_display_tz(b['generated_at'])}."]
    for m in ("gold", "silver"):
        t = (b["interpretations"].get(m) or {}).get("text")
        if t:
            out.append(t)
    n_conf = len(b.get("conflicts") or [])
    if n_conf:
        out.append(f"{n_conf} signal{'s' if n_conf != 1 else ''} point the other way, so treat the explanation as partial.")
    ev = b.get("upcoming") or []
    if ev:
        big = [e for e in ev if e.get("importance", 2) >= 3]
        if big:
            out.append(f"Next big scheduled item: {big[0]['title']} on {big[0]['when']}.")
    if b.get("diff", {}).get("new"):
        out.append(f"{len(b['diff']['new'])} of today's developments were not in the previous briefing.")
    return out


def drivers() -> list[str]:
    from .drivers import driver_board
    d = driver_board()
    ds = d["drivers"]
    cats = [x for x in ds if x["kind"] == "catalyst"]
    out = []
    if cats:
        out.append("Moved enough today to matter: " + ", ".join(x["name"].split(" (")[0] for x in cats) + ".")
    else:
        out.append("Nothing on the board moved enough today to count as a catalyst; the picture is set by slow-moving conditions.")
    for metal in ("gold", "silver"):
        sup = [x for x in ds if x.get(f"stance_{metal}") == "support" and x["kind"] != "background" and x["freshness"] == "fresh"]
        pre = [x for x in ds if x.get(f"stance_{metal}") == "pressure" and x["kind"] != "background" and x["freshness"] == "fresh"]
        mix = [x for x in ds if x.get(f"stance_{metal}") == "mixed"]
        lean = "leans supportive" if len(sup) > len(pre) else "leans negative" if len(pre) > len(sup) else "is balanced"
        out.append(f"For {metal} the fresh evidence {lean}: {len(sup)} supportive, {len(pre)} negative, {len(mix)} mixed or unstable.")
    stale = [x["name"] for x in ds if x["freshness"] == "stale"]
    if stale:
        out.append(f"Stale and therefore discounted: {', '.join(stale[:3])}.")
    out.append("Stances are rules applied to the latest numbers, not predictions; 'mixed' usually means the historical relationship is not holding right now.")
    return out


def positioning() -> list[str]:
    from .flows import cot_view, etf_view
    from .curve import curve_snapshot
    out = []
    for metal in ("gold", "silver"):
        cv = cot_view(metal, "disagg_fut", 260)
        if cv.get("available"):
            k = cv["context"]
            pct = k.get("percentile_3y")
            crowd = "very crowded on the long side" if pct is not None and pct >= 85 else "unusually light" if pct is not None and pct <= 15 else "in a normal range"
            wow = k.get("change_wow")
            trend = "added" if (wow or 0) > 0 else "cut" if (wow or 0) < 0 else "held"
            out.append(f"Speculators (managed money) in {metal} are net long {_n(k.get('value'))} contracts as of {k['report_date']}, {crowd} versus the last three years; they {trend} {_n(abs(wow or 0))} on the week.")
    ev = etf_view(60)
    bits = []
    for sid, label in (("iau_oz", "the IAU gold trust"), ("slv_oz", "the SLV silver trust")):
        f = ev["funds"].get(sid)
        if f and f.get("available"):
            d7 = f.get("delta_7d")
            if d7 is None:
                bits.append(f"{label} has {f['n_obs']} reading{'s' if f['n_obs'] != 1 else ''} so far (too few to call a trend)")
            else:
                bits.append(f"{label} {'added' if d7 > 0 else 'shed' if d7 < 0 else 'held'} {_n(abs(d7))} oz over the past week")
    if bits:
        out.append("Metal held by ETFs: " + "; ".join(bits) + ".")
    cs = curve_snapshot()
    st = []
    for metal in ("gold", "silver"):
        c = cs.get(metal, {})
        if c.get("state"):
            st.append(f"{metal} is in {c['state']} at about {c['annualized_carry_pct']:.1f}% a year")
    if st:
        out.append("The futures curve: " + " and ".join(st) + ". Contango near the interest rate is normal; backwardation would signal near-term tightness.")
    out.append("Positioning is weekly and lagged, so it explains the backdrop, not today's move.")
    return out


def physical() -> list[str]:
    from .physical import physical_view, premium_view
    p = physical_view()
    out = []
    for metal in ("gold", "silver"):
        c = p["comex"][metal]
        if not c.get("available"):
            out.append(f"No COMEX {metal} warehouse report has been dropped in yet, so there is no inventory read for {metal}.")
            continue
        t = c.get("total") or {}
        chg = t.get("total_chg")
        out.append(f"COMEX {metal} stocks were {_n(t.get('total'))} oz on {c['report_date']} ({_n(t.get('registered'))} registered), {'up' if (chg or 0) > 0 else 'down' if (chg or 0) < 0 else 'unchanged'} {_n(abs(chg or 0))} oz on the day{' — this report is stale' if c.get('stale') else ''}.")
    pr = premium_view()
    items = pr["items"]
    if items:
        def prem(name_part, metal):
            for i in items:
                if i["metal"] == metal and name_part in (i["product"] or "") and i.get("premium_pct_vs_spot") is not None:
                    return i
            return None
        ase, age = prem("Silver Eagle 1 oz", "silver"), prem("Gold Eagle 1 oz", "gold")
        bits = []
        if age:
            bits.append(f"a 1 oz Gold Eagle costs about {age['premium_pct_vs_spot']:.1f}% over spot")
        if ase:
            bits.append(f"a Silver Eagle about {ase['premium_pct_vs_spot']:.1f}% over spot")
        if bits:
            out.append("Dealer premiums: " + " and ".join(bits) + ". High or rising premiums mean retail demand is outrunning dealer stock; they do not move the futures price.")
    out.append("Inventory moves alone do not prove a shortage; read them with delivery activity and the curve.")
    return out


def news() -> list[str]:
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    rows = db.q("SELECT kind, metals, topics, cluster_id, publisher FROM articles WHERE COALESCE(published_at, ingested_at) >= ?", (since,))
    if not rows:
        return ["No items collected in the last 24 hours."]
    import json, collections
    clusters = {r["cluster_id"] or i for i, r in enumerate(rows)}
    official = sum(1 for r in rows if r["kind"] == "official")
    topics = collections.Counter()
    metals = collections.Counter()
    for r in rows:
        for t in json.loads(r["topics"] or "[]"):
            topics[t] += 1
        for m in json.loads(r["metals"] or "[]"):
            metals[m] += 1
    top_t = [t for t, _ in topics.most_common(3) if t not in ("gold", "silver", "markets", "commodities")]
    out = [f"{len(rows)} items came in over the last 24 hours, boiling down to {len(clusters)} distinct stories; {official} were official releases (Fed, CFTC, BEA, central banks)."]
    if metals:
        out.append(f"{metals.get('gold', 0)} items mention gold and {metals.get('silver', 0)} mention silver.")
    if top_t:
        out.append("The themes coming up most: " + ", ".join(top_t) + ".")
    top = db.q("SELECT title, publisher FROM articles WHERE COALESCE(published_at, ingested_at) >= ? AND kind IN ('official','reporting','research') ORDER BY score DESC LIMIT 2", (since,))
    if top:
        out.append("Highest-ranked right now: " + " / ".join(f"\"{t['title']}\" ({t['publisher']})" for t in top) + ".")
    out.append("Items marked 'headline only' were ranked from the headline alone; opinion and forecast pieces are labelled and ranked lower.")
    return out


def events() -> list[str]:
    from .calendar_view import upcoming
    e = upcoming(14, 0)["events"]
    big = [x for x in e if x["importance"] >= 3 and not x["is_past"]]
    out = []
    if big:
        out.append("Big scheduled items in the next two weeks: " + "; ".join(f"{x['title']} on {x['display_time']}" for x in big[:4]) + ".")
    else:
        out.append("No high-importance releases (CPI, jobs, PCE, FOMC) in the next two weeks.")
    cot = [x for x in e if x["category"] == "cot" and not x["is_past"]]
    if cot:
        out.append(f"The next positioning report lands {cot[0]['display_time']}.")
    out.append("No consensus numbers are shown because there is no free licensed source; dates come from the official schedules.")
    return out


def fundamentals() -> list[str]:
    from .fundamentals import fundamentals_view
    f = fundamentals_view()
    out = []
    def get(metal, metric):
        for x in f[metal]:
            if x["metric"] == metric and x.get("available"):
                return x
        return None
    cb = get("gold", "gold_cb_net_purchases_t")
    if cb:
        l, p = cb["latest"], cb.get("previous")
        out.append(f"Central banks bought a net {l['value']:,.0f} tonnes of gold in {l['period']}" + (f", versus {p['value']:,.0f} in {p['period']}" if p else "") + f" (published {l['published_at']}).")
    inv = get("gold", "gold_investment_demand_t")
    if inv:
        l = inv["latest"]
        out.append(f"Gold investment demand (bars, coins, ETFs) was {l['value']:,.0f} tonnes in {l['period']}.")
    ind = get("silver", "silver_industrial_demand_moz")
    bal = get("silver", "silver_market_balance_moz")
    if ind:
        l = ind["latest"]
        per = f"{l['period'].rstrip('F')} (forecast)" if l["period"].endswith("F") else l["period"]
        out.append(f"Silver industrial use was {l['value']:,.0f} million ounces in {per}, roughly half of all silver demand.")
    if bal:
        l = bal["latest"]
        out.append(f"The silver market is projected to be in {'deficit' if l['value'] < 0 else 'surplus'} by {abs(l['value']):,.0f} million ounces for {l['period'].rstrip('F')} (a forecast).")
    out.append("These are yearly and quarterly figures from industry bodies. They describe the backdrop and never explain a single day's move.")
    return out


def research() -> list[str]:
    from .research import research_view
    r = research_view()
    out = []
    h1 = r["h1_gold_vs_real_yield"]
    if h1.get("available"):
        m = h1["regime_means"]
        latest = (h1.get("latest") or {}).get("v")
        strength = "strong" if latest is not None and latest < -0.3 else "moderate" if latest is not None and latest < -0.15 else "weak"
        out.append(f"Gold vs real yields: the relationship is {strength} right now (60-day correlation {latest:+.2f}). It averaged {m['2008-2021']:+.2f} in 2008–2021, {m['2022-2023']:+.2f} in 2022–23 and {m['2024-now']:+.2f} since 2024, so the claim that it broke down after 2022 does not hold on this data; it weakened later, in 2024.")
    h2 = r["h2_gold_vs_dxy"]
    if h2.get("available"):
        latest = (h2.get("latest") or {}).get("v")
        base = h2["regime_means"].get("2008-2021")
        cmp_ = "" if latest is None or base is None else (", stronger than the 2008–2021 average" if abs(latest) > abs(base) * 1.2 else ", weaker than the 2008–2021 average" if abs(latest) < abs(base) * 0.8 else ", in line with the 2008–2021 average")
        if latest is not None:
            out.append(f"Gold vs the dollar: a 1% dollar rise has lately gone with a {abs(latest):.1f}% gold move {'the other way' if latest < 0 else 'in the same direction'}{cmp_}.")
    h3 = r["h3_silver_fair_value"]
    if h3.get("available"):
        hr = h3["out_of_sample"].get("hit_rate_thr10")
        out.append(f"The silver fair-value indicator explained only {h3['in_sample']['adj_r2'] * 100:.0f}% of daily moves in its fitting window, but has called the direction of the next 15 sessions right {hr * 100:.0f}% of the time since 2022 on {h3['out_of_sample']['signals_thr10']} overlapping signals. Overlap inflates that count, so treat it as a lead worth testing, not a result.")
    out.append("All of this is descriptive: it measures how things have moved together, not why, and none of it forecasts.")
    return out


def status() -> list[str]:
    jobs = db.job_status()
    ok = [j for j in jobs if j["ok"]]
    bad = [j for j in jobs if not j["ok"]]
    out = [f"{len(ok)} of {len(jobs)} collectors succeeded on their last run."]
    if bad:
        out.append("Failing: " + ", ".join(f"{j['job']} ({(j['message'] or '')[:60]})" for j in bad) + ".")
    n_series = db.q1("SELECT COUNT(*) c FROM series")["c"]
    n_obs = db.q1("SELECT COUNT(*) c FROM observations")["c"]
    n_art = db.q1("SELECT COUNT(*) c FROM articles")["c"]
    out.append(f"The database holds {n_series} series, {n_obs:,} observations and {n_art:,} news items.")
    gaps = []
    if not db.latest("dfii10"):
        gaps.append("FRED is unreachable (a free API key fixes this), so yields come from the Treasury's own files")
    if not db.q1("SELECT 1 AS x FROM comex_stocks LIMIT 1"):
        gaps.append("no COMEX warehouse file has been dropped in yet")
    if not db.latest("gld_oz"):
        gaps.append("GLD holdings are not collected")
    if gaps:
        out.append("Known gaps: " + "; ".join(gaps) + ".")
    return out


SNAPSHOTS = {"overview": overview, "today": today, "drivers": drivers, "positioning": positioning, "physical": physical,
             "news": news, "events": events, "fundamentals": fundamentals, "research": research, "status": status}


def snapshot(tab: str) -> dict:
    fn = SNAPSHOTS.get(tab)
    if not fn:
        return {"tab": tab, "available": False, "sentences": []}
    try:
        return {"tab": tab, "available": True, "sentences": fn(), "generated_at": db.utcnow()}
    except Exception as e:  # noqa: BLE001
        return {"tab": tab, "available": False, "sentences": [f"Could not build the summary: {type(e).__name__}: {e}"]}

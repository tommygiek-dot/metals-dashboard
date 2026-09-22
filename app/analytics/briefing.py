"""Daily briefing: deterministic, evidence-linked, archived, diffed against the previous briefing.

Structure (per the plan): observed facts → top developments → separate gold/silver interpretations →
evidence → conflicting signals → unknowns → upcoming → diff vs previous. Calibrated language only:
"consistent with", "may partly explain", "no supported explanation". Every claim carries a source series or
article id. An optional LLM narrative layer is a paid-service decision left to Tom; nothing here calls one.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import db
from ..config import DISPLAY_TZ, ANALYTICS_VERSION
from .changes import period_changes, ratio_context, realized_vol, is_globex_metals_open
from .drivers import driver_board
from .news_rank import list_news
from .calendar_view import upcoming


def _fmt_pct(p):
    return f"{p:+.2f}%" if p is not None else "n/a"


def _lvl(v, nd=2):
    return f"{v:,.{nd}f}" if v is not None else "n/a"


def generate(persist: bool = True) -> dict:
    now = datetime.now(timezone.utc)
    local_date = now.astimezone(ZoneInfo(DISPLAY_TZ)).date().isoformat()
    board = driver_board(persist=False)
    drivers = board["drivers"]
    facts, evidence = [], []
    metals_txt = {}
    for metal, sid in (("gold", "gold_fut_cont"), ("silver", "silver_fut_cont")):
        pc = period_changes(sid)
        if not pc.get("available"):
            metals_txt[metal] = {"summary": "No price data.", "facts": []}
            continue
        c1 = pc["changes"].get("1d") or {}
        c1w = pc["changes"].get("1w") or {}
        vol = realized_vol(sid)
        contract = (pc.get("meta") or {}).get("contract")
        f = (f"{metal.title()} front-month futures ({contract or 'GC=F/SI=F'}) last {_lvl(pc['last'])} $/oz on {pc['last_ts']}, "
             f"{_fmt_pct(c1.get('pct'))} vs the prior close ({_lvl(c1.get('base'))} on {c1.get('base_ts')}); {_fmt_pct(c1w.get('pct'))} over 1 week; "
             f"{_fmt_pct((pc['changes'].get('ytd') or {}).get('pct'))} YTD.")
        facts.append({"text": f, "source": f"series:{sid}", "kind": "observed"})
        move = c1.get("pct")
        size = "flat" if move is None or abs(move) < 0.3 else ("modest" if abs(move) < 1.0 else ("notable" if abs(move) < 2.5 else "large"))
        v21 = vol.get("21d")
        sigma_note = ""
        if v21 and move is not None:
            daily_sigma = v21 / (252 ** 0.5)
            z = move / daily_sigma if daily_sigma else None
            if z is not None:
                sigma_note = f" ({abs(z):.1f}σ of the 21-day realized daily volatility)"
        metals_txt[metal] = {"move_pct": move, "size": size, "sigma_note": sigma_note, "last": pc["last"], "last_ts": pc["last_ts"], "contract": contract, "vol": vol}
    rc = ratio_context()
    if rc.get("available"):
        facts.append({"text": f"Gold/silver ratio {rc['value']:.1f} (prior {rc['prev']:.1f} if available); {rc['percentile_5y']:.0f}th percentile of the last 5 years.",
                      "source": "derived:ratio", "kind": "observed"})

    # candidate explanations: catalysts first, then conditions with a non-mixed stance
    def stance(d, metal):
        return d.get(f"stance_{metal}")
    interpretations = {}
    conflicts = []
    for metal in ("gold", "silver"):
        mv = metals_txt[metal].get("move_pct")
        direction = None if mv is None or abs(mv) < 0.3 else ("up" if mv > 0 else "down")
        aligned, opposed, context = [], [], []
        for d in drivers:
            s = stance(d, metal)
            if s in (None, "insufficient"):
                continue
            item = {"driver": d["name"], "id": d["id"], "stance": s, "kind": d["kind"], "delta_1d": d.get("delta_1d"), "delta_1w": d.get("delta_1w"), "unit": d.get("unit"),
                    "evidence_strength": (d.get("evidence") or {}).get("strength"), "freshness": d.get("freshness"), "mechanism": d.get("mechanism")}
            slow = d["id"].startswith(("cot_", "etf_", "curve_", "fed_balance", "broad_dollar", "policy_rate"))
            if d["kind"] == "background" or d.get("freshness") == "stale" or slow:
                # weekly / lagged / structural series describe the backdrop; they never explain a same-day move
                context.append(item); continue
            if direction is None:
                context.append(item); continue
            if (s == "support" and direction == "up") or (s == "pressure" and direction == "down"):
                aligned.append(item)
            elif s in ("support", "pressure"):
                opposed.append(item)
            else:
                context.append(item)
        aligned.sort(key=lambda x: (x["kind"] != "catalyst", {"strong": 0, "moderate": 1, "weak": 2}.get(x["evidence_strength"], 3)))
        if direction is None:
            text = f"{metal.title()} is little changed ({_fmt_pct(mv)}); no explanation is needed for a move inside normal daily noise."
        elif aligned:
            names = ", ".join(a["driver"] for a in aligned[:3])
            strong = [a for a in aligned if a["evidence_strength"] == "strong" and a["kind"] == "catalyst"]
            verb = "is consistent with" if strong else "may partly reflect"
            text = f"{metal.title()}'s {metals_txt[metal]['size']} move {direction} ({_fmt_pct(mv)}{metals_txt[metal]['sigma_note']}) {verb} {names}."
            if opposed:
                text += f" Working against it: {', '.join(o['driver'] for o in opposed[:2])}."
        else:
            text = (f"{metal.title()} moved {direction} ({_fmt_pct(mv)}{metals_txt[metal]['sigma_note']}) with no tracked driver pointing the same way — "
                    f"no supported explanation from the data collected here; check the news clusters and treat any single-story narrative with caution.")
        interpretations[metal] = {"text": text, "aligned": aligned, "opposed": opposed, "context": context[:6], "direction": direction}
        for o in opposed:
            conflicts.append({"metal": metal, "text": f"{o['driver']} leans {o['stance']} while {metal} moved {direction}.", "driver_id": o["id"]})
    # crowded positioning conflicts
    for d in drivers:
        ex = d.get("extra") or {}
        if d["id"].startswith("cot_") and ex.get("percentile_3y") is not None and (ex["percentile_3y"] >= 85 or ex["percentile_3y"] <= 15):
            conflicts.append({"metal": d["id"].split("_")[1], "text": f"Managed-money net position is at the {ex['percentile_3y']:.0f}th percentile of 3 years (as of {d['observation']['last_ts']}): crowded positioning cuts both ways.", "driver_id": d["id"]})

    # news: top clusters last 36h
    since = (now - timedelta(hours=36)).isoformat(timespec="seconds")
    nw = list_news(since=since, limit=40)
    top = []
    for it in nw["items"][:12]:
        if it.get("kind") in ("opinion", "forecast"):
            continue
        top.append({"title": it["title"], "publisher": it["publisher"], "url": it["url"], "published_at": it["published_at"], "kind": it["kind"],
                    "metals": it["metals"], "topics": it["topics"], "cluster_size": it.get("cluster_size"), "independent_publishers": it.get("independent_publishers"),
                    "headline_only": bool(it.get("headline_only")), "id": it["id"]})
        if len(top) >= 5:
            break
    developments = [{"rank": i + 1, "text": t["title"], "source": f"article:{t['id']}", "publisher": t["publisher"], "url": t["url"], "kind": t["kind"],
                     "independent_publishers": t["independent_publishers"], "note": "headline only — body not accessed" if t["headline_only"] else None} for i, t in enumerate(top)]
    for d in drivers:
        if d["kind"] == "catalyst":
            developments.append({"rank": len(developments) + 1, "text": f"{d['name']}: {d.get('delta_1d'):+.2f} {d.get('unit')} today" if d.get("delta_1d") is not None else d["name"],
                                 "source": f"series:{d['observation'].get('series_id')}", "kind": "data", "publisher": d["observation"].get("source")})
    developments = developments[:5]
    for t in top:
        evidence.append({"claim": t["title"], "source_url": t["url"], "publisher": t["publisher"], "independent_publishers": t["independent_publishers"], "label": "reported" if not t["headline_only"] else "headline only"})
    for d in drivers:
        if d["kind"] == "catalyst":
            evidence.append({"claim": f"{d['name']} changed {d.get('delta_1d')} {d.get('unit')} on {d['observation'].get('last_ts')}", "source_url": None, "publisher": d["observation"].get("source"), "label": "observed"})

    unknowns = []
    if not db.latest("gld_oz"):
        unknowns.append("GLD holdings not collected (issuer data URL changed); gold ETF read relies on IAU only.")
    if not db.q1("SELECT 1 AS x FROM comex_stocks LIMIT 1"):
        unknowns.append("No COMEX warehouse report ingested yet (manual drop required).")
    if not db.latest("dfii10"):
        unknowns.append("FRED unreachable; real yields and breakevens come from Treasury par curves instead of H.15 constant-maturity series.")
    unknowns.append("No market-implied Fed path (no free reliable source); policy expectations are read from official communications.")
    unknowns.append("No options-implied volatility or skew (no free source); realized volatility only.")

    ev = upcoming(14, 0)["events"][:8]
    body = {"date": local_date, "tz": DISPLAY_TZ, "generated_at": now.isoformat(timespec="seconds"), "session": is_globex_metals_open(now),
            "facts": facts, "developments": developments, "interpretations": interpretations, "evidence": evidence, "conflicts": conflicts,
            "unknowns": unknowns, "upcoming": [{"title": e["title"], "when": e["display_time"], "category": e["category"], "importance": e["importance"]} for e in ev],
            "metals": metals_txt, "ratio": rc, "language_note": "Calibrated: 'consistent with' = strong, active relationship and a same-day move; 'may partly reflect' = weaker or unstable relationship; 'no supported explanation' = nothing tracked lines up."}
    prev = _previous(local_date)
    body["diff"] = _diff(prev["body"] if prev else None, body)
    body["previous_date"] = prev["date"] if prev else None
    inputs_hash = hashlib.sha256(json.dumps({"facts": facts, "dev": [d["text"] for d in developments], "drv": [(d["id"], d.get("delta_1d")) for d in drivers]}, default=str).encode()).hexdigest()[:16]
    body["inputs_hash"] = inputs_hash
    body["analytics_version"] = ANALYTICS_VERSION
    if persist:
        last = db.q1("SELECT inputs_hash FROM briefings WHERE date=? ORDER BY generated_at DESC LIMIT 1", (local_date,))
        if not last or last["inputs_hash"] != inputs_hash:
            with db.tx() as c:
                c.execute("INSERT OR REPLACE INTO briefings(date,generated_at,cutoff_at,tz,inputs_hash,analytics_version,body) VALUES(?,?,?,?,?,?,?)",
                          (local_date, body["generated_at"], body["generated_at"], DISPLAY_TZ, inputs_hash, ANALYTICS_VERSION, json.dumps(body, default=str)))
    return body


def _previous(date_: str) -> dict | None:
    r = db.q1("SELECT date, body FROM briefings WHERE date < ? ORDER BY date DESC, generated_at DESC LIMIT 1", (date_,))
    return {"date": r["date"], "body": json.loads(r["body"])} if r else None


def _diff(prev: dict | None, cur: dict) -> dict:
    if not prev:
        return {"new": [d["text"] for d in cur["developments"]], "changed": [], "unresolved": cur["unknowns"], "note": "no previous briefing"}
    pd_ = {d["text"] for d in prev.get("developments", [])}
    new = [d["text"] for d in cur["developments"] if d["text"] not in pd_]
    changed = []
    for m in ("gold", "silver"):
        a, b = (prev.get("interpretations", {}).get(m) or {}).get("direction"), (cur["interpretations"].get(m) or {}).get("direction")
        if a != b:
            changed.append(f"{m}: direction {a or 'flat'} → {b or 'flat'}")
    pconf = {c["text"] for c in prev.get("conflicts", [])}
    for c in cur["conflicts"]:
        if c["text"] not in pconf:
            changed.append(f"new conflict: {c['text']}")
    unresolved = [u for u in cur["unknowns"] if u in set(prev.get("unknowns", []))]
    return {"new": new, "changed": changed, "unresolved": unresolved, "resolved": [u for u in prev.get("unknowns", []) if u not in set(cur["unknowns"])]}


def get_briefing(date_: str | None = None) -> dict:
    if date_:
        r = db.q1("SELECT body FROM briefings WHERE date=? ORDER BY generated_at DESC LIMIT 1", (date_,))
        if not r:
            return {"available": False, "date": date_}
        return {"available": True, **json.loads(r["body"])}
    r = db.q1("SELECT body FROM briefings ORDER BY date DESC, generated_at DESC LIMIT 1")
    if r:
        return {"available": True, **json.loads(r["body"])}
    return {"available": True, **generate(persist=True)}


def list_briefings() -> dict:
    return {"briefings": db.q("SELECT date, MAX(generated_at) generated_at, COUNT(*) versions FROM briefings GROUP BY date ORDER BY date DESC LIMIT 400")}

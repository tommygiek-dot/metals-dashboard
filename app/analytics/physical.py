"""Physical-market views: COMEX warehouse stocks (manual-drop pipeline) and dealer premiums."""
from __future__ import annotations
from datetime import datetime, timezone, date

from .. import db


def physical_view() -> dict:
    out = {"comex": {}, "definitions": {
        "registered": "Metal in an approved depository with a warrant attached; deliverable against COMEX futures.",
        "eligible": "Metal meeting exchange specs stored in an approved depository without a warrant; not deliverable until a warrant is issued (can be converted quickly).",
        "caveat": "Inventory changes, category transfers and OI/inventory ratios are not by themselves evidence of shortage, manipulation or delivery failure. Use with delivery notices and spreads.",
        "pipeline": "cmegroup.com blocks scripted downloads from this machine; drop Gold_Stocks.xls / Silver_stocks.xls into data/inbox/ and they are parsed within 15 minutes."}}
    today = date.today()
    for metal in ("gold", "silver"):
        dates = db.q("SELECT DISTINCT report_date FROM comex_stocks WHERE metal=? ORDER BY report_date DESC LIMIT 400", (metal,))
        if not dates:
            out["comex"][metal] = {"available": False, "status": "no reports ingested yet"}
            continue
        latest = dates[0]["report_date"]
        age = (today - date.fromisoformat(latest)).days
        rows = db.q("SELECT * FROM comex_stocks WHERE metal=? AND report_date=? ORDER BY depository", (metal, latest))
        total = next((r for r in rows if r["depository"] == "TOTAL"), None)
        hist = db.q("SELECT report_date, registered, eligible, total FROM comex_stocks WHERE metal=? AND depository='TOTAL' ORDER BY report_date", (metal,))
        oi = db.latest(f"{metal}_front_oi")
        oz_per_contract = 100 if metal == "gold" else 5000
        cover = None
        if total and total.get("registered") and oi and oi.get("value"):
            cover = (oi["value"] * oz_per_contract) / total["registered"]
        out["comex"][metal] = {"available": True, "report_date": latest, "age_days": age,
                               "stale": age > 3, "status": "stale — drop a newer report in data/inbox/" if age > 3 else "current",
                               "total": total, "depositories": [r for r in rows if r["depository"] != "TOTAL"],
                               "history": hist, "front_oi_contracts": (oi or {}).get("value"),
                               "front_oi_oz_over_registered": cover,
                               "cover_note": "Front-month open interest × contract size ÷ registered ounces. Context only: most OI never goes to delivery."}
    return out


def premium_view() -> dict:
    spot = {"gold": db.latest("stak_spot_gold"), "silver": db.latest("stak_spot_silver")}
    fut = {"gold": db.latest("gold_fut_cont"), "silver": db.latest("silver_fut_cont")}
    items = []
    for s in db.q("SELECT id, name FROM series WHERE id LIKE 'retail_%' ORDER BY id"):
        last = db.latest(s["id"])
        if not last:
            continue
        m = last.get("meta") or {}
        metal, oz = m.get("metal"), m.get("oz") or 1
        ref = (spot.get(metal) or {}).get("value") or (fut.get(metal) or {}).get("value")
        per_oz = last["value"] / oz if oz else None
        prem = ((per_oz / ref - 1) * 100) if (ref and per_oz) else None
        vendors = m.get("vendors") or {}
        vlist = []
        if isinstance(vendors, dict):
            for name, v in vendors.items():
                price = v.get("price") if isinstance(v, dict) else v
                if price is None:
                    continue
                vlist.append({"vendor": name, "price": price, "in_stock": (v.get("inStock") if isinstance(v, dict) else None),
                              "premium_pct": ((price / oz) / ref - 1) * 100 if ref else None})
        vlist.sort(key=lambda x: x["price"])
        items.append({"product": m.get("name") or s["name"], "metal": metal, "oz": oz, "median": last["value"], "low": m.get("low"), "high": m.get("high"),
                      "per_oz": per_oz, "premium_pct_vs_spot": prem, "reference_spot": ref, "ts": last["ts"], "vendors": vlist})
    return {"items": items, "spot": spot,
            "notes": "Premium = (median dealer ask per ounce ÷ aggregated spot − 1). Asks only (no bid), single-unit quantity, availability as reported by the feed. Product size, dealer, quantity breaks and buyback spreads all move the number; compare like with like."}

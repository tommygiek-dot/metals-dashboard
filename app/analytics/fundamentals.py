"""Annual / quarterly fundamentals (central-bank buying, jewelry, industrial demand, mine supply, recycling,
supply-demand balances). Loaded from data/seeds/fundamentals_seed.json (each entry carries its source URL and
publication date) into the `fundamentals` table. Always displayed with the period and publication date and
kept visually separate from daily catalysts: a structural statistic never explains today's move."""
from __future__ import annotations
import json
from datetime import date

from .. import db
from ..config import DATA

SEED = DATA / "seeds" / "fundamentals_seed.json"


def load_seed() -> int:
    if not SEED.exists():
        return 0
    rows = json.loads(SEED.read_text(encoding="utf-8"))
    n = 0
    with db.tx() as c:
        for r in rows:
            c.execute("""INSERT INTO fundamentals(metric,period,value,unit,source,source_url,published_at,notes) VALUES(?,?,?,?,?,?,?,?)
                         ON CONFLICT(metric,period) DO UPDATE SET value=excluded.value, unit=excluded.unit, source=excluded.source,
                         source_url=excluded.source_url, published_at=excluded.published_at, notes=excluded.notes""",
                      (r["metric"], r["period"], r.get("value"), r.get("unit"), r.get("source"), r.get("source_url"), r.get("published_at"), r.get("notes")))
            n += 1
    return n


GROUPS = {
    "gold": [("gold_cb_net_purchases_t", "Central-bank net purchases"), ("gold_jewelry_demand_t", "Jewellery fabrication demand"),
             ("gold_investment_demand_t", "Investment demand (bar, coin, ETF)"), ("gold_mine_supply_t", "Mine production"),
             ("gold_recycling_t", "Recycled gold supply"), ("gold_total_demand_t", "Total demand"), ("gold_total_supply_t", "Total supply")],
    "silver": [("silver_industrial_demand_moz", "Industrial demand"), ("silver_solar_demand_moz", "Photovoltaic (solar) demand"),
               ("silver_electronics_demand_moz", "Electrical & electronics demand"), ("silver_jewelry_demand_moz", "Jewellery demand"),
               ("silver_investment_demand_moz", "Physical investment (bar & coin)"), ("silver_mine_supply_moz", "Mine production"),
               ("silver_recycling_moz", "Recycling supply"), ("silver_total_supply_moz", "Total supply"), ("silver_total_demand_moz", "Total demand"),
               ("silver_market_balance_moz", "Market balance (supply − demand)")],
}


def fundamentals_view() -> dict:
    load_seed()
    rows = db.q("SELECT * FROM fundamentals ORDER BY metric, period")
    by_metric: dict[str, list] = {}
    for r in rows:
        by_metric.setdefault(r["metric"], []).append(r)
    out = {"gold": [], "silver": [], "notes": "Annual/quarterly figures from industry bodies (World Gold Council / Metals Focus, Silver Institute). "
                                              "They describe structural conditions and are shown with period and publication date. They are NOT daily catalysts."}
    for metal, items in GROUPS.items():
        for metric, label in items:
            series = by_metric.get(metric, [])
            if not series:
                out[metal].append({"metric": metric, "label": label, "available": False})
                continue
            latest = series[-1]
            prev = series[-2] if len(series) > 1 else None
            age = None
            if latest.get("published_at"):
                try:
                    age = (date.today() - date.fromisoformat(latest["published_at"][:10])).days
                except ValueError:
                    pass
            out[metal].append({"metric": metric, "label": label, "available": True, "latest": latest, "previous": prev,
                               "change": (latest["value"] - prev["value"]) if prev and latest.get("value") is not None and prev.get("value") is not None else None,
                               "published_age_days": age, "history": series})
    return out

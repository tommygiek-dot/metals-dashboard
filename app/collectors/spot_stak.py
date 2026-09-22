"""StakTrakr free feed: aggregated spot (~20-min cadence) and dealer retail prices for common products.

Single-developer endpoint with no published terms: polled no faster than its own `stale_after`, labelled
"aggregated spot (StakTrakr)" in the UI, never merged with futures history. Fallible adapter.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

import requests

from .. import db
from ..config import USER_AGENT

log = logging.getLogger(__name__)
PARSER_VERSION = "1"
BASE = "https://api.staktrakr.com/data/v2"

# product slug -> (name, metal, ounces). Slugs observed in the StakTrakr repo; unknown slugs are skipped.
PRODUCTS = {
    "ase": ("American Silver Eagle 1 oz", "silver", 1.0),
    "maple-silver": ("Canadian Silver Maple 1 oz", "silver", 1.0),
    "silver-bar-10oz": ("Silver bar 10 oz", "silver", 10.0),
    "silver-bar-100oz": ("Silver bar 100 oz", "silver", 100.0),
    "silver-round-1oz": ("Generic silver round 1 oz", "silver", 1.0),
    "age": ("American Gold Eagle 1 oz", "gold", 1.0),
    "agb": ("American Gold Buffalo 1 oz", "gold", 1.0),
    "maple-gold": ("Canadian Gold Maple 1 oz", "gold", 1.0),
    "gold-bar-1oz": ("Gold bar 1 oz", "gold", 1.0),
    "krugerrand": ("Krugerrand 1 oz", "gold", 1.0),
}


def _get(path: str) -> dict | list | None:
    url = f"{BASE}/{path}"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    db.archive_raw("staktrakr", url, r.content, "json", PARSER_VERSION)
    return r.json()


def update_spot() -> int:
    d = _get("spot/latest.json")
    if not d:
        return 0
    data = d.get("data", {})
    n = 0
    for key, sid, name in (("xau", "stak_spot_gold", "Gold spot, aggregated (StakTrakr)"),
                           ("xag", "stak_spot_silver", "Silver spot, aggregated (StakTrakr)"),
                           ("xpt", "stak_spot_platinum", "Platinum spot, aggregated (StakTrakr)"),
                           ("xpd", "stak_spot_palladium", "Palladium spot, aggregated (StakTrakr)")):
        row = data.get(key)
        if not row or row.get("price") is None:
            continue
        db.ensure_series(sid, name, "spot_aggregated", "USD/oz", "StakTrakr api v2", "intraday",
                         "Third-party aggregated spot; ~20-min cadence; no published terms. Not a benchmark.")
        ts = row.get("t") or datetime.now(timezone.utc).isoformat(timespec="seconds")
        n += db.upsert_observations(sid, [(ts, float(row["price"]), {"generated_at": d.get("generated_at"), "stale_after_s": d.get("stale_after")})])
        # also keep a daily close-style series for overview comparisons (last print of the UTC day)
        day = ts[:10]
        db.ensure_series(sid + "_daily", name + " (last of day)", "spot_aggregated", "USD/oz", "StakTrakr api v2", "daily", "Last aggregated print of each UTC day.")
        n += db.upsert_observations(sid + "_daily", [(day, float(row["price"]), {"t": ts})])
    return n


MAX_DETAIL = 14   # per-slug vendor detail requests per run (politeness)


def update_retail() -> int:
    """retail/latest.json lists every tracked product (median/low/high/vendor_count); the per-slug file adds
    per-dealer asks. One index request + up to MAX_DETAIL detail requests per run."""
    idx = _get("retail/latest.json")
    if not idx or not idx.get("data"):
        return 0
    coins = idx["data"].get("coins") or {}
    ts_default = idx["data"].get("t") or datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    detail_left = MAX_DETAIL
    # prefer the common 1 oz gold/silver products first, then the rest
    order = sorted(coins.items(), key=lambda kv: (0 if kv[0] in PRODUCTS else 1, kv[0]))
    for slug, c in order:
        med = c.get("median")
        if med is None:
            continue
        name = c.get("name") or slug
        metal = {"xau": "gold", "xag": "silver", "xpt": "platinum", "xpd": "palladium"}.get(c.get("metal"), c.get("metal"))
        oz = float(c.get("weight_oz") or 1)
        vendors = {}
        if detail_left > 0:
            try:
                d = _get(f"retail/{slug}/latest.json")
                if d and d.get("data"):
                    vendors = d["data"].get("vendors") or {}
                detail_left -= 1
            except Exception as e:  # noqa: BLE001
                log.warning("retail detail %s failed: %s", slug, e)
        sid = f"retail_{slug}"
        db.ensure_series(sid, f"{name} — dealer median ask", "premium", "USD/each", "StakTrakr api v2 retail", "intraday",
                         f"Median of dealer asks for {name}; per-dealer asks in meta when fetched. ~30-min cadence.")
        ts = c.get("t") or ts_default
        meta = {"metal": metal, "oz": oz, "name": name, "low": c.get("low"), "high": c.get("high"), "vendor_count": c.get("vendor_count"),
                "vendors": {k: {"price": v.get("price"), "inStock": v.get("in_stock")} for k, v in vendors.items() if isinstance(v, dict)},
                "generated_at": idx.get("generated_at")}
        n += db.upsert_observations(sid, [(ts, float(med), meta)])
    return n

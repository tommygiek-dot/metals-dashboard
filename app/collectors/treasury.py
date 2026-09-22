"""US Treasury daily par yield curves (nominal and real) from home.treasury.gov CSV downloads.
Official source; used as the yield-curve view and as a fallback when FRED is unreachable."""
from __future__ import annotations
import csv
import io
import logging
from datetime import datetime

import requests

from .. import db
from ..config import BROWSER_UA

log = logging.getLogger(__name__)
PARSER_VERSION = "1"
BASE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all"

NOMINAL = {"1 Mo": "ust_1m", "3 Mo": "ust_3m", "6 Mo": "ust_6m", "1 Yr": "ust_1y", "2 Yr": "ust_2y", "5 Yr": "ust_5y",
           "7 Yr": "ust_7y", "10 Yr": "ust_10y", "20 Yr": "ust_20y", "30 Yr": "ust_30y"}
REAL = {"5 YR": "ust_real_5y", "7 YR": "ust_real_7y", "10 YR": "ust_real_10y", "20 YR": "ust_real_20y", "30 YR": "ust_real_30y"}


def _fetch(year: int, kind: str) -> list[dict]:
    url = BASE.format(year=year) + f"?type={kind}&field_tdr_date_value={year}&page&_format=csv"
    r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=60)
    r.raise_for_status()
    db.archive_raw("treasury_" + kind, url, r.content, "csv", PARSER_VERSION)
    return list(csv.DictReader(io.StringIO(r.text)))


def update(years: list[int] | None = None) -> int:
    years = years or [datetime.utcnow().year]
    n = 0
    for col, sid in NOMINAL.items():
        db.ensure_series(sid, f"US Treasury par yield {col}", "macro", "pct", "US Treasury daily par yield curve", "daily", "Official Treasury CSV.")
    for col, sid in REAL.items():
        db.ensure_series(sid, f"US Treasury real (TIPS) par yield {col.lower()}", "macro", "pct", "US Treasury daily real yield curve", "daily", "Official Treasury CSV.")
    for y in years:
        for kind, cols in (("daily_treasury_yield_curve", NOMINAL), ("daily_treasury_real_yield_curve", REAL)):
            try:
                rows = _fetch(y, kind)
            except Exception as e:  # noqa: BLE001
                log.warning("treasury %s %s failed: %s", y, kind, e)
                continue
            per: dict[str, list] = {sid: [] for sid in cols.values()}
            for row in rows:
                try:
                    d = datetime.strptime(row["Date"], "%m/%d/%Y").strftime("%Y-%m-%d")
                except Exception:  # noqa: BLE001
                    continue
                for col, sid in cols.items():
                    v = (row.get(col) or "").strip()
                    if v:
                        try:
                            per[sid].append((d, float(v), None))
                        except ValueError:
                            pass
            for sid, rs in per.items():
                n += db.upsert_observations(sid, rs)
    n += derive_breakevens()
    return n


def derive_breakevens() -> int:
    """Breakeven inflation (nominal par − real par) for 5y/10y/30y from the Treasury curves. Derived series,
    used when FRED's T5YIE/T10YIE are unreachable; the two differ slightly (FRED uses constant-maturity H.15 yields)."""
    n = 0
    for tenor in ("5y", "10y", "30y"):
        sid = f"ust_be_{tenor}"
        db.ensure_series(sid, f"Breakeven inflation {tenor} (Treasury par nominal − real)", "derived", "pct",
                         "derived: US Treasury par curves", "daily", "Nominal minus TIPS par yield; includes inflation-risk and liquidity premia. Not identical to FRED T10YIE.")
        nom = {r["ts"]: r["value"] for r in db.get_series(f"ust_{tenor}")}
        real = {r["ts"]: r["value"] for r in db.get_series(f"ust_real_{tenor}")}
        rows = [(d, nom[d] - real[d], None) for d in sorted(set(nom) & set(real)) if nom[d] is not None and real[d] is not None]
        n += db.upsert_observations(sid, rows)
    return n

"""BLS public API v1 (no key, 25 requests/day). Used only for the headline release series; FRED carries the
same data with a lag. Kept small on purpose."""
from __future__ import annotations
import logging

import requests

from .. import db
from ..config import USER_AGENT

log = logging.getLogger(__name__)
PARSER_VERSION = "1"

SERIES = {
    "bls_cpi_u_nsa": ("CUUR0000SA0", "CPI-U all items, NSA (BLS)", "index"),
    "bls_cpi_u_sa": ("CUSR0000SA0", "CPI-U all items, SA (BLS)", "index"),
    "bls_core_cpi_sa": ("CUSR0000SA0L1E", "Core CPI (ex food & energy), SA (BLS)", "index"),
    "bls_unrate": ("LNS14000000", "Unemployment rate (BLS)", "pct"),
    "bls_payems": ("CES0000000001", "Total nonfarm payrolls, SA (BLS)", "thousands"),
}


def update() -> int:
    n = 0
    ids = list(SERIES)
    codes = [SERIES[s][0] for s in ids]
    try:
        r = requests.post("https://api.bls.gov/publicAPI/v1/timeseries/data/", json={"seriesid": codes},
                          headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"}, timeout=60)
        r.raise_for_status()
        db.archive_raw("bls", "https://api.bls.gov/publicAPI/v1/timeseries/data/", r.content, "json", PARSER_VERSION)
        j = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("bls failed: %s", e)
        return 0
    if j.get("status") != "REQUEST_SUCCEEDED":
        log.warning("bls: %s", j.get("message"))
        return 0
    by_code = {s["seriesID"]: s for s in j.get("Results", {}).get("series", [])}
    for sid in ids:
        code, name, unit = SERIES[sid]
        db.ensure_series(sid, name, "macro", unit, f"BLS API v1:{code}", "monthly", "Latest vintage; 25 req/day limit.")
        s = by_code.get(code)
        if not s:
            continue
        rows = []
        for d in s.get("data", []):
            if not d["period"].startswith("M"):
                continue
            ts = f"{d['year']}-{d['period'][1:]}-01"
            try:
                rows.append((ts, float(d["value"]), {"footnotes": [f.get("text") for f in d.get("footnotes", []) if f.get("text")]} or None))
            except ValueError:
                pass
        n += db.upsert_observations(sid, rows)
    return n

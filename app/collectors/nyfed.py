"""New York Fed reference rates (EFFR, SOFR) from markets.newyorkfed.org — documented, keyless JSON API.
Used as the policy-rate series when FRED is unreachable."""
from __future__ import annotations
import logging

import requests

from .. import db
from ..config import USER_AGENT

log = logging.getLogger(__name__)
PARSER_VERSION = "1"
RATES = {"nyfed_effr": ("unsecured", "effr", "Effective federal funds rate (NY Fed)"),
         "nyfed_sofr": ("secured", "sofr", "SOFR (NY Fed)")}


def update(last_n: int = 400) -> int:
    n = 0
    for sid, (kind, code, name) in RATES.items():
        url = f"https://markets.newyorkfed.org/api/rates/{kind}/{code}/last/{last_n}.json"
        db.ensure_series(sid, name, "macro", "pct", f"NY Fed markets API:{code}", "daily", "Official; published each business day ~9am ET for the prior day.")
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=40)
            r.raise_for_status()
            db.archive_raw("nyfed_" + code, url, r.content, "json", PARSER_VERSION)
            rows = []
            for o in r.json().get("refRates", []):
                if o.get("percentRate") is not None and o.get("effectiveDate"):
                    rows.append((o["effectiveDate"], float(o["percentRate"]), {"volume_bn": o.get("volumeInBillions")}))
            n += db.upsert_observations(sid, rows)
        except Exception as e:  # noqa: BLE001
            log.warning("nyfed %s failed: %s", code, e)
    return n

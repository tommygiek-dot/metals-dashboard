"""CFTC Commitments of Traders via the public Socrata API (publicreporting.cftc.gov). No key needed;
an optional app token (CFTC_APP_TOKEN) only lifts IP throttling.

Report families are stored separately and never spliced:
  legacy_fut      6dca-aqww   futures only      (commercial / non-commercial / non-reportable)
  legacy_futopt   jun7-fc8e   futures + options
  disagg_fut      72hh-3qpy   futures only      (producer/merchant, swap dealer, managed money, other reportable)
  disagg_futopt   kh3c-gbw2   futures + options
Contracts are selected by CFTC contract market code: gold 088691, silver 084691 (COMEX, 100 oz / 5,000 oz).
report_date = the Tuesday the positions are as of; release is normally Friday 15:30 ET.
"""
from __future__ import annotations
import json
import logging

import requests

from .. import db
from ..config import USER_AGENT, CFTC_APP_TOKEN

log = logging.getLogger(__name__)
PARSER_VERSION = "1"
DATASETS = {"legacy_fut": "6dca-aqww", "legacy_futopt": "jun7-fc8e", "disagg_fut": "72hh-3qpy", "disagg_futopt": "kh3c-gbw2"}
CONTRACTS = {"gold": "088691", "silver": "084691"}


def fetch(report_type: str, code: str, limit: int = 2000) -> list[dict]:
    url = f"https://publicreporting.cftc.gov/resource/{DATASETS[report_type]}.json"
    params = {"cftc_contract_market_code": code, "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": limit}
    headers = {"User-Agent": USER_AGENT}
    if CFTC_APP_TOKEN:
        headers["X-App-Token"] = CFTC_APP_TOKEN
    r = requests.get(url, params=params, headers=headers, timeout=60)
    r.raise_for_status()
    db.archive_raw("cftc_" + report_type, r.url, r.content, "json", PARSER_VERSION)
    return r.json()


def update(full: bool = False) -> int:
    n = 0
    for rt in DATASETS:
        for metal, code in CONTRACTS.items():
            have = db.q1("SELECT COUNT(*) c FROM cot WHERE report_type=? AND contract_code=?", (rt, code))["c"]
            limit = 2000 if (full or have < 100) else 8
            try:
                rows = fetch(rt, code, limit)
            except Exception as e:  # noqa: BLE001
                log.warning("cftc %s %s failed: %s", rt, metal, e)
                continue
            now = db.utcnow()
            with db.tx() as c:
                for row in rows:
                    rd = (row.get("report_date_as_yyyy_mm_dd") or "")[:10]
                    if not rd:
                        continue
                    numeric = {k: _num(v) for k, v in row.items() if _num(v) is not None}
                    numeric["_metal"] = metal
                    c.execute("""INSERT INTO cot(report_type,contract_code,report_date,market_name,data,retrieved_at)
                                 VALUES(?,?,?,?,?,?) ON CONFLICT(report_type,contract_code,report_date)
                                 DO UPDATE SET data=excluded.data, retrieved_at=excluded.retrieved_at""",
                              (rt, code, rd, row.get("market_and_exchange_names"), json.dumps(numeric), now))
                    n += 1
    return n


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

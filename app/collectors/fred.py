"""FRED macro series.

Default path: the keyless download behind every FRED chart (fredgraph.csv). It is not a documented API and
was flaky under Python HTTP stacks from this machine, so it is fetched with curl (retries, HTTP/1.1).
If FRED_API_KEY is set, the documented API is used instead (and revision vintages become available).
Values are the latest vintage; observation ts = observation period date.
"""
from __future__ import annotations
import csv
import io
import logging
import subprocess
import time

import requests

from .. import db
from ..config import FRED_API_KEY, BROWSER_UA

log = logging.getLogger(__name__)
PARSER_VERSION = "1"

SERIES = {
    # id: (fred code, name, unit, cadence, notes)
    "dfii10":   ("DFII10", "10-year TIPS real yield (constant maturity)", "pct", "daily", "Market yield on 10y inflation-indexed Treasury. Source: Fed H.15 via FRED."),
    "dfii5":    ("DFII5", "5-year TIPS real yield", "pct", "daily", ""),
    "dgs10":    ("DGS10", "10-year Treasury nominal yield", "pct", "daily", "Fed H.15 via FRED."),
    "dgs2":     ("DGS2", "2-year Treasury nominal yield", "pct", "daily", ""),
    "dgs30":    ("DGS30", "30-year Treasury nominal yield", "pct", "daily", ""),
    "t10yie":   ("T10YIE", "10-year breakeven inflation rate", "pct", "daily", "Nominal minus TIPS yield; includes inflation risk and liquidity premia."),
    "t5yie":    ("T5YIE", "5-year breakeven inflation rate", "pct", "daily", ""),
    "t5yifr":   ("T5YIFR", "5-year, 5-year forward inflation expectation", "pct", "daily", ""),
    "dtwexbgs": ("DTWEXBGS", "Trade-weighted US dollar index: broad, goods & services (Fed)", "index", "daily", "NOT the ICE DXY. Fed nominal broad index, Jan 2006 = 100."),
    "dtwexafegs": ("DTWEXAFEGS", "Trade-weighted US dollar index: advanced foreign economies (Fed)", "index", "daily", ""),
    "dff":      ("DFF", "Effective federal funds rate", "pct", "daily", ""),
    "sofr":     ("SOFR", "Secured overnight financing rate", "pct", "daily", ""),
    "vixcls":   ("VIXCLS", "CBOE VIX close (FRED copy)", "index", "daily", ""),
    "dcoilwtico": ("DCOILWTICO", "WTI crude spot, Cushing (EIA via FRED)", "USD/bbl", "daily", ""),
    "walcl":    ("WALCL", "Fed total assets (H.4.1)", "USD mn", "weekly", "Wednesday level, released Thursday."),
    "wtregen":  ("WTREGEN", "Treasury General Account at the Fed", "USD mn", "weekly", ""),
    "rrpontsyd": ("RRPONTSYD", "Fed overnight reverse repo", "USD bn", "daily", ""),
    "bamlh0a0hym2": ("BAMLH0A0HYM2", "US high-yield OAS (ICE BofA)", "pct", "daily", "Credit stress gauge."),
    "nfci":     ("NFCI", "Chicago Fed National Financial Conditions Index", "index", "weekly", ""),
    "cpiaucsl": ("CPIAUCSL", "CPI-U, all items, SA", "index", "monthly", ""),
    "cpilfesl": ("CPILFESL", "Core CPI, SA", "index", "monthly", ""),
    "pcepi":    ("PCEPI", "PCE price index", "index", "monthly", ""),
    "pcepilfe": ("PCEPILFE", "Core PCE price index", "index", "monthly", ""),
    "unrate":   ("UNRATE", "Unemployment rate", "pct", "monthly", ""),
    "payems":   ("PAYEMS", "Nonfarm payrolls (level, thousands)", "thousands", "monthly", ""),
    "gdpc1":    ("GDPC1", "Real GDP (chained 2017 $, SAAR)", "USD bn", "quarterly", ""),
    "indpro":   ("INDPRO", "Industrial production index", "index", "monthly", ""),
    "michexp":  ("MICH", "U. Michigan 1-year inflation expectations", "pct", "monthly", ""),
    "dgorder":  ("DGORDER", "Durable goods orders", "USD mn", "monthly", ""),
}


_consecutive_failures = 0


def _curl(url: str, tries: int = 2, timeout: int = 25) -> bytes:
    global _consecutive_failures
    last = None
    for i in range(tries):
        r = subprocess.run(["curl", "-sS", "-L", "--http1.1", "-m", str(timeout), "-A", BROWSER_UA, url],
                           capture_output=True)
        if r.returncode == 0 and r.stdout.strip():
            _consecutive_failures = 0
            return r.stdout
        last = r.stderr.decode(errors="replace")[:200]
        time.sleep(2 + 3 * i)
    _consecutive_failures += 1
    raise RuntimeError(f"curl failed for {url}: {last}")


def fetch_csv(codes: list[str]) -> dict[str, list[tuple[str, float | None]]]:
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + ",".join(codes)
    raw = _curl(url)
    db.archive_raw("fred", url, raw, "csv", PARSER_VERSION)
    rdr = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    out = {c: [] for c in codes}
    for row in rdr:
        d = row.get("observation_date") or row.get("DATE")
        for c in codes:
            v = (row.get(c) or "").strip()
            if v in ("", "."):
                continue
            try:
                out[c].append((d, float(v)))
            except ValueError:
                pass
    return out


def fetch_api(code: str, start: str = "1990-01-01") -> list[tuple[str, float | None]]:
    r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                     params={"series_id": code, "api_key": FRED_API_KEY, "file_type": "json", "observation_start": start},
                     headers={"User-Agent": BROWSER_UA}, timeout=60)
    r.raise_for_status()
    db.archive_raw("fred_api", r.url.replace(FRED_API_KEY, "REDACTED"), r.content, "json", PARSER_VERSION)
    out = []
    for o in r.json().get("observations", []):
        if o["value"] not in (".", ""):
            out.append((o["date"], float(o["value"])))
    return out


def update(only: list[str] | None = None) -> int:
    n = 0
    ids = only or list(SERIES)
    for sid in ids:
        code, name, unit, cad, notes = SERIES[sid]
        db.ensure_series(sid, name, "macro", unit, f"FRED:{code}", cad, notes or None)
    if FRED_API_KEY:
        for sid in ids:
            code = SERIES[sid][0]
            try:
                rows = fetch_api(code)
                n += db.upsert_observations(sid, [(d, v, None) for d, v in rows])
            except Exception as e:  # noqa: BLE001
                log.warning("FRED api %s failed: %s", code, e)
        return n
    # keyless: batches of up to 6 codes per request (keeps the CSV small and the request cheap)
    batch = 6
    failures = 0
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        codes = [SERIES[s][0] for s in chunk]
        try:
            data = fetch_csv(codes)
        except Exception as e:  # noqa: BLE001
            log.warning("FRED csv batch %s failed: %s", codes, e)
            failures += 1
            if failures >= 2:
                raise RuntimeError("FRED unreachable (2 consecutive batch timeouts); giving up this run. "
                                   "A free FRED API key in .env makes this robust.") from e
            continue
        for sid, code in zip(chunk, codes):
            rows = data.get(code, [])
            n += db.upsert_observations(sid, [(d, v, None) for d, v in rows])
        time.sleep(1.0)
    return n

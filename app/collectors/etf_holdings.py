"""Physically-backed ETF holdings (ounces in trust) from the issuers' own product pages.

Flows are reported as "implied net metal change" = Δ ounces in trust, never Δ AUM (which moves with price).
SLV and IAU pages embed the figures in page JSON; GLD's CSV download currently serves a PDF (open issue),
so GLD holdings are absent until a working URL is found. One request per fund per run; one run per ~6 hours.
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timezone

import requests

from .. import db
from ..config import BROWSER_UA

log = logging.getLogger(__name__)
PARSER_VERSION = "1"

FUNDS = {
    "slv_oz": ("SLV", "silver", "iShares Silver Trust — ounces in trust", "https://www.ishares.com/us/products/239855/ishares-silver-trust-fund"),
    "iau_oz": ("IAU", "gold", "iShares Gold Trust — ounces in trust", "https://www.ishares.com/us/products/239561/ishares-gold-trust-fund"),
}
RX_OZ = re.compile(r'Ounces in Trust&quot;,&quot;formattedValue&quot;:&quot;([\d,\.]+)')
RX_T = re.compile(r'Tonnes in Trust&quot;,&quot;formattedValue&quot;:&quot;([\d,\.]+)')
RX_SHARES = re.compile(r'Shares Outstanding&quot;,&quot;formattedValue&quot;:&quot;([\d,\.]+)')
RX_ASOF = re.compile(r'as of ([A-Z][a-z]{2} \d{1,2}, \d{4})')


def _parse(html: str) -> dict | None:
    m = RX_OZ.search(html)
    if not m:
        return None
    out = {"ounces": float(m.group(1).replace(",", ""))}
    t = RX_T.search(html)
    if t:
        out["tonnes"] = float(t.group(1).replace(",", ""))
    s = RX_SHARES.search(html)
    if s:
        out["shares"] = float(s.group(1).replace(",", ""))
    a = RX_ASOF.search(html)
    if a:
        try:
            out["as_of"] = datetime.strptime(a.group(1), "%b %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return out


def update() -> int:
    n = 0
    for sid, (tk, metal, name, url) in FUNDS.items():
        db.ensure_series(sid, name, "holdings", "ounces", f"iShares product page ({tk})", "daily",
                         "Scraped from the issuer page once per run; ounces of metal held by the trust. Δounces = implied net metal change, not investor cash flow.")
        try:
            r = requests.get(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US"}, timeout=60)
            r.raise_for_status()
            snap = db.archive_raw("ishares_" + tk.lower(), url, r.content, "html", PARSER_VERSION)
            p = _parse(r.text)
            if not p:
                log.warning("etf %s: could not parse ounces (page layout changed?)", tk)
                continue
            day = p.get("as_of") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            meta = {k: v for k, v in p.items() if k != "ounces"}
            meta.update({"metal": metal, "ticker": tk, "sha256": snap["sha256"][:12]})
            n += db.upsert_observations(sid, [(day, p["ounces"], meta)])
        except Exception as e:  # noqa: BLE001
            log.warning("etf %s failed: %s", tk, e)
    return n

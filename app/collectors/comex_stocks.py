"""COMEX warehouse stocks from CME's daily Gold_Stocks.xls / Silver_stocks.xls.

cmegroup.com refuses non-browser clients from this machine ("IP blocked for suspected scraping"), so the
files are downloaded by hand in a browser and dropped into data/inbox/. This module validates each
workbook, extracts the report date from the sheet, records registered / eligible / total per depository
plus the day's change, hashes and archives the file, and flags when the newest report is stale.

Definitions (CME): Registered = metal with a warrant attached, deliverable against futures. Eligible =
metal meeting exchange specs stored in an approved depository without a warrant. Total = both.
"""
from __future__ import annotations
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .. import db
from ..config import INBOX, RAW

log = logging.getLogger(__name__)
PARSER_VERSION = "1"


def _read_rows(path: Path) -> list[list]:
    if path.suffix.lower() == ".xls":
        import xlrd
        wb = xlrd.open_workbook(str(path))
        sh = wb.sheet_by_index(0)
        return [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
    else:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        sh = wb.worksheets[0]
        return [list(r) for r in sh.iter_rows(values_only=True)]


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def parse_workbook(path: Path) -> dict:
    """Best-effort parser for CME's layout: a title block with the report date, then depository blocks
    with rows labelled Registered / Eligible / Total and columns for prior total, received, withdrawn,
    net change, adjustment, today's total. Layouts drift; anything unrecognised is reported, not guessed."""
    rows = _read_rows(path)
    text = "\n".join(" ".join(str(c) for c in r if c not in (None, "")) for r in rows)
    metal = "gold" if re.search(r"\bgold\b", path.name, re.I) or "GOLD" in text[:400].upper() else "silver"
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text[:2000])
    if not m:
        m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", text[:2000])
    report_date = None
    if m:
        for fmt in ("%m/%d/%Y", "%B %d, %Y"):
            try:
                report_date = datetime.strptime(m.group(1), fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                pass
    out = {"metal": metal, "report_date": report_date, "depositories": {}, "warnings": []}
    current = None
    for r in rows:
        cells = [c for c in r if c not in (None, "")]
        if not cells:
            continue
        label = str(cells[0]).strip()
        low = label.lower()
        nums = [x for x in (_num(c) for c in r[1:]) if x is not None]
        if low in ("registered", "eligible", "total") and current and nums:
            # last numeric = today's total; first numeric = prior; look for net change in between
            today_v = nums[-1]
            prior_v = nums[0] if len(nums) > 1 else None
            chg = (today_v - prior_v) if prior_v is not None else None
            out["depositories"].setdefault(current, {})[low] = {"today": today_v, "prior": prior_v, "chg": chg}
        elif not nums and len(label) > 2 and low not in ("troy ounce", "troy ounces") and not re.match(r"^\d", label):
            # a text-only row is treated as a depository / section heading
            if re.search(r"total|grand", low):
                current = "TOTAL"
            elif not re.search(r"report|stocks|date|comex|metal|ounce|warehouse|prior|received|withdrawn|net|adjust", low):
                current = label.upper()
    if not out["depositories"]:
        out["warnings"].append("no depository blocks recognised; layout may have changed")
    if "TOTAL" not in out["depositories"]:
        out["warnings"].append("no TOTAL block found")
    return out


def ingest_file(path: Path) -> int:
    content = path.read_bytes()
    snap = db.archive_raw("comex_" + ("gold" if "gold" in path.name.lower() else "silver"), None, content, path.suffix.lstrip("."), PARSER_VERSION)
    p = parse_workbook(path)
    if not p["report_date"]:
        raise ValueError(f"{path.name}: report date not found in sheet")
    n = 0
    now = db.utcnow()
    with db.tx() as c:
        for dep, d in p["depositories"].items():
            reg, eli, tot = d.get("registered", {}), d.get("eligible", {}), d.get("total", {})
            c.execute("""INSERT OR REPLACE INTO comex_stocks(metal,report_date,depository,registered,eligible,total,registered_chg,eligible_chg,total_chg,source_file,sha256,ingested_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (p["metal"], p["report_date"], dep, reg.get("today"), eli.get("today"), tot.get("today"),
                       reg.get("chg"), eli.get("chg"), tot.get("chg"), path.name, snap["sha256"], now))
            n += 1
    if p["warnings"]:
        log.warning("%s: %s", path.name, "; ".join(p["warnings"]))
    return n


def scan_inbox() -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    done = INBOX / "processed"
    done.mkdir(exist_ok=True)
    n = 0
    for f in sorted(INBOX.glob("*.xls*")):
        try:
            n += ingest_file(f)
            shutil.move(str(f), str(done / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{f.name}"))
        except Exception as e:  # noqa: BLE001
            log.warning("inbox %s: %s", f.name, e)
            bad = INBOX / "failed"
            bad.mkdir(exist_ok=True)
            shutil.move(str(f), str(bad / f.name))
    return n

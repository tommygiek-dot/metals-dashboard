"""Scheduled-event calendar.

Sources: the Fed's FOMC calendar page (parsed for meeting dates), plus data/seeds/events_seed.json for release
dates that live on script-blocked pages (BLS CPI / employment, BEA PCE / GDP) and exchange dates (COMEX
first-notice / option expiry). COT release Fridays are generated. No consensus figures: no free licensed source,
so cards say "consensus unavailable" and Tom can annotate actual/prior by hand later.
"""
from __future__ import annotations
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import requests

from .. import db
from ..config import BROWSER_UA, DATA

log = logging.getLogger(__name__)
PARSER_VERSION = "1"
ET = ZoneInfo("America/New_York")
SEED = DATA / "seeds" / "events_seed.json"
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}


def _eid(title: str, when: str) -> str:
    return hashlib.sha1(f"{title}|{when}".encode()).hexdigest()[:16]


def _put(c, title, category, when_iso, source_url=None, importance=2, notes=None, origin="seed"):
    c.execute("""INSERT INTO events(id,title,category,scheduled_at,source_url,importance,notes,origin) VALUES(?,?,?,?,?,?,?,?)
                 ON CONFLICT(id) DO UPDATE SET title=excluded.title, category=excluded.category, scheduled_at=excluded.scheduled_at,
                 source_url=excluded.source_url, importance=excluded.importance, notes=COALESCE(events.notes, excluded.notes), origin=excluded.origin""",
              (_eid(title, when_iso), title, category, when_iso, source_url, importance, notes, origin))


def fomc_from_page() -> list[tuple[str, str]]:
    """Returns (statement datetime ISO UTC, label) for FOMC meetings found on the Fed calendar page.
    The page lists each year as 'Month  DD-DD' rows; the statement is released 2:00 pm ET on the last day."""
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=60)
    r.raise_for_status()
    db.archive_raw("fomc_calendar", url, r.content, "html", PARSER_VERSION)
    html = r.text
    out = []
    # year sections: <div class="panel panel-default"> ... <h4>2026 FOMC Meetings</h4>
    for ym in re.finditer(r"(20\d\d) FOMC Meetings(.*?)(?=20\d\d FOMC Meetings|$)", html, re.S):
        year = int(ym.group(1))
        block = ym.group(2)
        for m in re.finditer(r'fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?fomc-meeting__date[^>]*>\s*([^<]+)<', block, re.S):
            month_txt, day_txt = m.group(1), m.group(2).strip()
            month_name = month_txt.split("/")[-1]  # "Apr/May" -> May (statement day is the second day)
            mon = None
            for full, i in MONTHS.items():
                if full.lower().startswith(month_name.lower()[:3]):
                    mon = i
                    break
            d = re.findall(r"\d+", day_txt)
            if not mon or not d:
                continue
            last_day = int(d[-1])
            notes = "unscheduled" if "unscheduled" in day_txt.lower() else None
            try:
                when = datetime(year, mon, last_day, 14, 0, tzinfo=ET).astimezone(timezone.utc)
            except ValueError:
                continue
            label = "FOMC statement" + (" + projections (SEP)" if "*" in day_txt else "")
            if "notation" in day_txt.lower():
                label = "FOMC notation vote"
            out.append((when.isoformat(timespec="minutes"), label if not notes else f"{label} ({notes})"))
    return out


def cot_release_fridays(weeks_ahead: int = 8) -> list[str]:
    today = date.today()
    out = []
    d = today
    while len(out) < weeks_ahead:
        if d.weekday() == 4:
            out.append(datetime(d.year, d.month, d.day, 15, 30, tzinfo=ET).astimezone(timezone.utc).isoformat(timespec="minutes"))
        d += timedelta(days=1)
    return out


def update() -> int:
    n = 0
    with db.tx() as c:
        try:
            for when, label in fomc_from_page():
                _put(c, label, "fomc", when, "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", 3, None, "fomc_page")
                n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("fomc calendar failed: %s", e)
        for when in cot_release_fridays():
            _put(c, "CFTC Commitments of Traders release (positions as of Tuesday)", "cot", when,
                 "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm", 1, None, "generated")
            n += 1
        if SEED.exists():
            try:
                for ev in json.loads(SEED.read_text(encoding="utf-8")):
                    t = ev["time_et"] if "time_et" in ev else "08:30"
                    hh, mm = (int(x) for x in t.split(":"))
                    y, mo, d = (int(x) for x in ev["date"].split("-"))
                    when = datetime(y, mo, d, hh, mm, tzinfo=ET).astimezone(timezone.utc).isoformat(timespec="minutes")
                    _put(c, ev["title"], ev["category"], when, ev.get("source_url"), int(ev.get("importance", 2)), ev.get("notes"), "seed")
                    n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("events seed failed: %s", e)
    return n

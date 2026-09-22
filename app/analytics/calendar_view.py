"""Upcoming and recent scheduled events, in the display time zone."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from .. import db
from .changes import to_display_tz


def upcoming(days_ahead: int = 45, days_back: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    lo = (now - timedelta(days=days_back)).isoformat(timespec="minutes")
    hi = (now + timedelta(days=days_ahead)).isoformat(timespec="minutes")
    rows = db.q("SELECT * FROM events WHERE scheduled_at BETWEEN ? AND ? ORDER BY scheduled_at", (lo, hi))
    for r in rows:
        r["display_time"] = to_display_tz(r["scheduled_at"])
        r["is_past"] = r["scheduled_at"] < now.isoformat(timespec="minutes")
        r["consensus"] = r.get("consensus") or None
    return {"now": now.isoformat(timespec="minutes"), "events": rows,
            "notes": "No licensed consensus feed; 'consensus unavailable' unless annotated by hand. Release dates for BLS/BEA come from a seed file (their sites block scripts); FOMC dates are parsed from the Fed's calendar page."}

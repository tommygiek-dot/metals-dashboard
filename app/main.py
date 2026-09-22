"""FastAPI app: JSON API + static single-page UI. Read-mostly; the worker process writes."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import DISPLAY_TZ, ANALYTICS_VERSION
from .analytics import changes as ch

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Metals dashboard", version=ANALYTICS_VERSION)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_EXPORTS = Path(__file__).parent.parent / "exports"
_EXPORTS.mkdir(exist_ok=True)
app.mount("/exports", StaticFiles(directory=_EXPORTS), name="exports")   # past snapshots, browsable

RANGE_DAYS = {"1d": 1, "5d": 5, "1m": 31, "3m": 92, "6m": 183, "1y": 366, "5y": 1830, "10y": 3660, "max": 40000}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def api_config():
    return {"display_tz": DISPLAY_TZ, "analytics_version": ANALYTICS_VERSION, "server_time_utc": db.utcnow()}


@app.get("/api/status")
def api_status():
    return {"jobs": db.job_status(), "series": db.all_series(), "now": db.utcnow()}


@app.get("/api/series/{series_id}")
def api_series(series_id: str, range: str = Query("1y"), intraday: bool = False):
    meta = db.series_meta(series_id)
    if not meta:
        raise HTTPException(404, f"unknown series {series_id}")
    days = RANGE_DAYS.get(range, 366)
    if intraday or meta["cadence"] == "intraday":
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    else:
        since = (date.today() - timedelta(days=days)).isoformat()
    rows = db.get_series(series_id, since=since)
    return {"series": meta, "points": [{"t": r["ts"], "v": r["value"], "m": r["meta"]} for r in rows]}


@app.get("/api/overview")
def api_overview():
    now = datetime.now(timezone.utc)
    metals = {}
    for metal, sid, sid5, oi in (("gold", "gold_fut_cont", "gold_fut_5m", "gold_front_oi"),
                                 ("silver", "silver_fut_cont", "silver_fut_5m", "silver_front_oi")):
        pc = ch.period_changes(sid)
        prov = ch.quote_provenance(sid, now)
        last5 = db.latest(sid5)
        front = db.latest(oi)
        metals[metal] = {"changes": pc, "provenance": prov, "realized_vol": ch.realized_vol(sid),
                         "intraday_last": last5, "front": front}
    related = {}
    for sid in ("dxy", "us10y", "copper_fut", "wti_fut", "spx", "vix", "gld", "slv", "iau", "paxg", "tip_etf",
                "dfii10", "dgs10", "dgs2", "t10yie", "t5yie", "dtwexbgs", "dff", "ust_10y", "ust_real_10y", "ust_be_10y",
                "nyfed_effr", "stak_spot_gold_daily", "stak_spot_silver_daily"):
        if db.series_meta(sid):
            related[sid] = {"changes": ch.period_changes(sid), "provenance": ch.quote_provenance(sid, now)}
    return {"as_of": db.utcnow(), "display_tz": DISPLAY_TZ, "session": ch.is_globex_metals_open(now),
            "us_equity_open": ch.is_us_equity_open(now), "metals": metals, "ratio": ch.ratio_context(),
            "related": related}


@app.get("/api/ratio")
def api_ratio(range: str = "5y"):
    days = RANGE_DAYS.get(range, 1830)
    rs = ch.ratio_series(days)
    return {"points": [{"t": r["ts"], "v": r["value"]} for r in rs], "context": ch.ratio_context()}


@app.get("/api/curve")
def api_curve():
    from .analytics import curve
    return curve.curve_snapshot()


@app.get("/api/cot")
def api_cot(metal: str = "gold", report_type: str = "disagg_fut", weeks: int = 260):
    from .analytics import flows
    return flows.cot_view(metal, report_type, weeks)


@app.get("/api/etf")
def api_etf(days: int = 365):
    from .analytics import flows
    return flows.etf_view(days)


@app.get("/api/drivers")
def api_drivers():
    from .analytics import drivers
    return drivers.driver_board()


@app.get("/api/news")
def api_news(metal: str | None = None, topic: str | None = None, source: str | None = None,
             kind: str | None = None, since: str | None = None, limit: int = 100, clustered: bool = True):
    from .analytics import news_rank
    return news_rank.list_news(metal=metal, topic=topic, source=source, kind=kind, since=since, limit=limit, clustered=clustered)


@app.get("/api/events")
def api_events(days_ahead: int = 45, days_back: int = 7):
    from .analytics import calendar_view
    return calendar_view.upcoming(days_ahead, days_back)


@app.get("/api/briefing")
def api_briefing(date_: str | None = Query(None, alias="date")):
    from .analytics import briefing
    return briefing.get_briefing(date_)


@app.get("/api/briefings")
def api_briefings():
    from .analytics import briefing
    return briefing.list_briefings()


@app.get("/api/physical")
def api_physical():
    from .analytics import physical
    return physical.physical_view()


@app.get("/api/premiums")
def api_premiums():
    from .analytics import physical
    return physical.premium_view()


@app.get("/api/fundamentals")
def api_fundamentals():
    from .analytics import fundamentals
    return fundamentals.fundamentals_view()


@app.get("/api/research")
def api_research():
    from .analytics import research
    return research.research_view()


@app.get("/api/export")
def api_export():
    """Build and download a single self-contained HTML snapshot of every tab."""
    from . import export
    path = export.build()
    return FileResponse(path, media_type="text/html", filename=path.name)


@app.get("/api/snapshot/{tab}")
def api_snapshot(tab: str):
    """Plain-English 'at a glance' paragraph for a tab."""
    from .analytics import snapshots
    return snapshots.snapshot(tab)


@app.post("/api/jobs/{job}/run")
def api_run_job(job: str):
    """Manual trigger (local use). Runs synchronously in this process."""
    from . import scheduler
    if job not in scheduler.JOBS:
        raise HTTPException(404, f"unknown job {job}; known: {sorted(scheduler.JOBS)}")
    return scheduler.run_job(job)

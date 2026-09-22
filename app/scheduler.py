"""Job registry + APScheduler worker. The worker is the single writer to the database.

Run modes (see run.py):
  python run.py            -> web + worker in one process (personal default; APScheduler jobs are
                              serialized with max_instances=1 and coalesce=True)
  python run.py --web      -> web only (read-mostly)
  python run.py --worker   -> worker only
Every job run is logged to job_runs; failures never stop the scheduler.
"""
from __future__ import annotations
import logging
import threading
import traceback
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from . import db
from .config import CADENCE
from .analytics.changes import is_globex_metals_open

log = logging.getLogger(__name__)
_job_lock = threading.Lock()


def _prices_intraday():
    from .collectors import prices_yf
    if not is_globex_metals_open()["open"]:
        return 0, "market closed; skipped"
    return prices_yf.update_intraday(), ""


def _prices_daily():
    from .collectors import prices_yf
    n = prices_yf.update_daily()
    n += prices_yf.update_curve()
    return n, ""


def _prices_backfill():
    from .collectors import prices_yf
    return prices_yf.backfill_daily("max"), ""


def _spot_stak():
    from .collectors import spot_stak
    return spot_stak.update_spot(), ""


def _retail_stak():
    from .collectors import spot_stak
    return spot_stak.update_retail(), ""


def _fred():
    from .collectors import fred
    return fred.update(), ""


def _treasury():
    from .collectors import treasury
    have = db.latest("ust_10y")
    years = None if have else list(range(2016, 2027))   # first run backfills a decade for the research page
    return treasury.update(years), ""


def _nyfed():
    from .collectors import nyfed
    return nyfed.update(), ""


def _cftc():
    from .collectors import cftc
    return cftc.update(), ""


def _etf_holdings():
    from .collectors import etf_holdings
    return etf_holdings.update(), ""


def _news():
    from .collectors import news_rss
    return news_rss.update(), ""


def _comex_inbox():
    from .collectors import comex_stocks
    return comex_stocks.scan_inbox(), ""


def _bls():
    from .collectors import bls
    return bls.update(), ""


def _calendar():
    from .collectors import calendar as cal
    return cal.update(), ""


def _briefing():
    from .analytics import briefing, drivers
    drivers.driver_board(persist=True)
    b = briefing.generate()
    return 1, f"briefing {b['date']} generated"


JOBS = {
    "prices_intraday": _prices_intraday,
    "prices_daily": _prices_daily,
    "prices_backfill": _prices_backfill,
    "spot_stak": _spot_stak,
    "retail_stak": _retail_stak,
    "fred": _fred,
    "treasury": _treasury,
    "nyfed": _nyfed,
    "cftc": _cftc,
    "etf_holdings": _etf_holdings,
    "news": _news,
    "comex_inbox": _comex_inbox,
    "bls": _bls,
    "calendar": _calendar,
    "briefing": _briefing,
}


def run_job(name: str) -> dict:
    fn = JOBS[name]
    started = db.utcnow()
    with _job_lock:  # one collector at a time: single writer, short transactions
        try:
            rows, msg = fn()
            db.log_job(name, started, True, int(rows or 0), msg or "")
            return {"job": name, "ok": True, "rows": rows, "message": msg, "started_at": started}
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc(limit=3)
            log.error("job %s failed: %s\n%s", name, e, tb)
            db.log_job(name, started, False, 0, f"{type(e).__name__}: {e}")
            return {"job": name, "ok": False, "error": f"{type(e).__name__}: {e}", "started_at": started}


def build_scheduler() -> BackgroundScheduler:
    s = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})
    m = lambda k, d: int(CADENCE.get(k, d))  # noqa: E731
    s.add_job(run_job, IntervalTrigger(minutes=m("prices_intraday", 5)), args=["prices_intraday"], id="prices_intraday")
    s.add_job(run_job, IntervalTrigger(minutes=m("prices_daily", 60)), args=["prices_daily"], id="prices_daily")
    s.add_job(run_job, IntervalTrigger(minutes=m("spot_stak", 20)), args=["spot_stak"], id="spot_stak")
    s.add_job(run_job, IntervalTrigger(minutes=m("retail_stak", 30)), args=["retail_stak"], id="retail_stak")
    s.add_job(run_job, IntervalTrigger(minutes=m("fred", 360)), args=["fred"], id="fred")
    s.add_job(run_job, IntervalTrigger(minutes=m("treasury", 360)), args=["treasury"], id="treasury")
    s.add_job(run_job, IntervalTrigger(minutes=m("treasury", 360)), args=["nyfed"], id="nyfed")
    # COT: Fridays 15:35 ET (=19:35/20:35 UTC depending on DST) plus a daily catch-up; CFTC posts late sometimes
    s.add_job(run_job, CronTrigger(day_of_week="fri", hour="20,21", minute=40, timezone="UTC"), args=["cftc"], id="cftc_release")
    s.add_job(run_job, IntervalTrigger(minutes=m("cftc", 720)), args=["cftc"], id="cftc")
    s.add_job(run_job, IntervalTrigger(minutes=m("etf_holdings", 360)), args=["etf_holdings"], id="etf_holdings")
    s.add_job(run_job, IntervalTrigger(minutes=m("news", 30)), args=["news"], id="news")
    s.add_job(run_job, IntervalTrigger(minutes=m("comex_inbox", 15)), args=["comex_inbox"], id="comex_inbox")
    s.add_job(run_job, CronTrigger(hour="13,14", minute=5, timezone="UTC"), args=["bls"], id="bls")  # after 8:30 ET releases
    s.add_job(run_job, IntervalTrigger(hours=24), args=["calendar"], id="calendar")
    s.add_job(run_job, IntervalTrigger(minutes=m("briefing", 60)), args=["briefing"], id="briefing")
    return s


def bootstrap(first_run: bool) -> None:
    """Sequential first pass so the UI is populated within a couple of minutes of first start."""
    order = ["prices_backfill" if first_run else "prices_daily", "prices_daily", "spot_stak", "treasury", "nyfed",
             "cftc", "etf_holdings", "retail_stak", "calendar", "news", "comex_inbox", "bls", "briefing", "fred"]
    for j in order:
        r = run_job(j)
        log.info("bootstrap %s -> %s", j, "ok" if r.get("ok") else r.get("error"))

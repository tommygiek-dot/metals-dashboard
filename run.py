"""Start the dashboard.

  python run.py             web + worker (default)
  python run.py --web       web only
  python run.py --worker    worker only (scheduler + bootstrap)
  python run.py --job NAME  run one collector job and exit (e.g. --job cftc)
"""
from __future__ import annotations
import argparse
import logging
import sys
import threading
import time

def _setup_logging(log_file: str | None):
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    if log_file or sys.stderr is None:      # pythonw / scheduled task: no console, log to a rotating file
        from logging.handlers import RotatingFileHandler
        from pathlib import Path
        p = Path(log_file or "logs/dashboard.log")
        p.parent.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(p, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter(fmt))
        logging.basicConfig(level=logging.INFO, handlers=[h])
    else:
        logging.basicConfig(level=logging.INFO, format=fmt)


log = logging.getLogger("run")


def start_worker():
    from app import db, scheduler
    first = not db.latest("gold_fut_cont")
    threading.Thread(target=scheduler.bootstrap, args=(first,), daemon=True, name="bootstrap").start()
    s = scheduler.build_scheduler()
    s.start()
    log.info("worker started (%d jobs)", len(s.get_jobs()))
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--web", action="store_true")
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--job")
    ap.add_argument("--log", help="log to this file instead of the console (used by the login task)")
    a = ap.parse_args()
    _setup_logging(a.log)
    if a.job:
        from app import scheduler
        r = scheduler.run_job(a.job)
        print(r)
        sys.exit(0 if r.get("ok") else 1)
    from app.config import HOST, PORT
    run_web = a.web or not a.worker
    run_worker = a.worker or not a.web
    if run_worker:
        start_worker()
    if run_web:
        import uvicorn
        uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="info", reload=False, log_config=None)
    else:
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    a = ap.parse_args()
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
        uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="info", reload=False)
    else:
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()

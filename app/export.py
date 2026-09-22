"""Build a self-contained HTML snapshot of the dashboard, or the pieces GitHub Pages needs.

Two products share one page template (index.html + styles.css + app.js inlined):
  * Snapshot file (`build()`): every API response embedded, plus the Pages URL so that when the file is
    opened with internet access it fetches the latest data.json first and falls back to the embedded copy.
  * Pages site (`publish.py`): a small index.html that only fetches data.json, plus data.json itself.
Chart.js loads from its CDN in both cases.
"""
from __future__ import annotations
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from .config import ROOT, DISPLAY_TZ, CFG

STATIC = Path(__file__).parent / "static"
EXPORTS = ROOT / "exports"
PAGES_URL = CFG.get("publish", {}).get("url", "")

PATHS = [
    "/api/config", "/api/overview", "/api/ratio?range=5y", "/api/curve", "/api/etf?days=365", "/api/drivers",
    "/api/news?clustered=true&limit=120", "/api/events?days_ahead=60&days_back=7", "/api/briefing", "/api/briefings",
    "/api/physical", "/api/premiums", "/api/fundamentals", "/api/research", "/api/status",
    "/api/series/gold_front_oi?range=1y", "/api/series/silver_front_oi?range=1y",
] + [f"/api/snapshot/{t}" for t in ("overview", "today", "drivers", "positioning", "physical", "news", "events", "fundamentals", "research", "calculator", "status")] \
  + [f"/api/series/{m}_fut_cont?range={r}" for m in ("gold", "silver") for r in ("1m", "6m", "1y", "5y", "max")] \
  + [f"/api/series/{m}_fut_5m?range={r}" for m in ("gold", "silver") for r in ("1d", "5d")] \
  + [f"/api/cot?metal={m}&report_type={t}&weeks=260" for m in ("gold", "silver") for t in ("disagg_fut", "disagg_futopt", "legacy_fut", "legacy_futopt")]


def stamp_now() -> str:
    return datetime.now(ZoneInfo(DISPLAY_TZ)).strftime("%Y-%m-%d %H:%M %Z")


def collect() -> dict:
    """Every API response the UI needs, keyed by request path."""
    from .main import app
    client = TestClient(app)
    data = {}
    for p in PATHS:
        r = client.get(p)
        if r.status_code == 200:
            data[p] = r.json()
    return data


def write_data_json(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, default=str, separators=(",", ":")), encoding="utf-8")


def write_page(path: Path, data: dict | None, remote_url: str | None, stamp: str) -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    blob = json.dumps(data, default=str).replace("</", "<\\/") if data is not None else "null"
    head = (f"<script>window.__EXPORT__ = {blob}; window.__EXPORT_STAMP__ = {json.dumps(stamp)}; "
            f"window.__REMOTE_DATA__ = {json.dumps(remote_url)};</script>")
    style_block = f"<style>\n{css}\n</style>"
    script_block = f"{head}\n<script>\n{js}\n</script>"
    html = re.sub(r'<link rel="stylesheet" href="/static/styles.css[^"]*">', lambda m: style_block, html)
    html = re.sub(r'<script src="/static/app.js[^"]*"></script>', lambda m: script_block, html)
    html = html.replace("<title>Metals — gold & silver intelligence</title>", f"<title>Metals — gold & silver ({stamp[:10]})</title>")
    html = html.replace('<footer class="muted small">', '<footer class="muted small"><div class="banner" id="snapshot-banner">Loading…</div>')
    path.write_text(html, encoding="utf-8")


def build(out_path: Path | None = None) -> Path:
    data = collect()
    stamp = stamp_now()
    data["_stamp"] = stamp
    EXPORTS.mkdir(exist_ok=True)
    out = out_path or EXPORTS / f"metals-snapshot-{datetime.now(ZoneInfo(DISPLAY_TZ)).strftime('%Y-%m-%d_%H%M')}.html"
    write_page(out, data=data, remote_url=(PAGES_URL.rstrip("/") + "/data.json") if PAGES_URL else None, stamp=stamp)
    return out


if __name__ == "__main__":
    print(build())

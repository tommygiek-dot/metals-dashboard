"""Build a single self-contained HTML snapshot of the dashboard (all tabs, today's data embedded).

The exported page reuses the same styles and app.js; `api()` reads from the embedded JSON instead of the
server, so it opens from a file, an email attachment or a phone with no backend. Chart.js still loads from
its CDN (needs internet). Live-only controls (regenerate, run job, range/filter changes not captured) are
disabled in the export.
"""
from __future__ import annotations
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from .config import ROOT, DISPLAY_TZ

STATIC = Path(__file__).parent / "static"
EXPORTS = ROOT / "exports"

PATHS = [
    "/api/config", "/api/overview", "/api/ratio?range=5y", "/api/curve", "/api/etf?days=365", "/api/drivers",
    "/api/news?clustered=true&limit=120", "/api/events?days_ahead=60&days_back=7", "/api/briefing", "/api/briefings",
    "/api/physical", "/api/premiums", "/api/fundamentals", "/api/research", "/api/status",
    "/api/series/gold_front_oi?range=1y", "/api/series/silver_front_oi?range=1y",
] + [f"/api/snapshot/{t}" for t in ("overview", "today", "drivers", "positioning", "physical", "news", "events", "fundamentals", "research", "status")] \
  + [f"/api/series/{m}_fut_cont?range={r}" for m in ("gold", "silver") for r in ("1m", "6m", "1y", "5y", "max")] \
  + [f"/api/series/{m}_fut_5m?range={r}" for m in ("gold", "silver") for r in ("1d", "5d")] \
  + [f"/api/cot?metal={m}&report_type={t}&weeks=260" for m in ("gold", "silver") for t in ("disagg_fut", "disagg_futopt", "legacy_fut", "legacy_futopt")]


def build(out_path: Path | None = None) -> Path:
    from .main import app
    client = TestClient(app)
    data = {}
    for p in PATHS:
        r = client.get(p)
        if r.status_code == 200:
            data[p] = r.json()
    now_local = datetime.now(ZoneInfo(DISPLAY_TZ))
    stamp = now_local.strftime("%Y-%m-%d %H:%M %Z")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    blob = json.dumps(data, default=str).replace("</", "<\\/")
    style_block = f"<style>\n{css}\n</style>"
    script_block = f"<script>window.__EXPORT__ = {blob}; window.__EXPORT_STAMP__ = {json.dumps(stamp)};</script>\n<script>\n{js}\n</script>"
    # lambda replacements: the blobs contain backslashes that re would otherwise interpret
    html = re.sub(r'<link rel="stylesheet" href="/static/styles.css[^"]*">', lambda m: style_block, html)
    html = re.sub(r'<script src="/static/app.js[^"]*"></script>', lambda m: script_block, html)
    html = html.replace("<title>Metals — gold & silver intelligence</title>", f"<title>Metals snapshot — {now_local.strftime('%Y-%m-%d')}</title>")
    html = html.replace('<footer class="muted small">',
                        f'<footer class="muted small"><div class="banner">This is a saved snapshot of the dashboard taken {stamp}. Numbers do not update; charts need an internet connection to draw.</div>')
    EXPORTS.mkdir(exist_ok=True)
    out = out_path or EXPORTS / f"metals-snapshot-{now_local.strftime('%Y-%m-%d_%H%M')}.html"
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build())

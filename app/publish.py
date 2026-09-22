"""Publish the live dashboard to GitHub Pages.

Writes two files — a small index.html (the app, which fetches data.json at load) and data.json (every
API response the UI needs, ~4 MB) — and force-pushes them as a single-commit orphan `gh-pages` branch, so
the repository's history does not grow by 4 MB every run. GitHub Pages serves the branch at
https://<owner>.github.io/<repo>/ within a minute or two of the push.

Runs from the worker on a schedule (config.toml [publish]) and by hand: python -m app.publish
"""
from __future__ import annotations
import logging
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .config import CFG, ROOT

log = logging.getLogger(__name__)
PUB = CFG.get("publish", {})
REMOTE = PUB.get("remote", "https://github.com/tommygiek-dot/metals-dashboard.git")
BRANCH = PUB.get("branch", "gh-pages")
PAGES_URL = PUB.get("url", "https://tommygiek-dot.github.io/metals-dashboard/")


def _git(args: list[str], cwd: Path) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # never pop a console when running from pythonw
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=300, creationflags=flags)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()[:400]}")
    return r.stdout


def build_site(site: Path) -> dict:
    from . import export
    site.mkdir(parents=True, exist_ok=True)
    data = export.collect()
    stamp = export.stamp_now()
    data["_stamp"] = stamp
    data["_published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    export.write_data_json(data, site / "data.json")
    export.write_page(site / "index.html", data=None, remote_url="data.json", stamp=stamp)
    (site / ".nojekyll").write_text("")
    (site / "README.md").write_text(f"Live snapshot of the Metals dashboard, republished automatically. Data as of {stamp}.\n")
    return {"stamp": stamp, "bytes": (site / "data.json").stat().st_size}


def publish() -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="metals-pages-"))
    try:
        info = build_site(tmp)
        _git(["init", "-q", "-b", BRANCH], tmp)
        _git(["config", "user.name", "Metals dashboard bot"], tmp)
        _git(["config", "user.email", "tommygiek@gmail.com"], tmp)
        _git(["add", "-A"], tmp)
        _git(["commit", "-q", "-m", f"Publish {info['stamp']}"], tmp)
        _git(["push", "--force", "-q", REMOTE, f"{BRANCH}:{BRANCH}"], tmp)
        log.info("published %s (%d bytes) to %s", info["stamp"], info["bytes"], PAGES_URL)
        return {**info, "url": PAGES_URL}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(publish())

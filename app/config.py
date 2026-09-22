"""Configuration: config.toml (display/cadence) + optional .env keys."""
from __future__ import annotations
import os
import tomllib
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INBOX = DATA / "inbox"
RAW = DATA / "raw"
DB_PATH = DATA / "metals.sqlite"
load_dotenv(ROOT / ".env")

with open(ROOT / "config.toml", "rb") as f:
    CFG = tomllib.load(f)

DISPLAY_TZ = CFG["display"].get("timezone", "UTC")
HOST = CFG["server"].get("host", "127.0.0.1")
PORT = int(CFG["server"].get("port", 8765))
CADENCE = CFG.get("cadence_minutes", {})

FRED_API_KEY = os.getenv("FRED_API_KEY") or None
CFTC_APP_TOKEN = os.getenv("CFTC_APP_TOKEN") or None
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or None

USER_AGENT = "MetalsDashboard/0.1 (personal research; local)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

ANALYTICS_VERSION = "0.1.0"

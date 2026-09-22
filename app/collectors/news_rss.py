"""News and official-release collection from RSS/Atom feeds.

Rules: metadata + link + short excerpt only (feed-provided summary, trimmed). No full article bodies are
fetched or stored. Google News items are headline + publisher only (headline_only=1). Feed text is
untrusted data: it is stored and displayed, never interpreted as instructions.
"""
from __future__ import annotations
import hashlib
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import requests

from .. import db
from ..config import BROWSER_UA

log = logging.getLogger(__name__)
PARSER_VERSION = "1"

# name -> (url, kind, default topics)
FEEDS = {
    "Fed press releases": ("https://www.federalreserve.gov/feeds/press_all.xml", "official", ["fed"]),
    "Fed monetary policy": ("https://www.federalreserve.gov/feeds/press_monetary.xml", "official", ["fed", "fomc"]),
    "Fed speeches": ("https://www.federalreserve.gov/feeds/speeches.xml", "official", ["fed", "speech"]),
    "Fed H.4.1 balance sheet": ("https://www.federalreserve.gov/feeds/h41.xml", "official", ["fed", "liquidity"]),
    "CFTC press": ("https://www.cftc.gov/RSS/RSSGP/rssgp.xml", "official", ["cftc", "regulation"]),
    "BEA releases": ("https://apps.bea.gov/rss/rss.xml", "official", ["macro", "gdp", "pce"]),
    "ECB press": ("https://www.ecb.europa.eu/rss/press.html", "official", ["ecb", "central-bank"]),
    "Bank of England": ("https://www.bankofengland.co.uk/rss/news", "official", ["boe", "central-bank"]),
    "World Gold Council": ("https://www.gold.org/rss.xml", "research", ["gold", "wgc", "demand"]),
    "Silver Institute": ("https://www.silverinstitute.org/feed/", "research", ["silver", "industrial", "supply"]),
    "Metals Focus": ("https://www.metalsfocus.com/feed/", "research", ["research"]),
    "Mining.com": ("https://www.mining.com/feed/", "reporting", ["mining", "supply"]),
    "CNBC gold": ("https://www.cnbc.com/id/10000664/device/rss/rss.html", "reporting", ["gold"]),
    "WSJ markets": ("https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", "reporting", ["markets"]),
    "FT commodities": ("https://www.ft.com/commodities?format=rss", "reporting", ["commodities"]),
    "Google News: gold": ("https://news.google.com/rss/search?q=%22gold%22+(price+OR+bullion+OR+%22central+bank%22+OR+comex)&hl=en-US&gl=US&ceid=US:en", "reporting", ["gold"]),
    "Google News: silver": ("https://news.google.com/rss/search?q=%22silver%22+(price+OR+solar+OR+industrial+OR+comex+OR+bullion)&hl=en-US&gl=US&ceid=US:en", "reporting", ["silver"]),
    "Google News: Reuters metals": ("https://news.google.com/rss/search?q=site:reuters.com+(gold+OR+silver)+(price+OR+bullion)&hl=en-US&gl=US&ceid=US:en", "reporting", ["gold", "silver"]),
    "Google News: Bloomberg metals": ("https://news.google.com/rss/search?q=site:bloomberg.com+(gold+OR+silver)&hl=en-US&gl=US&ceid=US:en", "reporting", ["gold", "silver"]),
}

TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref", "cmpid", "ncid"}
OPINION_RX = re.compile(r"\b(opinion|column|commentary|op-ed|analysis:|why (gold|silver) (will|could|may)|forecasts?|outlook|predicts?|price target|could hit|to hit \$|how to|tips|guide|should you|best (way|time) to|beginner)\b", re.I)
FORECAST_RX = re.compile(r"\b(forecasts?|outlook|price targets?|predicts?|predictions?|expects? .* to (reach|hit)|could (reach|hit|surge to|fall to)|by 20\d\d)\b", re.I)


def canonical_url(u: str) -> str:
    try:
        p = urlparse(u)
        qs = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in TRACKING]
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", urlencode(qs), ""))
    except Exception:  # noqa: BLE001
        return u


def title_hash(t: str) -> str:
    words = re.findall(r"[a-z0-9]+", t.lower())
    stop = {"the", "a", "an", "of", "to", "in", "on", "as", "and", "for", "at", "by", "is", "are", "with", "from", "after", "amid", "vs"}
    core = " ".join(w for w in words if w not in stop)[:120]
    return hashlib.sha1(core.encode()).hexdigest()[:16]


def classify(title: str, summary: str, feed_kind: str) -> tuple[str, list[str], list[str]]:
    text = f"{title} {summary}".lower()
    metals = []
    if re.search(r"\bgold\b|bullion|xau", text):
        metals.append("gold")
    if re.search(r"\bsilver\b|xag", text):
        metals.append("silver")
    topics = []
    for rx, t in ((r"\bfed\b|fomc|powell|rate (cut|hike)|federal reserve", "fed"), (r"\bcpi\b|inflation|pce\b", "inflation"),
                  (r"payrolls|jobs report|unemployment|employment", "employment"), (r"\btariff|trade war", "trade"),
                  (r"central bank|pboc|rbi\b|reserves", "central-banks"), (r"\betf\b|inflows|outflows|holdings", "etf-flows"),
                  (r"comex|warehouse|inventor|delivery|registered|eligible", "comex-physical"), (r"\bsolar\b|photovoltaic|electronics|industrial demand", "industrial"),
                  (r"\bmine\b|mining|production|output|strike|disruption", "mine-supply"), (r"dollar|dxy|greenback", "dollar"),
                  (r"yield|treasur|bond", "yields"), (r"geopolit|war\b|missile|sanction|attack|conflict|ceasefire", "geopolitics"),
                  (r"shanghai|china|india|premium", "regional-demand"), (r"cftc|speculat|managed money|positioning|net long|net short", "positioning"),
                  (r"recycl|scrap", "recycling"), (r"jewel", "jewelry")):
        if re.search(rx, text):
            topics.append(t)
    if feed_kind == "official":
        kind = "official"
    elif FORECAST_RX.search(title):
        kind = "forecast"
    elif OPINION_RX.search(title):
        kind = "opinion"
    elif feed_kind == "research":
        kind = "research"
    else:
        kind = "reporting"
    return kind, metals, topics


def _pub(e) -> str | None:
    for k in ("published_parsed", "updated_parsed"):
        v = e.get(k)
        if v:
            try:
                return datetime(*v[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")
            except Exception:  # noqa: BLE001
                pass
    for k in ("published", "updated"):
        v = e.get(k)
        if v:
            try:
                return parsedate_to_datetime(v).astimezone(timezone.utc).isoformat(timespec="seconds")
            except Exception:  # noqa: BLE001
                pass
    return None


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def update(only: list[str] | None = None) -> int:
    n = 0
    now = db.utcnow()
    for name, (url, feed_kind, default_topics) in FEEDS.items():
        if only and name not in only:
            continue
        try:
            r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=40)
            r.raise_for_status()
            db.archive_raw("rss_" + re.sub(r"[^a-z0-9]+", "_", name.lower()), url, r.content, "xml", PARSER_VERSION)
            fp = feedparser.parse(r.content)
        except Exception as e:  # noqa: BLE001
            log.warning("feed %s failed: %s", name, e)
            continue
        is_google = "news.google.com" in url
        rows = []
        for e in fp.entries[:80]:
            link = e.get("link") or ""
            title = _strip_html(e.get("title") or "")
            if not link or not title:
                continue
            publisher = name.split(":")[0]
            if is_google:
                src = e.get("source")
                publisher = (src.get("title") if isinstance(src, dict) else None) or publisher
                # Google titles end with " - Publisher"
                title = re.sub(r"\s+-\s+[^-]{2,60}$", "", title)
            summary = "" if is_google else _strip_html(e.get("summary") or e.get("description") or "")[:400]
            cu = canonical_url(link)
            aid = hashlib.sha1(cu.encode()).hexdigest()[:20]
            kind, metals, topics = classify(title, summary, feed_kind)
            topics = sorted(set(topics + default_topics))
            author = e.get("author")
            rows.append((aid, cu, title, publisher, author, _pub(e), None, now, name, kind, metals, topics, summary, 1 if is_google or not summary else 0, title_hash(title)))
        import json
        with db.tx() as c:
            for row in rows:
                (aid, cu, title, publisher, author, pub, ev, ing, feed, kind, metals, topics, summary, ho, th) = row
                cur = c.execute("SELECT 1 FROM articles WHERE id=?", (aid,)).fetchone()
                if cur:
                    continue
                c.execute("""INSERT INTO articles(id,url,title,publisher,author,published_at,event_at,ingested_at,feed,kind,metals,topics,geo,
                             summary,why_matters,excerpt,content_stored,headline_only,cluster_id,score,title_hash)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,NULL,0,?)""",
                          (aid, cu, title, publisher, author, pub, ev, ing, feed, kind, json.dumps(metals), json.dumps(topics), json.dumps([]),
                           summary, None, summary[:280] if summary else None, ho, th))
                n += 1
    if n:
        from ..analytics import news_rank
        news_rank.recluster_and_score()
    return n

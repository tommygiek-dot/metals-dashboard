"""Dedupe, cluster and rank collected items.

Clustering: identical canonical URL is already deduped at insert. Items whose normalized-title hash matches,
or whose title token Jaccard >= 0.6 within a 48h window, join one cluster; the canonical item is the earliest
official/research item, else the earliest reporting item. A cluster counts as ONE source unless its members
come from distinct publishers.
Score = relevance (metal + topic hits) + novelty (new cluster) + timeliness (age decay) + source quality
(official > research > reporting > opinion/forecast) + significance (cluster breadth across publishers).
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone

from .. import db

SOURCE_QUALITY = {"official": 3.0, "research": 2.5, "reporting": 2.0, "opinion": 0.8, "forecast": 0.6, "unknown": 1.0}
PUBLISHER_BONUS = {"Reuters": 0.8, "Bloomberg": 0.8, "Financial Times": 0.6, "The Wall Street Journal": 0.6, "WSJ markets": 0.6,
                   "CNBC gold": 0.3, "Mining.com": 0.3, "Fed press releases": 0.5, "Fed monetary policy": 1.0, "Fed speeches": 0.6}
STOP = {"the", "a", "an", "of", "to", "in", "on", "as", "and", "for", "at", "by", "is", "are", "with", "from", "after", "amid", "vs", "says", "said"}


def _tokens(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP and len(w) > 2}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def recluster_and_score(days: int = 14) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    arts = db.q("SELECT id,title,publisher,published_at,ingested_at,kind,metals,topics,title_hash,feed FROM articles "
                "WHERE COALESCE(published_at, ingested_at) >= ? ORDER BY COALESCE(published_at, ingested_at)", (since,))
    clusters: list[dict] = []   # {id, tokens, members[], first}
    assign: dict[str, str] = {}
    for a in arts:
        toks = _tokens(a["title"])
        when = a["published_at"] or a["ingested_at"]
        hit = None
        for cl in clusters:
            if abs((datetime.fromisoformat(when) - datetime.fromisoformat(cl["first"])).total_seconds()) > 48 * 3600:
                continue
            if a["title_hash"] == cl["hash"] or _jaccard(toks, cl["tokens"]) >= 0.6:
                hit = cl
                break
        if hit is None:
            hit = {"id": a["id"], "hash": a["title_hash"], "tokens": toks, "members": [], "first": when}
            clusters.append(hit)
        hit["members"].append(a)
        hit["tokens"] |= toks
        assign[a["id"]] = hit["id"]
    now = datetime.now(timezone.utc)
    updates = []
    for cl in clusters:
        pubs = {m["publisher"] for m in cl["members"]}
        breadth = min(len(pubs), 6)
        for m in cl["members"]:
            when = datetime.fromisoformat(m["published_at"] or m["ingested_at"])
            age_h = max((now - when).total_seconds() / 3600, 0)
            metals = json.loads(m["metals"] or "[]")
            topics = json.loads(m["topics"] or "[]")
            relevance = 1.5 * len(metals) + 0.4 * len(topics)
            timeliness = 3.0 * (0.5 ** (age_h / 24))
            quality = SOURCE_QUALITY.get(m["kind"], 1.0) + PUBLISHER_BONUS.get(m["publisher"] or "", 0) + PUBLISHER_BONUS.get(m["feed"] or "", 0)
            significance = 0.6 * breadth
            novelty = 1.0 if m["id"] == cl["id"] else 0.0
            score = relevance + timeliness + quality + significance + novelty
            updates.append((cl["id"], round(score, 3), m["id"]))
    with db.tx() as c:
        c.executemany("UPDATE articles SET cluster_id=?, score=? WHERE id=?", updates)
    return len(clusters)


def list_news(metal=None, topic=None, source=None, kind=None, since=None, limit=100, clustered=True) -> dict:
    sql = "SELECT * FROM articles WHERE 1=1"
    p: list = []
    if metal:
        sql += " AND metals LIKE ?"; p.append(f'%"{metal}"%')
    if topic:
        sql += " AND topics LIKE ?"; p.append(f'%"{topic}"%')
    if source:
        sql += " AND (publisher LIKE ? OR feed LIKE ?)"; p += [f"%{source}%", f"%{source}%"]
    if kind:
        sql += " AND kind=?"; p.append(kind)
    if since:
        sql += " AND COALESCE(published_at, ingested_at) >= ?"; p.append(since)
    sql += " ORDER BY score DESC, COALESCE(published_at, ingested_at) DESC LIMIT ?"
    p.append(limit * (4 if clustered else 1))
    rows = db.q(sql, p)
    for r in rows:
        for k in ("metals", "topics", "geo"):
            r[k] = json.loads(r[k] or "[]")
    if not clustered:
        return {"items": rows[:limit], "clustered": False}
    seen: dict[str, dict] = {}
    order = []
    for r in rows:
        cid = r["cluster_id"] or r["id"]
        if cid not in seen:
            seen[cid] = {"canonical": r, "others": [], "publishers": {r["publisher"]}}
            order.append(cid)
        else:
            seen[cid]["others"].append({"id": r["id"], "title": r["title"], "publisher": r["publisher"], "url": r["url"], "published_at": r["published_at"], "kind": r["kind"]})
            seen[cid]["publishers"].add(r["publisher"])
    items = []
    for cid in order[:limit]:
        c = seen[cid]
        items.append({**c["canonical"], "cluster_size": 1 + len(c["others"]), "independent_publishers": len(c["publishers"]), "also_reported_by": c["others"][:8]})
    facets = {
        "sources": db.q("SELECT publisher AS name, COUNT(*) n FROM articles GROUP BY publisher ORDER BY n DESC LIMIT 40"),
        "kinds": db.q("SELECT kind AS name, COUNT(*) n FROM articles GROUP BY kind ORDER BY n DESC"),
    }
    return {"items": items, "clustered": True, "facets": facets}

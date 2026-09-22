"""Unit tests that do not touch the network. Run: python -m pytest -q"""
from __future__ import annotations
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# isolate the database before importing the app
_tmp = tempfile.mkdtemp()
os.environ["METALS_TEST_DB"] = os.path.join(_tmp, "t.sqlite")
from app import db  # noqa: E402
db.DB_PATH = Path(os.environ["METALS_TEST_DB"])  # type: ignore[attr-defined]
db._con = None


def test_migrations_and_revisions():
    db.ensure_series("t1", "test", "macro", "pct", "unit-test", "daily")
    assert db.upsert_observations("t1", [("2026-01-01", 1.0, None), ("2026-01-02", 2.0, None)]) == 2
    # unchanged value: no new revision
    assert db.upsert_observations("t1", [("2026-01-02", 2.0, None)]) == 0
    # changed value: revision bump + history row
    assert db.upsert_observations("t1", [("2026-01-02", 2.5, None)]) == 1
    row = db.q1("SELECT value, revision FROM observations WHERE series_id='t1' AND ts='2026-01-02'")
    assert row["value"] == 2.5 and row["revision"] == 2
    hist = db.q("SELECT * FROM observation_history WHERE series_id='t1'")
    assert len(hist) == 1 and hist[0]["value"] == 2.0 and hist[0]["revision"] == 1


def test_period_changes_and_ratio():
    from app.analytics import changes as ch
    db.ensure_series("gold_fut_cont", "g", "future_continuous", "USD/oz", "test", "daily")
    db.ensure_series("silver_fut_cont", "s", "future_continuous", "USD/oz", "test", "daily")
    import datetime as dt
    base = dt.date.today() - dt.timedelta(days=30)
    g = [((base + dt.timedelta(days=i)).isoformat(), 4000 + i * 10, None) for i in range(31)]
    s = [((base + dt.timedelta(days=i)).isoformat(), 50 + i * 0.1, None) for i in range(31)]
    db.upsert_observations("gold_fut_cont", g); db.upsert_observations("silver_fut_cont", s)
    pc = ch.period_changes("gold_fut_cont")
    assert pc["available"] and abs(pc["changes"]["1d"]["abs"] - 10) < 1e-9
    assert pc["changes"]["1w"]["base_ts"] == (dt.date.today() - dt.timedelta(days=7)).isoformat()
    rc = ch.ratio_context()
    assert rc["available"] and abs(rc["value"] - (4300 / 53.0)) < 1e-6
    vol = ch.realized_vol("gold_fut_cont")
    assert vol["10d"] is not None and vol["10d"] > 0


def test_session_rules():
    from app.analytics.changes import is_globex_metals_open, ET
    def at(wd_iso, hh, mm=0):
        # 2026-09-21 is a Monday
        d = datetime(2026, 9, 21 + (wd_iso - 1), hh, mm, tzinfo=ET)
        return is_globex_metals_open(d)["open"]
    assert at(1, 10) is True           # Monday 10:00 ET open
    assert at(1, 17, 30) is False      # daily break
    assert at(5, 16, 59) is True       # Friday before 5pm
    assert at(5, 17, 1) is False       # Friday after 5pm
    assert at(6, 12) is False          # Saturday
    assert at(7, 17) is False and at(7, 18, 5) is True   # Sunday reopen 6pm


def test_contract_tickers():
    from app.collectors.prices_yf import contract_tickers
    t = contract_tickers("gold", 4, datetime(2026, 9, 21, tzinfo=timezone.utc))
    assert t == ["GCV26.CMX", "GCZ26.CMX", "GCG27.CMX", "GCJ27.CMX"]
    s = contract_tickers("silver", 3, datetime(2026, 12, 30, tzinfo=timezone.utc))
    assert s == ["SIZ26.CMX", "SIH27.CMX", "SIK27.CMX"]


def test_news_canonical_and_classify():
    from app.collectors.news_rss import canonical_url, classify, title_hash
    assert canonical_url("https://Example.com/a/b/?utm_source=x&id=2") == "https://example.com/a/b?id=2"
    kind, metals, topics = classify("Gold jumps as Fed signals rate cut; silver follows", "", "reporting")
    assert kind == "reporting" and metals == ["gold", "silver"] and "fed" in topics
    kind, _, _ = classify("Gold price forecast: could hit $6,000 by 2027", "", "reporting")
    assert kind == "forecast"
    assert title_hash("Gold rises on Fed hopes") == title_hash("gold rises on the fed hopes")


def test_cot_field_mapping():
    import json
    from app.analytics import flows
    now = db.utcnow()
    with db.tx() as c:
        for i, d in enumerate(["2026-09-01", "2026-09-08", "2026-09-15"]):
            data = {"m_money_positions_long_all": 100 + i, "m_money_positions_short_all": 40, "open_interest_all": 500 + i,
                    "prod_merc_positions_long": 10, "prod_merc_positions_short": 60, "swap_positions_long_all": 5, "swap__positions_short_all": 20}
            c.execute("INSERT OR REPLACE INTO cot(report_type,contract_code,report_date,market_name,data,retrieved_at) VALUES(?,?,?,?,?,?)",
                      ("disagg_fut", "088691", d, "GOLD - COMMODITY EXCHANGE INC.", json.dumps(data), now))
    v = flows.cot_view("gold", "disagg_fut", 52)
    assert v["available"] and v["context"]["value"] == 62 and v["context"]["change_wow"] == 1
    assert v["history"][-1]["comm_like_net"] == (10 - 60) + (5 - 20)


def test_comex_parser_layout():
    from app.collectors import comex_stocks
    import openpyxl
    p = Path(_tmp) / "Gold_Stocks.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active
    rows = [["GOLD STOCKS", None], ["Report Date: 09/18/2026", None], [None], ["BRINKS", None],
            ["Registered", 100, 5, 0, 5, 0, 105], ["Eligible", 200, 0, 10, -10, 0, 190], ["Total", 300, 5, 10, -5, 0, 295],
            ["TOTAL", None], ["Registered", 1000, 5, 0, 5, 0, 1005], ["Eligible", 2000, 0, 10, -10, 0, 1990], ["Total", 3000, 5, 10, -5, 0, 2995]]
    for r in rows:
        ws.append(r)
    wb.save(p)
    out = comex_stocks.parse_workbook(p)
    assert out["metal"] == "gold" and out["report_date"] == "2026-09-18"
    assert out["depositories"]["TOTAL"]["registered"]["today"] == 1005 and out["depositories"]["TOTAL"]["registered"]["chg"] == 5
    assert out["depositories"]["BRINKS"]["eligible"]["today"] == 190

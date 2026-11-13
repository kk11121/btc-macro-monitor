"""過去の通知の復元（scripts/reconstruct_history.py）の数え方。"""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "reconstruct_history.py"
spec = importlib.util.spec_from_file_location("reconstruct_history", SCRIPT)
rh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rh)

T = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)


def _old(**overrides):
    state = {
        "cpi": {"date": "2026-04-01", "value": 1.0},
        "fomc": {"url": "a", "date": "2026-04-29"},
        "market": {"dxy": {"value": 100.0}, "vix": {"value": 20.0}},
        "stablecoin": {"usdt": {"value": 1.0}},
        "liquidity": {"m2sl": {"date": "2026-04-01", "value": 1.0}},
        "glassnode": {"fear_greed": {"value": 50, "zone": "neutral"},
                      "etf_flow": {"date": "2026-05-31", "value": 1.0},
                      "funding_rate": {"value": 0.0001}},
        "sec": {"date": "2026-06-01", "seen_ids": ["a", "b"]},
    }
    for key, value in overrides.items():
        state[key] = value
    return state


def test_old_format_counts_changes_like_the_old_code():
    prev = _old()
    cur = _old(
        cpi={"date": "2026-05-01", "value": 2.0},
        market={"dxy": {"value": 101.0}, "vix": {"value": 20.0}},
        glassnode={"fear_greed": {"value": 20, "zone": "fear"},
                   "etf_flow": {"date": "2026-06-01", "value": 2.0},
                   "funding_rate": {"value": -0.0001}},
        sec={"date": "2026-06-01", "seen_ids": ["a", "b", "c", "d"]},
    )
    events = dict(rh.diff_old(prev, cur))
    assert events == {"cpi": 1, "dxy": 1, "exchange_netflow": 1, "fear_greed": 1,
                      "funding_rate": 1, "sec": 2}


def test_old_sec_counts_all_ids_on_a_new_day():
    prev = _old(sec={"date": "2026-06-01", "seen_ids": ["a", "b"]})
    cur = _old(sec={"date": "2026-06-02", "seen_ids": ["x"]})
    assert dict(rh.diff_old(prev, cur)) == {"sec": 1}


def test_old_fear_greed_first_value_is_silent():
    prev = _old(glassnode={"fear_greed": {"value": None, "zone": None}})
    cur = _old(glassnode={"fear_greed": {"value": 50, "zone": "neutral"}})
    assert "fear_greed" not in dict(rh.diff_old(prev, cur))


def test_funding_threshold_crossing():
    assert rh._funding_notified(0.0001, 0.0006)        # 0.05% をまたぐ
    assert not rh._funding_notified(0.0001, 0.0004)
    assert rh._funding_notified(0.0001, -0.0001)       # 符号の反転
    assert not rh._funding_notified(None, 0.0001)


def _new(**overrides):
    state = {
        "schema_version": 2,
        "cpi": {"date": "2026-09-01", "value": 1.0},
        "fomc": {"statement": {"date": "2026-10-28"}, "minutes": {"date": "2026-09-17"}},
        "market": {"vix": {"value": 20.0}},
        "flows": {"spot_etf": {"date": "2026-11-03"}, "exchange_netflow": {"date": "2026-11-03"}},
        "summary": {"last_date": "2026-11-05"},
        "errors": {},
    }
    state.update(overrides)
    return state


def test_new_format_counts_alerts_flows_morning_and_errors():
    prev = _new()
    cur = _new(
        cpi={"date": "2026-10-01", "value": 2.0},
        market={"vix": {"value": 23.0}},
        flows={"spot_etf": {"date": "2026-11-04"}, "exchange_netflow": {"date": "2026-11-03"}},
        summary={"last_date": "2026-11-06"},
        errors={"dxy": {}},
    )
    assert dict(rh.diff_new(prev, cur)) == {"cpi": 1, "vix": 1, "spot_etf": 1, "morning": 1,
                                            "error": 1}


def test_new_format_vix_drop_is_a_silent_rebase():
    assert "vix" not in dict(rh.diff_new(_new(), _new(market={"vix": {"value": 15.0}})))


def test_migration_commit_is_not_counted():
    snapshots = [(T, _old()), (T + timedelta(hours=1), _new()),
                 (T + timedelta(hours=2), _new(summary={"last_date": "2026-11-06"}))]
    events = rh.reconstruct(snapshots)
    assert [(e["indicator"], e["count"]) for e in events] == [("morning", 1)]


def test_moves_and_aggregate_against_baseline():
    closes = {T + timedelta(hours=h): 100.0 + (10.0 if h == 1 else 0.0) for h in range(0, 100)}
    events = [{"ts": T + timedelta(minutes=5), "indicator": "cpi", "count": 1}]
    enriched = rh.attach_moves(events, closes)
    assert enriched[0]["btc_price"] == 100.0
    assert round(enriched[0]["moves"][1], 3) == 0.1
    base = rh.baseline(closes)
    rows = rh.aggregate(enriched, base)
    assert rows[0]["indicator"] == "cpi" and rows[0]["count"] == 1
    assert rows[0]["ratio_1"] > 1


def test_markdown_lists_indicators_and_months():
    events = [{"ts": T, "indicator": "sec", "count": 3, "btc_price": 1.0,
               "moves": {1: 0.01, 24: 0.02, 72: 0.03}}]
    rows = rh.aggregate(events, {1: 0.01, 24: 0.02, 72: 0.03})
    text = rh.render_markdown("2026-05-24", "2026-11-13", events, rows,
                              {1: 0.01, 24: 0.02, 72: 0.03})
    assert "| SEC | 3 |" in text
    assert "2026-06" in text

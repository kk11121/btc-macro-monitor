"""通知の記録（logs/alerts.jsonl）と、通知後の BTC 価格の書き足し。"""

import json
from datetime import datetime, timedelta, timezone

import alert_log
from runtime import Runtime
from state_store import StateStore

T0 = datetime(2026, 11, 13, 10, 23, tzinfo=timezone.utc)


def _runtime(tmp_path, sender, dry_run=False):
    store = StateStore(tmp_path / "state.json", dry_run=dry_run)
    store.load()
    return Runtime(store, tmp_path / "h.json", sender=sender, dry_run=dry_run,
                   log_dir=tmp_path / "logs")


def test_send_appends_one_line_with_empty_prices(tmp_path, sender):
    rt = _runtime(tmp_path, sender)
    rt.send("本文", {"indicator": "vix", "kind": "immediate", "actual": 23.0,
                   "compared": {"previous": 20.0}, "verdict": "弱気"})
    rows = alert_log.read(rt.alert_log_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["indicator"] == "vix" and row["kind"] == "immediate" and row["verdict"] == "弱気"
    assert len(row["id"]) == 16
    for field in ("btc_price", "btc_price_1h", "btc_price_24h", "btc_price_72h"):
        assert row[field] is None


def test_failed_send_is_not_recorded(tmp_path, sender):
    rt = _runtime(tmp_path, sender)
    sender.fail_on = ["本文"]
    try:
        rt.send("本文", {"indicator": "vix"})
    except Exception:
        pass
    assert alert_log.read(rt.alert_log_path) == []


def test_dry_run_records_nothing(tmp_path, sender):
    rt = _runtime(tmp_path, sender, dry_run=True)
    rt.send("本文", {"indicator": "vix"})
    assert not (tmp_path / "logs" / "alerts.jsonl").exists()


def test_error_and_recovery_notices_are_recorded(tmp_path, sender):
    rt = _runtime(tmp_path, sender)

    def fail():
        raise RuntimeError("down")

    rt.guard("dxy", "DXY", fail)
    rt.guard("dxy", "DXY", lambda: None)
    kinds = [(r["indicator"], r["kind"]) for r in alert_log.read(rt.alert_log_path)]
    assert kinds == [("dxy", "error"), ("dxy", "recovery")]


def _row(ts, **prices):
    base = alert_log.new_record({"indicator": "cpi", "kind": "immediate"}, ts)
    return {**base, **prices}


def _closes(start, end):
    """毎時の終値を 100, 101, ... とする偽の OKX。"""
    closes, t, value = {}, start, 100.0
    while t <= end:
        closes[t] = value + (t - datetime(2026, 11, 13, tzinfo=timezone.utc)).total_seconds() / 3600
        t += timedelta(hours=1)
    return closes


def test_fill_prices_uses_close_of_the_hour_containing_each_time(tmp_path):
    path = tmp_path / "alerts.jsonl"
    alert_log.write(path, [_row(T0)])
    now = T0 + timedelta(hours=3)
    filled = alert_log.fill_prices(path, now, _closes)
    row = alert_log.read(path)[0]
    assert filled == 2  # 通知時点と1時間後だけ確定している
    assert row["btc_price"] == 110.0       # 10:00 の足
    assert row["btc_price_1h"] == 111.0    # 11:00 の足
    assert row["btc_price_24h"] is None and row["btc_price_72h"] is None


def test_fill_prices_retries_missing_and_fills_later_horizons(tmp_path):
    path = tmp_path / "alerts.jsonl"
    alert_log.write(path, [_row(T0)])

    def missing(start, end):
        return {}

    assert alert_log.fill_prices(path, T0 + timedelta(hours=3), missing) == 0
    assert alert_log.read(path)[0]["btc_price"] is None  # 空欄のまま残す
    filled = alert_log.fill_prices(path, T0 + timedelta(hours=80), _closes)
    row = alert_log.read(path)[0]
    assert filled == 4
    assert row["btc_price_72h"] == 110.0 + 72


def test_fill_prices_skips_rows_older_than_limit(tmp_path):
    path = tmp_path / "alerts.jsonl"
    alert_log.write(path, [_row(T0 - timedelta(days=200))])
    calls = []
    assert alert_log.fill_prices(path, T0, lambda s, e: calls.append(1) or {}) == 0
    assert calls == []


def test_read_skips_broken_lines(tmp_path):
    path = tmp_path / "alerts.jsonl"
    path.write_text(json.dumps({"ts": "x"}) + "\n{broken\n", encoding="utf-8")
    assert len(alert_log.read(path)) == 1


def test_latency_is_written_only_outside_dry_run(tmp_path, sender):
    rt = _runtime(tmp_path, sender)
    rt.log_latency({"release": "cpi", "seconds": 42})
    assert "42" in rt.latency_log_path.read_text(encoding="utf-8")
    dry = _runtime(tmp_path / "d", sender, dry_run=True)
    dry.log_latency({"release": "cpi", "seconds": 1})
    assert not (tmp_path / "d" / "logs").exists()

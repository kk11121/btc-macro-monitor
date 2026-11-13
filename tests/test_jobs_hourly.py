from datetime import datetime, timezone

import jobs_hourly
from state_store import load_etf_history, save_etf_history


def obs(*pairs):
    return [{"date": d, "value": v} for d, v in pairs]


# ── VIX ───────────────────────────────────────────────────────────────────────


def _vix(monkeypatch, value):
    monkeypatch.setattr(jobs_hourly.market, "fetch_market", lambda key: {"value": value})


def test_vix_spike_alerts_and_moves_baseline(make_runtime, sender, monkeypatch):
    rt = make_runtime({"market": {"vix": {"value": 20.0}}})
    _vix(monkeypatch, 23.0)
    jobs_hourly.check_vix(rt)
    assert len(sender.messages) == 1
    assert "VIX" in sender.messages[0]
    assert "🔴" in sender.messages[0]
    assert rt.store.get("market", "vix", "value") == 23.0


def test_vix_small_move_is_silent_and_keeps_baseline(make_runtime, sender, monkeypatch):
    rt = make_runtime({"market": {"vix": {"value": 20.0}}})
    _vix(monkeypatch, 21.0)
    jobs_hourly.check_vix(rt)
    assert sender.messages == []
    assert rt.store.get("market", "vix", "value") == 20.0


def test_vix_drop_rebases_silently(make_runtime, sender, monkeypatch):
    rt = make_runtime({"market": {"vix": {"value": 30.0}}})
    _vix(monkeypatch, 20.0)
    jobs_hourly.check_vix(rt)
    assert sender.messages == []
    assert rt.store.get("market", "vix", "value") == 20.0


def test_vix_send_failure_keeps_baseline_for_retry(make_runtime, sender, monkeypatch):
    rt = make_runtime({"market": {"vix": {"value": 20.0}}})
    _vix(monkeypatch, 23.0)
    sender.fail_on = ["VIX"]
    rt.guard("vix", "VIX", lambda: jobs_hourly.check_vix(rt))
    assert rt.notification_failed is True
    assert rt.store.get("market", "vix", "value") == 20.0


# ── 日次フロー ─────────────────────────────────────────────────────────────────


def _etf_rows(source="sosovalue", value=-115563333.62):
    return [
        {"date": "2026-11-03", "value": value, "source": source},
        {"date": "2026-11-02", "value": -398617353.4, "source": source},
    ]


def _history(days=100, value=1_000_000.0):
    rows = []
    for i in range(days):
        month, day = (9 + i // 28), (i % 28) + 1
        date_str = f"2026-{month:02d}-{day:02d}"
        rows.append({"date": date_str, "value": value + i * 1000, "source": "tftc"})
    return [r for r in rows if r["date"] < "2026-11-02"]


def _etf(monkeypatch, rows=None, tftc_latest=None, tftc_history=None):
    monkeypatch.setenv("SOSOVALUE_API_KEY", "x")
    monkeypatch.setattr(jobs_hourly.etf, "fetch_sosovalue_rows", lambda: rows or _etf_rows())
    monkeypatch.setattr(
        jobs_hourly.etf, "fetch_tftc_latest_rows", lambda: tftc_latest or _etf_rows("tftc")
    )
    monkeypatch.setattr(
        jobs_hourly.etf, "fetch_tftc_history", lambda: tftc_history or _history()
    )


def test_etf_backfills_from_tftc_exactly_once(make_runtime, history_path, monkeypatch):
    rt = make_runtime({})
    calls = []

    def history():
        calls.append(1)
        return _history()

    _etf(monkeypatch)
    monkeypatch.setattr(jobs_hourly.etf, "fetch_tftc_history", history)
    jobs_hourly.check_spot_etf(rt)
    jobs_hourly.check_spot_etf(rt)
    assert len(calls) == 1
    assert load_etf_history(history_path)["backfilled"] is True


def test_etf_retries_backfill_when_it_failed(make_runtime, history_path, monkeypatch):
    rt = make_runtime({})
    _etf(monkeypatch)

    def fail():
        raise RuntimeError("down")

    monkeypatch.setattr(jobs_hourly.etf, "fetch_tftc_history", fail)
    jobs_hourly.check_spot_etf(rt)
    assert load_etf_history(history_path)["backfilled"] is False
    assert "etf_backfill" in rt.store.get("errors")
    monkeypatch.setattr(jobs_hourly.etf, "fetch_tftc_history", _history)
    jobs_hourly.check_spot_etf(rt)
    assert load_etf_history(history_path)["backfilled"] is True


def test_etf_accumulates_sosovalue_rows(make_runtime, history_path, monkeypatch):
    rt = make_runtime({})
    save_etf_history(history_path, {"backfilled": True, "rows": _history()})
    _etf(monkeypatch)
    jobs_hourly.check_spot_etf(rt)
    rows = load_etf_history(history_path)["rows"]
    assert rows[-1]["date"] == "2026-11-03"
    assert rows[-1]["source"] == "sosovalue"


def test_etf_sosovalue_notice_has_no_tftc_credit(make_runtime, sender, history_path, monkeypatch):
    rt = make_runtime({})
    save_etf_history(history_path, {"backfilled": True, "rows": _history()})
    _etf(monkeypatch)
    jobs_hourly.check_spot_etf(rt)
    text = sender.messages[0]
    assert "現物BTC ETF（11/3分）" in text
    assert "-$115.6M" in text and "流出" in text
    assert "🔴 弱気" in text
    assert "TFTC" not in text
    assert "SoSoValue" in text


def test_etf_falls_back_to_tftc_and_credits_it(make_runtime, sender, history_path, monkeypatch):
    rt = make_runtime({})
    save_etf_history(history_path, {"backfilled": True, "rows": _history()})
    _etf(monkeypatch)

    def fail():
        raise RuntimeError("down")

    monkeypatch.setattr(jobs_hourly.etf, "fetch_sosovalue_rows", fail)
    rt.guard("spot_etf", "現物BTC ETF", lambda: jobs_hourly.check_spot_etf(rt))
    flow = [m for m in sender.messages if "現物BTC ETF（" in m][0]
    assert "The Commoner Bitcoin ETF Tracker by TFTC" in flow
    assert "etf_sosovalue" in rt.store.get("errors")
    assert rt.store.get("flows", "spot_etf", "source") == "tftc"


def test_etf_missing_key_uses_tftc_without_error_notice(
    make_runtime, sender, history_path, monkeypatch
):
    rt = make_runtime({})
    save_etf_history(history_path, {"backfilled": True, "rows": _history()})
    _etf(monkeypatch)
    monkeypatch.delenv("SOSOVALUE_API_KEY")
    monkeypatch.setattr(jobs_hourly.etf, "fetch_sosovalue_rows", _unreachable)
    jobs_hourly.check_spot_etf(rt)
    assert len(sender.messages) == 1
    assert "TFTC" in sender.messages[0]
    assert rt.store.get("errors") == {}


def _unreachable():
    raise AssertionError("キー未設定なら呼ばない")


def test_etf_older_fallback_date_is_not_resent(make_runtime, sender, history_path, monkeypatch):
    """TFTC は SoSoValue より遅れる。古い日付で再送しない。"""
    rt = make_runtime({"flows": {"spot_etf": {"date": "2026-11-03", "value": 1.0}}})
    save_etf_history(history_path, {"backfilled": True, "rows": _history()})
    _etf(monkeypatch, rows=[{"date": "2026-11-02", "value": 1.0, "source": "tftc"}])
    jobs_hourly.check_spot_etf(rt)
    assert sender.messages == []


def test_etf_same_day_is_sent_only_once(make_runtime, sender, history_path, monkeypatch):
    rt = make_runtime({})
    save_etf_history(history_path, {"backfilled": True, "rows": _history()})
    _etf(monkeypatch)
    jobs_hourly.check_spot_etf(rt)
    jobs_hourly.check_spot_etf(rt)
    assert len(sender.messages) == 1


def test_etf_outlier_is_highlighted(make_runtime, sender, history_path, monkeypatch):
    rt = make_runtime({})
    save_etf_history(history_path, {"backfilled": True, "rows": _history()})
    _etf(monkeypatch, rows=_etf_rows(value=-900_000_000.0))
    jobs_hourly.check_spot_etf(rt)
    assert "⚠️ 普段の範囲を超えた" in sender.messages[0]
    assert rt.store.get("flows", "spot_etf", "outside") is True


def test_etf_baseline_excludes_the_new_day_and_uses_90_rows(
    make_runtime, history_path, monkeypatch
):
    rt = make_runtime({})
    rows = [
        {"date": f"2026-{1 + i // 28:02d}-{i % 28 + 1:02d}", "value": float(i), "source": "tftc"}
        for i in range(200)
    ]
    rows = [r for r in rows if r["date"] < "2026-11-02"]
    save_etf_history(history_path, {"backfilled": True, "rows": rows})
    _etf(monkeypatch)
    captured = {}
    original = jobs_hourly.rules.flow_band

    def spy(values):
        captured["values"] = list(values)
        return original(values)

    monkeypatch.setattr(jobs_hourly.rules, "flow_band", spy)
    jobs_hourly.check_spot_etf(rt)
    assert len(captured["values"]) == 90
    assert -115563333.62 not in captured["values"]


def test_etf_short_history_is_labelled(make_runtime, sender, history_path, monkeypatch):
    rt = make_runtime({})
    rows = [{"date": f"2026-10-{d:02d}", "value": float(d), "source": "s"} for d in range(1, 23)]
    save_etf_history(history_path, {"backfilled": True, "rows": rows})
    _etf(monkeypatch)
    jobs_hourly.check_spot_etf(rt)
    assert "過去23取引日" in sender.messages[0]  # 履歴22行 + 今回重ねた11/2分


def test_etf_send_failure_keeps_date_for_retry(make_runtime, sender, history_path, monkeypatch):
    rt = make_runtime({})
    save_etf_history(history_path, {"backfilled": True, "rows": _history()})
    _etf(monkeypatch)
    sender.fail_on = ["現物BTC ETF"]
    rt.guard("spot_etf", "現物BTC ETF", lambda: jobs_hourly.check_spot_etf(rt))
    assert rt.store.get("flows", "spot_etf", "date") is None
    sender.fail_on = []
    rt.guard("spot_etf", "現物BTC ETF", lambda: jobs_hourly.check_spot_etf(rt))
    assert rt.store.get("flows", "spot_etf", "date") == "2026-11-03"


def test_dry_run_leaves_history_file_alone(make_runtime, history_path, monkeypatch):
    rt = make_runtime({}, dry_run=True)
    _etf(monkeypatch)
    jobs_hourly.check_spot_etf(rt)
    assert not history_path.exists()


def _netflow(monkeypatch, latest=3837.0):
    rows = [{"date": "2026-11-03", "value": latest}] + [
        {"date": f"2026-{10 - i // 28:02d}-{28 - i % 28:02d}", "value": float(i % 7)}
        for i in range(95)
    ]
    monkeypatch.setattr(
        jobs_hourly.coinmetrics, "fetch_exchange_netflow_history", lambda: rows
    )


def test_exchange_netflow_new_day_is_sent_with_inverse_direction(make_runtime, sender, monkeypatch):
    rt = make_runtime({"flows": {"exchange_netflow": {"date": "2026-11-02", "value": 1.0}}})
    _netflow(monkeypatch)
    jobs_hourly.check_exchange_netflow(rt)
    text = sender.messages[0]
    assert "取引所BTC純流入（11/3分）" in text
    assert "+3,837 BTC" in text and "流入" in text
    assert "🔴 弱気" in text
    assert "⚠️" in text
    assert rt.store.get("flows", "exchange_netflow", "date") == "2026-11-03"


def test_exchange_netflow_same_day_is_silent(make_runtime, sender, monkeypatch):
    rt = make_runtime({"flows": {"exchange_netflow": {"date": "2026-11-03", "value": 1.0}}})
    _netflow(monkeypatch)
    jobs_hourly.check_exchange_netflow(rt)
    assert sender.messages == []


# ── 毎時の監視全体 ─────────────────────────────────────────────────────────────


def test_run_hourly_isolates_each_indicator(make_runtime, sender, monkeypatch, fake_clock):
    rt = make_runtime(
        {
            "market": {"vix": {"value": 20.0}},
            "icsa": {"date": "2026-10-31", "value": 194000.0, "release": None},
            "flows": {"exchange_netflow": {"date": "2026-11-02", "value": 1.0}},
            "calendar": {"fetched": "2026-11-13T00:00:00+00:00", "releases": {}},
        }
    )

    def fail(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(jobs_hourly.market, "fetch_market", fail)
    monkeypatch.setattr(jobs_hourly.fomc_alerts.fomc, "fetch_fomc_documents", fail)
    monkeypatch.setattr(jobs_hourly.fomc_alerts.fomc_outlook, "fetch", fail)
    monkeypatch.setattr(jobs_hourly.etf, "fetch_sosovalue_rows", fail)
    monkeypatch.setattr(jobs_hourly.etf, "fetch_tftc_latest_rows", fail)
    _netflow(monkeypatch)

    def observations(key, limit=1):
        if key == "icsa":
            return obs(("2026-11-07", 200000.0), ("2026-10-31", 194000.0))
        raise RuntimeError("down")

    monkeypatch.setattr(jobs_hourly.macro.fred, "fetch_observations", observations)
    monkeypatch.setattr(jobs_hourly.macro.fred, "fetch_release_date", lambda key: "2026-11-12")
    monkeypatch.setenv("SOSOVALUE_API_KEY", "x")
    clock = fake_clock(datetime(2026, 11, 13, 3, 23, tzinfo=timezone.utc))
    jobs_hourly.run_hourly(rt, clock)
    joined = "\n".join(sender.messages)
    assert "取引所BTC純流入（11/3分）" in joined
    assert "新規失業保険申請（11/7週）" in joined
    assert rt.store.get("icsa", "release") == "2026-11-12"
    assert "vix" in rt.store.get("errors")
    assert clock.sleeps == []  # 発表がないので待たない


def test_run_hourly_skips_backup_for_watched_release_and_waits(
    make_runtime, sender, monkeypatch, fake_clock
):
    """待ち受けの対象になった発表は、予備確認では見ない（待ち受けが拾う）。"""
    rt = make_runtime({
        "market": {"vix": {"value": 20.0}},
        "icsa": {"date": "2026-11-07", "value": 200000.0, "release": "2026-11-12"},
        "calendar": {"fetched": "2026-11-19T13:00:00+00:00", "releases": {"icsa": ["2026-11-19"]}},
    })
    calls = []
    monkeypatch.setattr(jobs_hourly.market, "fetch_market", lambda key: {"value": 20.0})
    monkeypatch.setattr(jobs_hourly.fomc_alerts, "check_documents", lambda rt: None)
    monkeypatch.setattr(jobs_hourly.fomc_alerts, "check_outlook", lambda rt, now: None)
    monkeypatch.setattr(jobs_hourly, "run_daily_flows", lambda rt: None)
    monkeypatch.setattr(jobs_hourly.macro, "backup_check",
                        lambda rt, release: calls.append(release))
    watched = []
    monkeypatch.setattr(jobs_hourly.release_watch, "run",
                        lambda rt, events, clock: watched.extend(e["key"] for e in events))
    clock = fake_clock(datetime(2026, 11, 19, 13, 23, tzinfo=timezone.utc))  # 米東部 8:23
    jobs_hourly.run_hourly(rt, clock)
    assert "icsa" not in calls
    assert set(calls) == {"cpi", "employment", "core_pce"}
    assert watched == ["icsa"]

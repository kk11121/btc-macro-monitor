import pytest

import jobs_hourly
from state_store import load_etf_history, save_etf_history


def obs(*pairs):
    return [{"date": d, "value": v} for d, v in pairs]


@pytest.fixture(autouse=True)
def fixed_release(monkeypatch):
    monkeypatch.setattr(jobs_hourly.fred, "fetch_release_date", lambda key: "2026-10-14")


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


# ── 経済指標 ───────────────────────────────────────────────────────────────────


def _months(end_value, step, count=14, year=2026, month=9):
    """月初の日付で count か月分（新しい順）を作る。"""
    rows, value = [], end_value
    for _ in range(count):
        rows.append({"date": f"{year}-{month:02d}-01", "value": round(value, 3)})
        value -= step
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return rows


def _cpi_series(monkeypatch):
    series = {
        "cpi": _months(336.3, 0.3),
        "cpi_nsa": _months(336.0, 0.3),
        "core_cpi": _months(338.9, 1.0),
        "core_cpi_nsa": _months(338.0, 0.3),
    }
    monkeypatch.setattr(jobs_hourly.fred, "fetch_observations", lambda key, limit=1: series[key])


def test_cpi_shows_target_month_release_time_and_no_forecast(make_runtime, sender, monkeypatch):
    rt = make_runtime({"cpi": {"date": "2026-08-01", "value": 336.0}})
    _cpi_series(monkeypatch)
    jobs_hourly.check_cpi(rt)
    text = sender.messages[0]
    assert "CPI（9月分）" in text
    assert "2026-09-01" not in text
    assert "予想値なし" in text
    assert "発表 10/14 21:30" in text
    assert "コア前月比" in text and "コア前年比" in text
    assert "総合前月比" in text and "総合前年比" in text
    assert "← 判定に使用" in text
    assert rt.store.get("cpi", "date") == "2026-09-01"


def test_cpi_judges_core_mom_against_previous_mom(make_runtime, sender, monkeypatch):
    rt = make_runtime({"cpi": {"date": "2026-08-01", "value": 336.0}})
    series = {
        "cpi": _months(336.3, 0.3),
        "cpi_nsa": _months(336.0, 0.3),
        "core_cpi": obs(
            ("2026-09-01", 101.0), ("2026-08-01", 100.0), ("2026-07-01", 99.8)
        ),
        "core_cpi_nsa": _months(338.0, 0.3),
    }
    monkeypatch.setattr(jobs_hourly.fred, "fetch_observations", lambda key, limit=1: series[key])
    jobs_hourly.check_cpi(rt)
    assert "🔴 弱気" in sender.messages[0]  # 前月比 +1.0% > 先月 +0.2%


def test_cpi_missing_yoy_base_month_does_not_fail(make_runtime, sender, monkeypatch):
    rt = make_runtime({"cpi": {"date": "2026-09-01", "value": 336.0}})
    gap = [r for r in _months(340.0, 0.3, month=10) if r["date"] != "2025-10-01"]
    series = {"cpi": gap, "cpi_nsa": gap, "core_cpi": gap, "core_cpi_nsa": gap}
    monkeypatch.setattr(jobs_hourly.fred, "fetch_observations", lambda key, limit=1: series[key])
    jobs_hourly.check_cpi(rt)
    assert "コア前年比 ―" in sender.messages[0]


def test_cpi_same_date_is_silent(make_runtime, sender, monkeypatch):
    rt = make_runtime({"cpi": {"date": "2026-09-01", "value": 336.0}})
    _cpi_series(monkeypatch)
    jobs_hourly.check_cpi(rt)
    assert sender.messages == []


def test_nfp_compares_monthly_change(make_runtime, sender, monkeypatch):
    rt = make_runtime({"nfp": {"date": "2026-08-01", "value": 159015}})
    rows = obs(("2026-09-01", 159044), ("2026-08-01", 159015), ("2026-07-01", 158853))
    monkeypatch.setattr(jobs_hourly.fred, "fetch_observations", lambda key, limit=1: rows)
    jobs_hourly.check_nfp(rt)
    text = sender.messages[0]
    assert "雇用統計（9月分）" in text
    assert "+29千人" in text and "+162千人" in text
    assert "🟢 強気" in text  # 増減が先月より縮小
    assert rt.store.get("nfp", "value") == 159044


def test_unrate_compares_level_with_previous_notice(make_runtime, sender, monkeypatch):
    rt = make_runtime({"unrate": {"date": "2026-08-01", "value": 4.2}})
    monkeypatch.setattr(
        jobs_hourly.fred, "fetch_observations", lambda key, limit=1: obs(("2026-09-01", 4.3))
    )
    jobs_hourly.check_unrate(rt)
    assert "失業率（9月分）" in sender.messages[0]
    assert "🟢 強気" in sender.messages[0]
    assert "4.3%" in sender.messages[0] and "4.2%" in sender.messages[0]


def test_icsa_compares_level_and_uses_week_label(make_runtime, sender, monkeypatch):
    rt = make_runtime({"icsa": {"date": "2026-10-17", "value": 199000}})
    monkeypatch.setattr(
        jobs_hourly.fred, "fetch_observations", lambda key, limit=1: obs(("2026-10-24", 194000))
    )
    jobs_hourly.check_icsa(rt)
    assert "新規失業保険申請（10/24週）" in sender.messages[0]
    assert "🟡 中立" in sender.messages[0]  # ±5,000件の幅に収まる
    assert "194,000件" in sender.messages[0]


def test_core_pce_first_run_is_recorded_silently(make_runtime, sender, monkeypatch):
    rt = make_runtime({})
    rows = obs(("2026-09-01", 128.0), ("2026-08-01", 127.6), ("2026-07-01", 127.4))
    monkeypatch.setattr(jobs_hourly.fred, "fetch_observations", lambda key, limit=1: rows)
    jobs_hourly.check_core_pce(rt)
    assert sender.messages == []
    assert rt.store.get("core_pce", "date") == "2026-09-01"


def test_core_pce_new_release_compares_mom(make_runtime, sender, monkeypatch):
    rt = make_runtime({"core_pce": {"date": "2026-08-01", "value": 127.6}})
    rows = obs(("2026-09-01", 128.0), ("2026-08-01", 127.6), ("2026-07-01", 127.4))
    monkeypatch.setattr(jobs_hourly.fred, "fetch_observations", lambda key, limit=1: rows)
    jobs_hourly.check_core_pce(rt)
    assert "コアPCE（9月分）" in sender.messages[0]
    assert "🔴 弱気" in sender.messages[0]
    assert "予想値なし" in sender.messages[0]


def test_release_date_failure_falls_back_to_detected_time(make_runtime, sender, monkeypatch):
    rt = make_runtime({"unrate": {"date": "2026-08-01", "value": 4.2}})

    def fail(key):
        raise RuntimeError("down")

    monkeypatch.setattr(jobs_hourly.fred, "fetch_release_date", fail)
    monkeypatch.setattr(
        jobs_hourly.fred, "fetch_observations", lambda key, limit=1: obs(("2026-09-01", 4.3))
    )
    jobs_hourly.check_unrate(rt)
    assert "検知 " in sender.messages[0]
    assert "発表 " not in sender.messages[0]


# ── FOMC ──────────────────────────────────────────────────────────────────────

STATEMENT = {"url": "https://x/monetary20261209a.htm", "date": "2026-12-09"}
MINUTES = {"url": "https://x/fomcminutes20261028.htm", "date": "2026-10-28"}


def _fomc(monkeypatch, statement=STATEMENT, minutes=MINUTES, content="本文"):
    monkeypatch.setattr(
        jobs_hourly.fomc,
        "fetch_fomc_documents",
        lambda: {"statement": statement, "minutes": minutes},
    )
    monkeypatch.setattr(jobs_hourly.fomc, "fetch_content", lambda url: content)


def _state_fomc(statement_date="2026-10-28", minutes_date="2026-09-16"):
    return {
        "fomc": {
            "statement": {"url": "https://x/old", "date": statement_date},
            "minutes": {"url": "https://x/old", "date": minutes_date},
        }
    }


def test_statement_is_judged_from_the_text(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(minutes_date="2026-10-28"))
    _fomc(
        monkeypatch,
        content="The Committee decided to lower the target range for the federal funds rate "
        "by 1/4 percentage point to 3-1/2 to 3-3/4 percent.",
    )
    jobs_hourly.check_fomc(rt)
    assert len(sender.messages) == 1
    text = sender.messages[0]
    assert "FOMC声明" in text
    assert "🟢 強気" in text
    assert "3.50〜3.75%" in text
    assert "予想値なし" in text
    assert rt.store.get("fomc", "statement", "date") == "2026-12-09"


def test_statement_unreadable_is_unknown_but_still_sent(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(minutes_date="2026-10-28"))
    _fomc(monkeypatch, content="something else")
    jobs_hourly.check_fomc(rt)
    assert "⚪ 判定不可" in sender.messages[0]
    assert rt.store.get("fomc", "statement", "date") == "2026-12-09"


def test_minutes_are_sent_separately_from_statement(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(statement_date="2026-12-09"))
    _fomc(monkeypatch)
    monkeypatch.setattr(
        jobs_hourly.summarizer,
        "summarize_minutes",
        lambda content, date: {"judgment": "弱気", "emoji": "🔴", "summary": "要約"},
    )
    jobs_hourly.check_fomc(rt)
    assert len(sender.messages) == 1
    assert "FOMC議事録（10/28会合）" in sender.messages[0]
    assert "要約" in sender.messages[0]
    assert rt.store.get("fomc", "minutes", "date") == "2026-10-28"


def test_minutes_summary_failure_shows_unknown(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(statement_date="2026-12-09"))
    _fomc(monkeypatch, content=None)
    jobs_hourly.check_fomc(rt)
    assert "⚪ 判定不可" in sender.messages[0]


def _fetch_content_fails(monkeypatch):
    def fail(url):
        raise RuntimeError("503")

    monkeypatch.setattr(jobs_hourly.fomc, "fetch_content", fail)


def test_statement_page_failure_does_not_advance_and_retries(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(minutes_date="2026-10-28"))
    _fomc(monkeypatch)
    _fetch_content_fails(monkeypatch)
    jobs_hourly.check_fomc(rt)
    assert rt.store.get("fomc", "statement", "date") == "2026-10-28"
    assert "fomc_statement" in rt.store.get("errors")
    assert not any("判定不可" in m for m in sender.messages)


def test_minutes_page_failure_does_not_advance_and_retries(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(statement_date="2026-12-09"))
    _fomc(monkeypatch)
    _fetch_content_fails(monkeypatch)
    jobs_hourly.check_fomc(rt)
    assert rt.store.get("fomc", "minutes", "date") == "2026-09-16"
    assert "fomc_minutes" in rt.store.get("errors")


def test_minutes_first_run_is_recorded_silently(make_runtime, sender, monkeypatch):
    state = _state_fomc(statement_date="2026-12-09")
    state["fomc"]["minutes"] = {"url": None, "date": None}
    rt = make_runtime(state)
    _fomc(monkeypatch)

    def must_not_run(content, date):
        raise AssertionError("初回の議事録は要約しない")

    monkeypatch.setattr(jobs_hourly.summarizer, "summarize_minutes", must_not_run)
    jobs_hourly.check_fomc(rt)
    assert sender.messages == []
    assert rt.store.get("fomc", "minutes", "date") == "2026-10-28"


def test_statement_send_failure_does_not_block_minutes(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc())
    _fomc(monkeypatch)
    monkeypatch.setattr(
        jobs_hourly.summarizer,
        "summarize_minutes",
        lambda content, date: {"judgment": "中立", "emoji": "🟡", "summary": "要約"},
    )
    sender.fail_on = ["FOMC声明"]
    jobs_hourly.check_fomc(rt)
    assert rt.store.get("fomc", "statement", "date") == "2026-10-28"  # 次回やり直す
    assert rt.store.get("fomc", "minutes", "date") == "2026-10-28"
    assert rt.notification_failed is True


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


def test_run_hourly_isolates_each_indicator(make_runtime, sender, monkeypatch):
    rt = make_runtime(
        {
            "market": {"vix": {"value": 20.0}},
            "unrate": {"date": "2026-08-01", "value": 4.2},
            "flows": {"exchange_netflow": {"date": "2026-11-02", "value": 1.0}},
        }
    )

    def fail(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(jobs_hourly.market, "fetch_market", fail)
    monkeypatch.setattr(jobs_hourly.fomc, "fetch_fomc_documents", fail)
    monkeypatch.setattr(jobs_hourly.etf, "fetch_sosovalue_rows", fail)
    monkeypatch.setattr(jobs_hourly.etf, "fetch_tftc_latest_rows", fail)
    _netflow(monkeypatch)

    def observations(key, limit=1):
        if key == "unrate":
            return obs(("2026-09-01", 4.3))
        raise RuntimeError("down")

    monkeypatch.setattr(jobs_hourly.fred, "fetch_observations", observations)
    monkeypatch.setenv("SOSOVALUE_API_KEY", "x")
    jobs_hourly.run_hourly(rt)
    joined = "\n".join(sender.messages)
    assert "取引所BTC純流入（11/3分）" in joined
    assert "失業率（9月分）" in joined
    assert rt.store.get("unrate", "date") == "2026-09-01"
    assert "vix" in rt.store.get("errors")

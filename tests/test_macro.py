"""経済指標の実績・判定・通知文（待ち受けと予備確認で共通）。"""

import pytest

import macro


def obs(*pairs):
    return [{"date": d, "value": v} for d, v in pairs]


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


@pytest.fixture(autouse=True)
def fixed_release(monkeypatch):
    monkeypatch.setattr(macro.fred, "fetch_release_date", lambda key: "2026-10-14")


def _fred(monkeypatch, series):
    monkeypatch.setattr(macro.fred, "fetch_observations", lambda key, limit=1: series[key])


def _cpi_series(monkeypatch, month=9, count=14):
    _fred(monkeypatch, {
        "cpi": _months(336.3, 0.3, count, month=month),
        "cpi_nsa": _months(336.0, 0.3, count, month=month),
        "core_cpi": _months(338.9, 1.0, count, month=month),
        "core_cpi_nsa": _months(338.0, 0.3, count, month=month),
    })


# ── CPI（予備確認・予想なし）──────────────────────────────────────────────────


def test_cpi_without_forecast_compares_previous_and_says_so(make_runtime, sender, monkeypatch):
    rt = make_runtime({"cpi": {"date": "2026-08-01", "value": 336.0}})
    _cpi_series(monkeypatch)
    macro.backup_check(rt, "cpi")
    text = sender.messages[0]
    assert "CPI（9月分）" in text
    assert "2026-09-01" not in text
    assert "予想値なし（前回値と比較）" in text
    assert "発表 10/14 21:30（日本時間）" in text  # 10月14日は夏時間
    assert "コア前月比" in text and "コア前年比" in text
    assert "総合前月比" in text and "総合前年比" in text
    assert "← 判定に使用" in text
    assert "予想値の出所" not in text
    assert rt.store.get("cpi", "date") == "2026-09-01"
    assert rt.store.get("cpi", "release") == "2026-10-14"


def test_cpi_with_stored_forecasts_compares_to_both(make_runtime, sender, monkeypatch):
    """2つの予想が一致した回（2026年11月10日 CPI のコア前月比 予想 0.2%・0.2%）の形。"""
    forecasts = {"release_date": "2026-10-14", "values": {
        "core_cpi_mom": {"sosovalue": 0.2, "forexfactory": 0.2},
        "core_cpi_yoy": {"sosovalue": 2.5, "forexfactory": None},
        "cpi_mom": {"sosovalue": 0.1, "forexfactory": 0.1},
        "cpi_yoy": {"sosovalue": None, "forexfactory": None},
    }}
    rt = make_runtime({"cpi": {"date": "2026-08-01", "value": 336.0},
                       "forecasts": {"cpi": forecasts}})
    _fred(monkeypatch, {
        "cpi": obs(("2026-09-01", 100.1), ("2026-08-01", 100.0)),
        "cpi_nsa": _months(336.0, 0.3),
        "core_cpi": obs(("2026-09-01", 100.3), ("2026-08-01", 100.0)),
        "core_cpi_nsa": _months(338.0, 0.3),
    })
    macro.backup_check(rt, "cpi")
    text = sender.messages[0]
    assert "🔴 弱気" in text
    assert "コア前月比 +0.3%（予想 +0.2%）上振れ ← 判定に使用" in text
    assert "総合前月比 +0.1%（予想 +0.1%）一致" in text
    assert "総合前年比" in text and "（先月" in text  # 予想がない数字は前回と並べる
    assert "予想値の出所：SoSoValue・ForexFactory" in text
    assert "予想値なし" not in text


def test_cpi_yoy_opposite_direction_is_noted(make_runtime, sender, monkeypatch):
    forecasts = {"release_date": "2026-10-14", "values": {
        "core_cpi_mom": {"sosovalue": 0.2, "forexfactory": None},
        "core_cpi_yoy": {"sosovalue": 9.9, "forexfactory": None},  # 前年比は下振れ（強気）
    }}
    rt = make_runtime({"cpi": {"date": "2026-08-01", "value": 1.0},
                       "forecasts": {"cpi": forecasts}})
    _fred(monkeypatch, {
        "cpi": obs(("2026-09-01", 100.1), ("2026-08-01", 100.0)),
        "cpi_nsa": _months(336.0, 0.3),
        "core_cpi": obs(("2026-09-01", 100.3), ("2026-08-01", 100.0)),
        "core_cpi_nsa": _months(338.0, 0.3),
    })
    macro.backup_check(rt, "cpi")
    assert "（前年比は逆方向）" in sender.messages[0]


def test_cpi_forecasts_for_another_release_are_ignored(make_runtime, sender, monkeypatch):
    stale = {"release_date": "2026-09-11", "values": {
        "core_cpi_mom": {"sosovalue": 0.2, "forexfactory": 0.2}}}
    rt = make_runtime({"cpi": {"date": "2026-08-01", "value": 336.0},
                       "forecasts": {"cpi": stale}})
    _cpi_series(monkeypatch)
    macro.backup_check(rt, "cpi")
    assert "予想値なし" in sender.messages[0]


def test_cpi_missing_yoy_base_month_is_labeled(make_runtime, sender, monkeypatch):
    rt = make_runtime({"cpi": {"date": "2026-09-01", "value": 336.0}})
    gap = [r for r in _months(340.0, 0.3, month=10) if r["date"] != "2025-10-01"]
    _fred(monkeypatch, {"cpi": gap, "cpi_nsa": gap, "core_cpi": gap, "core_cpi_nsa": gap})
    macro.backup_check(rt, "cpi")
    text = sender.messages[0]
    assert "コア前年比 —（基準月のデータなし）（先月 +" in text
    assert "総合前年比 —（基準月のデータなし）（先月 +" in text
    assert "コア前月比 +" in text


def test_cpi_previous_month_yoy_with_missing_base_is_labeled(make_runtime, sender, monkeypatch):
    rt = make_runtime({"cpi": {"date": "2026-10-01", "value": 336.0}})
    months = _months(340.0, 0.3, count=macro.CPI_ROWS, month=11)
    gap = [r for r in months if r["date"] != "2025-10-01"]
    _fred(monkeypatch, {"cpi": gap, "cpi_nsa": gap, "core_cpi": gap, "core_cpi_nsa": gap})
    macro.backup_check(rt, "cpi")
    text = sender.messages[0]
    assert "コア前年比 +" in text
    assert "（先月 —（基準月のデータなし））" in text


def test_cpi_yoy_outside_fetched_range_is_not_labeled_as_missing(
    make_runtime, sender, monkeypatch
):
    rt = make_runtime({"cpi": {"date": "2026-08-01", "value": 336.0}})
    short = _months(340.0, 0.3, count=3)
    _fred(monkeypatch, {"cpi": short, "cpi_nsa": short, "core_cpi": short, "core_cpi_nsa": short})
    macro.backup_check(rt, "cpi")
    text = sender.messages[0]
    assert "コア前年比 ―（先月 ―）" in text
    assert "基準月のデータなし" not in text


def test_cpi_same_period_is_silent(make_runtime, sender, monkeypatch):
    rt = make_runtime({"cpi": {"date": "2026-09-01", "value": 336.0}})
    _cpi_series(monkeypatch)
    macro.backup_check(rt, "cpi")
    assert sender.messages == []


def test_cpi_partial_series_update_waits(make_runtime, sender, monkeypatch):
    """季節調整ありだけ先に更新された途中の状態では、まだ通知しない。"""
    rt = make_runtime({"cpi": {"date": "2026-08-01", "value": 336.0}})
    _fred(monkeypatch, {
        "cpi": _months(336.3, 0.3), "core_cpi": _months(338.9, 1.0),
        "cpi_nsa": _months(336.0, 0.3, month=8), "core_cpi_nsa": _months(338.0, 0.3, month=8),
    })
    macro.backup_check(rt, "cpi")
    assert sender.messages == []
    assert rt.store.get("cpi", "date") == "2026-08-01"


def test_backup_skips_release_already_notified_by_watch(make_runtime, sender, monkeypatch):
    """待ち受けで通知済みの発表日なら、FRED の新しい期間は記録だけする。"""
    rt = make_runtime({"cpi": {"date": "2026-08-01", "value": 336.0, "release": "2026-10-14"}})
    _cpi_series(monkeypatch)
    macro.backup_check(rt, "cpi")
    assert sender.messages == []
    assert rt.store.get("cpi", "date") == "2026-09-01"
    assert rt.store.get("cpi", "release") == "2026-10-14"


def test_backup_release_date_failure_raises_for_guard(make_runtime, sender, monkeypatch):
    rt = make_runtime({"cpi": {"date": "2026-08-01", "value": 336.0}})
    _cpi_series(monkeypatch)

    def fail(key):
        raise RuntimeError("down")

    monkeypatch.setattr(macro.fred, "fetch_release_date", fail)
    with pytest.raises(RuntimeError):
        macro.backup_check(rt, "cpi")
    assert rt.store.get("cpi", "date") == "2026-08-01"


# ── 雇用統計 ──────────────────────────────────────────────────────────────────


def _employment(monkeypatch, unrate=(("2026-09-01", 4.3), ("2026-08-01", 4.2))):
    _fred(monkeypatch, {
        "nfp": obs(("2026-09-01", 159044), ("2026-08-01", 159015), ("2026-07-01", 158853)),
        "unrate": obs(*unrate),
    })


def test_employment_without_forecast_combines_nfp_and_unrate(make_runtime, sender, monkeypatch):
    rt = make_runtime({"nfp": {"date": "2026-08-01", "value": 159015},
                       "unrate": {"date": "2026-08-01", "value": 4.2}})
    _employment(monkeypatch)
    macro.backup_check(rt, "employment")
    assert len(sender.messages) == 1
    text = sender.messages[0]
    assert "雇用統計（9月分）" in text
    assert "NFP +2.9万人（先月 +16.2万人） ← 判定に使用" in text
    assert "失業率 4.3%（先月 4.2%）" in text
    assert "🟢 強気" in text  # 増減が先月より縮小
    assert rt.store.get("nfp", "value") == 159044
    assert rt.store.get("unrate", "value") == 4.3
    assert rt.store.get("unrate", "release") == "2026-10-14"


def test_employment_split_forecasts_use_range(make_runtime, sender, monkeypatch):
    """
    予想が割れた回（2026年11月6日 NFP：実績 16.5万人、予想 6.3万人（11月4日時点）と 65K）。
    範囲は 3.8万〜9.0万人で、上振れの弱気。
    """
    forecasts = {"release_date": "2026-10-14", "values": {
        "nfp": {"sosovalue": 63.0, "forexfactory": 65.0},
        "unrate": {"sosovalue": None, "forexfactory": 4.2},
    }}
    rt = make_runtime({"nfp": {"date": "2026-09-01", "value": 159128},
                       "unrate": {"date": "2026-09-01", "value": 4.2},
                       "forecasts": {"employment": forecasts}})
    _fred(monkeypatch, {
        "nfp": obs(("2026-10-01", 159293), ("2026-09-01", 159128)),
        "unrate": obs(("2026-10-01", 4.1), ("2026-09-01", 4.2)),
    })
    macro.backup_check(rt, "employment")
    text = sender.messages[0]
    assert "🔴 弱気" in text
    assert "NFP +16.5万人（予想 6.3万〜6.5万人）上振れ ← 判定に使用" in text
    assert "失業率 4.1%（予想 4.2%）下振れ" in text
    assert "失業率は逆方向" not in text  # 失業率の下振れも弱気で同じ向き
    assert "予想値の出所：SoSoValue・ForexFactory" in text


def test_employment_unrate_opposite_is_noted(make_runtime, sender, monkeypatch):
    forecasts = {"release_date": "2026-10-14", "values": {
        "nfp": {"sosovalue": 63.0, "forexfactory": 65.0},
        "unrate": {"sosovalue": None, "forexfactory": 4.0},
    }}
    rt = make_runtime({"nfp": {"date": "2026-09-01", "value": 159128},
                       "forecasts": {"employment": forecasts}})
    _fred(monkeypatch, {
        "nfp": obs(("2026-10-01", 159293), ("2026-09-01", 159128)),
        "unrate": obs(("2026-10-01", 4.1), ("2026-09-01", 4.2)),
    })
    macro.backup_check(rt, "employment")
    assert "（失業率は逆方向）" in sender.messages[0]


# ── 新規失業保険申請・コアPCE ───────────────────────────────────────────────────


def test_icsa_from_fred_uses_week_label_and_neutral_band(make_runtime, sender, monkeypatch):
    rt = make_runtime({"icsa": {"date": "2026-10-17", "value": 199000}})
    _fred(monkeypatch, {"icsa": obs(("2026-10-24", 194000), ("2026-10-17", 199000))})
    macro.backup_check(rt, "icsa")
    text = sender.messages[0]
    assert "新規失業保険申請（10/24週）" in text
    assert "🟡 中立" in text  # ±5,000件の幅に収まる
    assert "申請件数 19.4万件（前回 19.9万件）" in text


def test_icsa_from_sosovalue_actual_with_both_forecasts():
    """2026年11月12日：実績 20.0万件、予想 19.4万件（SoSoValue）と 194K（ForexFactory）。"""
    snapshot = {"release": "icsa", "source": "sosovalue", "actual": 200000.0,
                "previous": 189000.0}
    values = {"icsa": {"sosovalue": 194000.0, "forexfactory": 194000.0}}
    report = macro.build_report(snapshot, values, "2026-11-12")
    assert report["verdict"].label == "強気"  # 申請が予想より多いのは強気
    assert "申請件数 20.0万件（予想 19.4万件）上振れ" in report["message"]
    assert "新規失業保険申請（11/7週）" in report["message"]
    assert "発表 11/12 22:30（日本時間）" in report["message"]  # 11月は冬時間
    assert report["state"] == {}  # 対象期間は FRED の更新で記録する


def test_core_pce_from_sosovalue_matching_forecast():
    """2026年10月29日：コアPCE前月比 0.3%、予想 0.3%（一致で中立）。"""
    snapshot = {"release": "core_pce", "source": "sosovalue", "actual": 0.3, "previous": 0.2}
    values = {"core_pce_mom": {"sosovalue": 0.3, "forexfactory": None}}
    report = macro.build_report(snapshot, values, "2026-10-29")
    assert report["verdict"].label == "中立"
    assert "コアPCE前月比 +0.3%（予想 +0.3%）一致" in report["message"]
    assert "コアPCE（9月分）" in report["message"]
    assert "予想値の出所：SoSoValue" in report["message"]


def test_core_pce_first_run_is_recorded_silently(make_runtime, sender, monkeypatch):
    rt = make_runtime({})
    _fred(monkeypatch, {"core_pce": obs(("2026-09-01", 128.0), ("2026-08-01", 127.6),
                                        ("2026-07-01", 127.4))})
    macro.backup_check(rt, "core_pce")
    assert sender.messages == []
    assert rt.store.get("core_pce", "date") == "2026-09-01"


def test_core_pce_from_fred_compares_mom(make_runtime, sender, monkeypatch):
    rt = make_runtime({"core_pce": {"date": "2026-08-01", "value": 127.6}})
    _fred(monkeypatch, {"core_pce": obs(("2026-09-01", 128.0), ("2026-08-01", 127.6),
                                        ("2026-07-01", 127.4))})
    macro.backup_check(rt, "core_pce")
    assert "コアPCE（9月分）" in sender.messages[0]
    assert "🔴 弱気" in sender.messages[0]
    assert "予想値なし" in sender.messages[0]


# ── スナップショット ────────────────────────────────────────────────────────────


def test_sosovalue_snapshot_reads_actual_on_release_date(monkeypatch):
    rows = [{"date": "2026-11-12", "actual": "200", "forecast": "194", "previous": "189"}]
    monkeypatch.setattr(macro.sosovalue, "fetch_event_history", lambda name: rows)
    snapshot = macro.sosovalue_snapshot("icsa", "2026-11-12")
    assert snapshot["actual"] == 200000.0 and snapshot["previous"] == 189000.0


def test_sosovalue_snapshot_none_before_release(monkeypatch):
    rows = [{"date": "2026-11-19", "actual": "", "forecast": "199", "previous": "200"}]
    monkeypatch.setattr(macro.sosovalue, "fetch_event_history", lambda name: rows)
    assert macro.sosovalue_snapshot("icsa", "2026-11-19") is None


def test_bls_snapshot_maps_series_to_keys(monkeypatch):
    def fake(ids, today=None):
        return {sid: [{"date": "2026-10-01", "value": 1.0}] for sid in ids}

    monkeypatch.setattr(macro.bls, "fetch_series", fake)
    snapshot = macro.bls_snapshot("employment")
    assert set(snapshot["rows"]) == {"nfp", "unrate"}
    assert macro.period_of(snapshot) == "2026-10-01"


def test_is_new_compares_period_with_state():
    snap = {"release": "icsa", "source": "fred", "rows": {"icsa": obs(("2026-11-07", 1.0))}}
    assert macro.is_new({"icsa": {"date": "2026-10-31"}}, snap) is True
    assert macro.is_new({"icsa": {"date": "2026-11-07"}}, snap) is False
    assert macro.is_new({}, None) is False


@pytest.mark.parametrize(
    ("release_date", "week_end"),
    [("2026-11-12", "2026-11-07"), ("2026-11-25", "2026-11-21")],  # 木曜と、祝日週の水曜
)
def test_week_ending_before(release_date, week_end):
    assert macro.week_ending_before(release_date) == week_end

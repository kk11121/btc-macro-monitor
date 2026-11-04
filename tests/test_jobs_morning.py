import pytest

import jobs_morning

BASE_STATE = {
    "market": {"vix": {"value": 20.0}},
    "liquidity": {
        "m2sl": {"date": "2026-08-01", "value": 23342.8},
        "walcl": {"date": "2026-07-22", "value": 6747378},
    },
    "summary": {
        "last_date": "2026-11-04",
        "values": {
            "dxy": 102.5,
            "us10y": 4.95,
            "nasdaq": 26000.0,
            "vix": 17.8,
            "usdt": 180.6e9,
            "usdc": 71.0e9,
            "btc_exchange": 2_690_000.0,
            "fear_greed": {"value": 53, "zone": "neutral", "date": "2026-11-04"},
            "funding_rate": 0.0001,
        },
    },
    "flows": {
        "spot_etf": {
            "date": "2026-11-03",
            "value": 137_300_000.0,
            "source": "sosovalue",
            "outside": False,
            "band": {"low": -5e8, "high": 6e8, "samples": 90},
        },
        "exchange_netflow": {
            "date": "2026-11-03",
            "value": 3837.0,
            "outside": False,
            "band": {"low": -9e3, "high": 9e3, "samples": 90},
        },
    },
}

MARKET = {"dxy": 102.21, "us10y": 4.99, "nasdaq": 26155.0, "vix": 16.9}


@pytest.fixture
def healthy(monkeypatch):
    monkeypatch.setattr(jobs_morning, "run_daily_flows", lambda rt: None)
    monkeypatch.setattr(
        jobs_morning.market, "fetch_market", lambda key: {"key": key, "value": MARKET[key]}
    )
    monkeypatch.setattr(
        jobs_morning.stablecoin,
        "fetch_stablecoin",
        lambda key: {"value": {"usdt": 181.0e9, "usdc": 71.1e9}[key]},
    )
    monkeypatch.setattr(jobs_morning.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        jobs_morning.coinmetrics, "fetch_exchange_supply", lambda: {"value": 2_687_000.0}
    )
    monkeypatch.setattr(
        jobs_morning.glassnode,
        "fetch_fear_greed",
        lambda: {"value": 51.0, "zone": "neutral", "zone_label": "中立", "date": "2026-11-05"},
    )
    monkeypatch.setattr(jobs_morning.glassnode, "fetch_funding_rate", lambda: {"value": 0.0001})

    def observations(key, limit=1):
        if key == "m2sl":
            return [{"date": "2026-08-01", "value": 23342.8}]
        return [
            {"date": "2026-10-28", "value": 6_800_000.0},
            {"date": "2026-10-21", "value": 6_780_000.0},
        ]

    monkeypatch.setattr(jobs_morning.fred, "fetch_observations", observations)


def _run(make_runtime, state=None):
    rt = make_runtime(state or BASE_STATE)
    jobs_morning.run_morning(rt, today="2026-11-05")
    return rt


def test_morning_lists_all_sections(make_runtime, sender, healthy):
    _run(make_runtime)
    assert len(sender.messages) == 1
    text = sender.messages[0]
    assert "朝のまとめ 11月5日(木)" in text
    assert "■ マクロ・市場（前回まとめ比）" in text
    assert "DXY 102.21 ↓0.3% 🟢" in text
    assert "米10年債 4.99% ↑4bp 🔴" in text
    assert "NASDAQ 26,155 ↑0.6% 🟢" in text
    assert "VIX 16.9 ↓5.1% 🟢" in text
    assert "■ 暗号資産" in text
    assert "現物BTC ETF +$137.3M（11/3分）流入 🟢 普段の範囲内" in text
    assert "取引所BTC純流入 +3,837 BTC（11/3分）流入 🔴 普段の範囲内" in text
    assert "恐怖&強欲 51 中立（前回 53）" in text
    assert "Funding Rate（OKX）+0.010%" in text
    assert "USDT 181.0B ↑0.2% 🟢" in text
    assert "取引所BTC残高 2.687M ↓0.1% 🟢" in text
    assert "■ 稼働状況：すべて正常" in text


def test_morning_does_not_carry_phase_b_sections(make_runtime, sender, healthy):
    _run(make_runtime)
    assert "今日・明日の予定" not in sender.messages[0]
    assert "金利見通し" not in sender.messages[0]


def test_morning_saves_new_baselines(make_runtime, healthy):
    rt = _run(make_runtime)
    values = rt.store.get("summary", "values")
    assert values["dxy"] == 102.21
    assert values["fear_greed"]["value"] == 51.0
    assert rt.store.get("summary", "last_date") == "2026-11-05"


def test_liquidity_new_release_is_saved_even_when_change_is_tiny(make_runtime, sender, healthy):
    """閾値未満でも新しい発表は必ず保存し、前回の発表と比べる。"""
    rt = _run(make_runtime)
    assert "FRBバランスシート" in sender.messages[0]
    assert "↑0.3%" in sender.messages[0]  # 10/21 → 10/28 の比較（7/22 の保存値とは比べない）
    assert "10/28時点" in sender.messages[0]
    assert rt.store.get("liquidity", "walcl") == {"date": "2026-10-28", "value": 6_800_000.0}


def test_liquidity_without_new_release_is_hidden(make_runtime, sender, healthy):
    _run(make_runtime)
    assert "M2" not in sender.messages[0]


def test_liquidity_uses_month_label(make_runtime, sender, healthy, monkeypatch):
    state = {
        **BASE_STATE,
        "liquidity": {
            "m2sl": {"date": "2026-07-01", "value": 23300.0},
            "walcl": {"date": "2026-10-28", "value": 6_800_000.0},
        },
    }

    def observations(key, limit=1):
        if key == "m2sl":
            return [
                {"date": "2026-08-01", "value": 23342.8},
                {"date": "2026-07-01", "value": 23300.0},
            ]
        return [{"date": "2026-10-28", "value": 6_800_000.0}]

    monkeypatch.setattr(jobs_morning.fred, "fetch_observations", observations)
    _run(make_runtime, state)
    assert "8月分" in sender.messages[0]
    assert "FRBバランスシート" not in sender.messages[0]


def test_failed_indicator_shows_in_row_and_status_and_keeps_baseline(
    make_runtime, sender, healthy, monkeypatch
):
    def market(key):
        if key == "dxy":
            raise RuntimeError("down")
        return {"value": MARKET[key]}

    monkeypatch.setattr(jobs_morning.market, "fetch_market", market)
    rt = _run(make_runtime)
    summary = [m for m in sender.messages if "朝のまとめ" in m][0]
    assert "DXY 取得失敗" in summary
    assert "NASDAQ 26,155" in summary
    assert "■ 稼働状況：失敗中 1件" in summary
    assert "DXY" in summary.split("■ 稼働状況")[1]
    assert rt.store.get("summary", "values")["dxy"] == 102.5


def test_continuing_failure_stays_in_status_without_new_notice(
    make_runtime, sender, healthy, monkeypatch
):
    state = {
        **BASE_STATE,
        "errors": {"dxy": {"label": "DXY", "since": "2026-11-03T10:00:00+00:00"}},
    }

    def market(key):
        if key == "dxy":
            raise RuntimeError("down")
        return {"value": MARKET[key]}

    monkeypatch.setattr(jobs_morning.market, "fetch_market", market)
    _run(make_runtime, state)
    assert len(sender.messages) == 1
    assert "11/3から" in sender.messages[0]


def test_send_failure_keeps_baselines_and_liquidity(make_runtime, sender, healthy):
    sender.fail_on = ["朝のまとめ"]
    rt = make_runtime(BASE_STATE)
    rt.guard("morning", "朝のまとめ", lambda: jobs_morning.run_morning(rt, today="2026-11-05"))
    assert rt.notification_failed is True
    assert rt.store.get("summary", "values")["dxy"] == 102.5
    assert rt.store.get("liquidity", "walcl", "date") == "2026-07-22"


def test_first_run_without_baseline_marks_first(make_runtime, sender, healthy):
    state = {**BASE_STATE, "summary": {"last_date": None, "values": {}}}
    _run(make_runtime, state)
    assert "DXY 102.21（初回）" in sender.messages[0]


def test_funding_extreme_is_noted(make_runtime, sender, healthy, monkeypatch):
    monkeypatch.setattr(jobs_morning.glassnode, "fetch_funding_rate", lambda: {"value": 0.0004})
    _run(make_runtime)
    assert "Funding Rate（OKX）+0.040% ⚠️" in sender.messages[0]


def test_funding_within_threshold_has_no_note(make_runtime, sender, healthy, monkeypatch):
    monkeypatch.setattr(jobs_morning.glassnode, "fetch_funding_rate", lambda: {"value": -0.0003})
    _run(make_runtime)
    assert "Funding Rate（OKX）-0.030%\n" in sender.messages[0]


def test_extreme_fear_is_noted(make_runtime, sender, healthy, monkeypatch):
    monkeypatch.setattr(
        jobs_morning.glassnode,
        "fetch_fear_greed",
        lambda: {"value": 12.0, "zone": "extreme_fear", "zone_label": "極度の恐怖", "date": "d"},
    )
    _run(make_runtime)
    assert "恐怖&強欲 12 極度の恐怖（前回 53）⚠️" in sender.messages[0]


def test_tftc_sourced_etf_row_carries_credit(make_runtime, sender, healthy):
    state = {
        **BASE_STATE,
        "flows": {
            **BASE_STATE["flows"],
            "spot_etf": {**BASE_STATE["flows"]["spot_etf"], "source": "tftc"},
        },
    }
    _run(make_runtime, state)
    assert "The Commoner Bitcoin ETF Tracker by TFTC" in sender.messages[0]


def test_flow_row_outside_band_is_highlighted(make_runtime, sender, healthy):
    state = {
        **BASE_STATE,
        "flows": {
            **BASE_STATE["flows"],
            "spot_etf": {**BASE_STATE["flows"]["spot_etf"], "outside": True},
        },
    }
    _run(make_runtime, state)
    assert "⚠️ 普段の範囲を超えた" in sender.messages[0]


def test_flow_row_without_data_is_marked(make_runtime, sender, healthy):
    state = {
        **BASE_STATE,
        "flows": {
            **BASE_STATE["flows"],
            "spot_etf": {"date": None, "value": None, "source": None},
        },
    }
    _run(make_runtime, state)
    assert "現物BTC ETF データなし" in sender.messages[0]


def test_migrated_flow_without_band_still_renders(make_runtime, sender, healthy):
    state = {
        **BASE_STATE,
        "flows": {
            **BASE_STATE["flows"],
            "exchange_netflow": {"date": "2026-11-03", "value": 3837.0},
        },
    }
    _run(make_runtime, state)
    assert "取引所BTC純流入 +3,837 BTC（11/3分）流入 🔴" in sender.messages[0]

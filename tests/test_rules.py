import pytest

import config
import rules
from rules import BEARISH, BULLISH, NEUTRAL, UNKNOWN


@pytest.mark.parametrize(
    "key,current,previous,expected",
    [
        ("dxy", 102.0, 101.0, BEARISH),
        ("dxy", 100.0, 101.0, BULLISH),
        ("us10y", 4.5, 4.4, BEARISH),
        ("nasdaq", 26000, 25000, BULLISH),
        ("nasdaq", 24000, 25000, BEARISH),
        ("vix", 22, 20, BEARISH),
        ("usdt", 2, 1, BULLISH),
        ("usdc", 1, 2, BEARISH),
        ("m2sl", 2, 1, BULLISH),
        ("walcl", 1, 2, BEARISH),
        ("btc_exchange", 2, 1, BEARISH),
        ("btc_exchange", 1, 2, BULLISH),
        ("core_cpi", 0.4, 0.3, BEARISH),
        ("core_pce", 0.2, 0.3, BULLISH),
        ("nfp", 90, 29, BEARISH),
        ("unrate", 4.3, 4.2, BULLISH),
        ("icsa", 210000, 200000, BULLISH),
    ],
)
def test_judge_change_follows_configured_direction(key, current, previous, expected):
    assert rules.judge_change(key, current, previous) == expected


def test_judge_change_same_value_is_neutral():
    assert rules.judge_change("core_cpi", 0.3, 0.3) == NEUTRAL


@pytest.mark.parametrize(
    "key,current,previous",
    [("nfp", 100, 76), ("nfp", 100, 125), ("icsa", 204000, 200000), ("icsa", 195000, 200000)],
)
def test_judge_change_within_band_is_neutral(key, current, previous):
    assert rules.judge_change(key, current, previous) == NEUTRAL


def test_judge_change_just_outside_band_is_judged():
    assert rules.judge_change("icsa", 205001, 200000) == BULLISH


@pytest.mark.parametrize("current,previous", [(None, 1.0), (1.0, None), (None, None)])
def test_judge_change_missing_value_is_unknown(current, previous):
    assert rules.judge_change("dxy", current, previous) == UNKNOWN


def test_direction_can_be_flipped_from_config(monkeypatch):
    monkeypatch.setitem(config.DIRECTION, "dxy", config.RISE_BULLISH)
    assert rules.judge_change("dxy", 102, 101) == BULLISH


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("spot_etf", 100.0, BULLISH),
        ("spot_etf", -100.0, BEARISH),
        ("exchange_netflow", 100.0, BEARISH),
        ("exchange_netflow", -100.0, BULLISH),
        ("spot_etf", 0.0, NEUTRAL),
    ],
)
def test_judge_flow_uses_sign_not_change(key, value, expected):
    assert rules.judge_flow(key, value) == expected


def test_flow_band_matches_mean_and_two_sample_sigma():
    band = rules.flow_band([1.0, 2.0, 3.0, 4.0, 5.0] * 4)
    assert band["mean"] == pytest.approx(3.0)
    assert band["low"] == pytest.approx(3.0 - 2 * 1.4509525, abs=1e-6)
    assert band["high"] == pytest.approx(3.0 + 2 * 1.4509525, abs=1e-6)
    assert band["samples"] == 20


def test_flow_band_returns_none_with_too_few_samples():
    assert rules.flow_band([1.0] * (config.FLOW_MIN_SAMPLES - 1)) is None


def test_is_outside_band():
    band = {"low": -10.0, "high": 10.0}
    assert rules.is_outside_band(11, band) is True
    assert rules.is_outside_band(-11, band) is True
    assert rules.is_outside_band(10, band) is False
    assert rules.is_outside_band(5, None) is None


@pytest.mark.parametrize(
    "current,baseline,expected",
    [
        (22.0, None, "init"),
        (22.0, 20.0, "alert"),   # +10% かつ 20超
        (21.9, 20.0, "hold"),    # +9.5%
        (19.9, 18.0, "hold"),    # +10.5% だが 20 以下
        (20.0, 18.0, "hold"),    # ちょうど 20 は「超」ではない
        (20.1, 18.0, "alert"),
        (18.0, 20.0, "rebase"),  # -10% は通知せず基準だけ下げる
        (18.1, 20.0, "hold"),    # -9.5%
    ],
)
def test_evaluate_vix(current, baseline, expected):
    assert rules.evaluate_vix(current, baseline)["action"] == expected


def test_vix_rebase_then_new_spike_alerts():
    assert rules.evaluate_vix(18.0, 30.0)["action"] == "rebase"
    assert rules.evaluate_vix(20.5, 18.0)["action"] == "alert"


OBS = [
    {"date": "2026-09-01", "value": 338.896},
    {"date": "2026-08-01", "value": 337.765},
    {"date": "2026-07-01", "value": 336.789},
]


def test_month_over_month_rounds_to_one_decimal():
    assert rules.month_over_month(OBS, "2026-09-01") == 0.3
    assert rules.month_over_month(OBS, "2026-08-01") == 0.3


def test_month_over_month_none_when_previous_month_is_missing():
    rows = [{"date": "2025-11-01", "value": 331.043}, {"date": "2025-09-01", "value": 330.418}]
    assert rules.month_over_month(rows, "2025-11-01") is None


def test_year_over_year():
    rows = [{"date": "2026-09-01", "value": 103.7}, {"date": "2025-09-01", "value": 100.0}]
    assert rules.year_over_year(rows, "2026-09-01") == 3.7


def test_year_over_year_none_when_base_month_is_missing():
    """2025年10月は FRED で欠測。次回の10月分 CPI で実際に起きる。"""
    rows = [{"date": "2026-10-01", "value": 340.0}, {"date": "2025-09-01", "value": 330.0}]
    assert rules.year_over_year(rows, "2026-10-01") is None


def test_is_missing_month_inside_fetched_range():
    rows = [{"date": "2026-10-01", "value": 340.0}, {"date": "2025-09-01", "value": 330.0}]
    assert rules.is_missing_month(rows, "2025-10-01") is True


def test_is_missing_month_false_when_present():
    rows = [{"date": "2026-10-01", "value": 340.0}, {"date": "2025-10-01", "value": 330.0}]
    assert rules.is_missing_month(rows, "2025-10-01") is False


def test_is_missing_month_false_outside_fetched_range():
    """取得した範囲より古い月は、欠測かどうか分からない。"""
    rows = [{"date": "2026-10-01", "value": 340.0}, {"date": "2026-09-01", "value": 339.0}]
    assert rules.is_missing_month(rows, "2025-10-01") is False
    assert rules.is_missing_month([], "2025-10-01") is False


def test_monthly_change_for_payrolls():
    rows = [
        {"date": "2026-09-01", "value": 159044},
        {"date": "2026-08-01", "value": 159015},
        {"date": "2026-07-01", "value": 158853},
    ]
    assert rules.monthly_change(rows, "2026-09-01") == 29
    assert rules.monthly_change(rows, "2026-08-01") == 162


def test_months_before_crosses_year():
    assert rules.months_before("2026-01-01", 1) == "2025-12-01"
    assert rules.months_before("2026-10-01", 12) == "2025-10-01"


REAL_MAINTAIN = (
    "approved the following statement for release by a 10 - 2 vote: The Committee decided to "
    "maintain the target range for the federal funds rate at 3-3/4 to 4 percent, in support of "
    "the dual mandate. Voting against the monetary policy action were Lisa D. Cook and Beth M. "
    "Hammack, who preferred to raise the target range for the federal funds rate by 1/4 "
    "percentage point at this meeting."
)


def test_parse_maintain_ignores_dissent_sentence():
    decision = rules.parse_fomc_decision(REAL_MAINTAIN)
    assert decision == {"action": "maintain", "lower": 3.75, "upper": 4.0}


def test_parse_lower_with_step():
    text = (
        "the Committee decided to lower the target range for the federal funds rate by 1/4 "
        "percentage point to 3-1/2 to 3-3/4 percent."
    )
    assert rules.parse_fomc_decision(text) == {"action": "lower", "lower": 3.5, "upper": 3.75}


def test_parse_raise_across_line_breaks():
    text = (
        "the Committee\ndecided to raise\nthe target range for the federal funds rate\n"
        "to 4-1/4\nto 4-1/2 percent"
    )
    assert rules.parse_fomc_decision(text) == {"action": "raise", "lower": 4.25, "upper": 4.5}


@pytest.mark.parametrize("text", ["", None, "The Committee said something else entirely."])
def test_parse_returns_none_when_unreadable(text):
    assert rules.parse_fomc_decision(text) is None


@pytest.mark.parametrize(
    "decision,expected",
    [
        ({"action": "lower"}, BULLISH),
        ({"action": "raise"}, BEARISH),
        ({"action": "maintain"}, NEUTRAL),
        (None, UNKNOWN),
    ],
)
def test_judge_fomc(decision, expected):
    assert rules.judge_fomc(decision) == expected

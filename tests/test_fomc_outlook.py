"""利下げ確率の計算と、金利見通しの通知・声明の判定。"""

from datetime import datetime, timezone

import pytest

import fomc_alerts
from fetchers import fomc_outlook

TARGET = {"lower": 3.75, "upper": 4.00, "date": "2026-11-12"}

# 2026年11月13日の実データ（次回会合の配列はレンジより短い）
RAW = {
    "rate_ranges": ["375-400", "400-425", "425-450", "450-475", "475-500", "500-525",
                    "525-550", "550-575"],
    "meetings": [
        {"meeting_date": "2026-12-09", "probabilities": ["0.0", "84.5", "15.5", "0.0", "0.0",
                                                         "0.0"]},
        {"meeting_date": "2027-01-27", "probabilities": ["0.0", "22.4", "66.2", "11.4", "0.0",
                                                         "0.0", "0.0", "0.0"]},
        {"meeting_date": "2027-03-17", "probabilities": ["0.0", "6.1", "34.4", "51.2", "8.3",
                                                         "0.0", "0.0", "0.0"]},
    ],
    "as_of": 1794537474324,
}


def test_normalize_pads_short_arrays_with_zero():
    normalized = fomc_outlook.normalize(RAW)
    first = normalized["meetings"][0]
    assert first["date"] == "2026-12-09"
    assert len(first["probs"]) == 8
    assert first["probs"][1] == (4.00, 4.25, 84.5)
    assert first["probs"][-1] == (5.50, 5.75, 0.0)
    assert normalized["as_of"].startswith("2026-11-13")


def test_normalize_treats_null_as_zero():
    raw = {**RAW, "meetings": [{"meeting_date": "2026-12-09",
                                "probabilities": ["50.0", "50.0", None]}]}
    assert fomc_outlook.normalize(raw)["meetings"][0]["probs"][2][2] == 0.0


def test_summarize_real_data_prices_a_hike():
    outlook = fomc_outlook.summarize(fomc_outlook.normalize(RAW), TARGET, "2026-11-13")
    assert outlook["meeting"] == "2026-12-09"
    assert outlook["cut_prob"] == 0.0
    assert outlook["hike_prob"] == 100.0
    assert outlook["expected_rate"] == pytest.approx(4.16375, abs=1e-4)
    assert outlook["horizon_meeting"] == "2027-03-17"
    assert outlook["priced_bp"] > 0  # 3会合先までに利上げを織り込む


def test_meeting_stats_cut_probability():
    probs = [(3.25, 3.50, 30.0), (3.50, 3.75, 40.0), (3.75, 4.00, 30.0)]
    stats = fomc_outlook.meeting_stats(probs, TARGET)
    assert stats["cut_prob"] == 70.0
    assert stats["hike_prob"] == 0.0
    assert stats["expected_rate"] == pytest.approx(3.625, abs=1e-4)


def test_summarize_skips_past_meetings():
    outlook = fomc_outlook.summarize(fomc_outlook.normalize(RAW), TARGET, "2026-12-10")
    assert outlook["meeting"] == "2027-01-27"


# ── 毎時の確認と通知 ──────────────────────────────────────────────────────────

NOW = datetime(2026, 11, 13, 3, 23, tzinfo=timezone.utc)


def _outlook(cut=0.0, hike=84.5, expected=4.04, meeting="2026-12-09", priced=30.0):
    return {"meeting": meeting, "cut_prob": cut, "hike_prob": hike, "expected_rate": expected,
            "horizon_meeting": "2027-03-17", "priced_bp": priced,
            "as_of": "2026-11-13T02:00:00+00:00"}


def _serve(monkeypatch, outlook):
    monkeypatch.setattr(fomc_alerts, "fetch_outlook", lambda rt, now: outlook)


def test_first_outlook_is_recorded_silently(make_runtime, sender, monkeypatch):
    rt = make_runtime({})
    _serve(monkeypatch, _outlook())
    fomc_alerts.check_outlook(rt, NOW)
    assert sender.messages == []
    assert rt.store.get("fomc_outlook", "notified")["hike_prob"] == 84.5


def test_hike_probability_move_alerts_even_when_cut_is_flat(make_runtime, sender, monkeypatch):
    """利上げが織り込まれていて利下げ確率が0のままでも、利上げ確率の15pt以上の変化で通知する。"""
    rt = make_runtime({"fomc_outlook": {"notified": _outlook(hike=60.0, expected=3.99)}})
    _serve(monkeypatch, _outlook(hike=80.0, expected=4.04))
    fomc_alerts.check_outlook(rt, NOW)
    text = sender.messages[0]
    assert "FOMC金利見通し" in text
    assert "🔴 弱気" in text  # 期待金利が上がった
    assert "利上げ確率 80%（前回通知 60%）+20pt" in text
    assert "利下げ確率 0%（前回通知 0%）+0pt" in text
    assert "出所：SoSoValue（11/13 11:00 時点）" in text
    assert rt.store.get("fomc_outlook", "notified")["hike_prob"] == 80.0


def test_cut_probability_move_alerts_bullish(make_runtime, sender, monkeypatch):
    rt = make_runtime({"fomc_outlook": {"notified": _outlook(cut=60.0, hike=0.0,
                                                             expected=3.70)}})
    _serve(monkeypatch, _outlook(cut=78.0, hike=0.0, expected=3.66))
    fomc_alerts.check_outlook(rt, NOW)
    assert "🟢 強気" in sender.messages[0]
    assert "利下げ確率 78%（前回通知 60%）+18pt" in sender.messages[0]


def test_small_move_is_silent_and_keeps_baseline(make_runtime, sender, monkeypatch):
    rt = make_runtime({"fomc_outlook": {"notified": _outlook(hike=70.0)}})
    _serve(monkeypatch, _outlook(hike=84.5))
    fomc_alerts.check_outlook(rt, NOW)
    assert sender.messages == []
    assert rt.store.get("fomc_outlook", "notified")["hike_prob"] == 70.0


def test_meeting_switch_rebases_without_alert(make_runtime, sender, monkeypatch):
    rt = make_runtime({"fomc_outlook": {"notified": _outlook(hike=10.0)}})
    _serve(monkeypatch, _outlook(hike=90.0, meeting="2027-01-27"))
    fomc_alerts.check_outlook(rt, NOW)
    assert sender.messages == []
    assert rt.store.get("fomc_outlook", "notified")["meeting"] == "2027-01-27"


def test_send_failure_keeps_baseline(make_runtime, sender, monkeypatch):
    rt = make_runtime({"fomc_outlook": {"notified": _outlook(hike=50.0)}})
    _serve(monkeypatch, _outlook(hike=90.0))
    sender.fail_on = ["FOMC金利見通し"]
    rt.guard("fomc_outlook", "x", lambda: fomc_alerts.check_outlook(rt, NOW))
    assert rt.store.get("fomc_outlook", "notified")["hike_prob"] == 50.0


def test_pre_meeting_is_kept_only_before_the_statement(make_runtime):
    rt = make_runtime({})
    before = datetime(2026, 12, 9, 18, 23, tzinfo=timezone.utc)  # 米東部 13:23
    after = datetime(2026, 12, 9, 19, 23, tzinfo=timezone.utc)   # 米東部 14:23
    fomc_alerts.remember_pre_meeting(rt, _outlook(expected=4.10), "2026-12-09", before)
    fomc_alerts.remember_pre_meeting(rt, _outlook(expected=9.99), "2026-12-09", after)
    assert rt.store.get("fomc_outlook", "pre_meeting", "2026-12-09")["expected_rate"] == 4.10


def test_morning_lines_show_daily_change(make_runtime, monkeypatch):
    rt = make_runtime({"fomc_outlook": {"morning": _outlook(hike=80.0, expected=4.02,
                                                            priced=25.0)}})
    _serve(monkeypatch, _outlook(hike=84.5, expected=4.04, priced=30.0))
    lines = fomc_alerts.morning_lines(rt, NOW, "2026-11-13")
    assert lines[0] == "■ 金利見通し（次回FOMC 12/9）"
    assert lines[1] == " 利下げ確率 0%（前日比 +0pt） ／ 利上げ確率 84%（前日比 +4pt） 🔴"
    assert lines[2] == " 3会合先までの織り込み +30bp（前日比 +5bp）"
    assert rt.store.get("fomc_outlook", "morning")["hike_prob"] == 84.5


def test_morning_lines_store_pre_meeting_on_meeting_day(make_runtime, monkeypatch):
    """会合当日（日本時間）の朝のまとめで、会合前の期待金利を控える。"""
    rt = make_runtime({})
    _serve(monkeypatch, _outlook(expected=4.05))
    morning = datetime(2026, 12, 8, 22, 47, tzinfo=timezone.utc)  # 日本時間 12/9 7:47
    fomc_alerts.morning_lines(rt, morning, "2026-12-09")
    assert rt.store.get("fomc_outlook", "pre_meeting", "2026-12-09")["expected_rate"] == 4.05


# ── 声明の判定（会合前の期待金利との差）────────────────────────────────────────


def test_statement_is_judged_against_pre_meeting_expectation(make_runtime, sender, monkeypatch):
    rt = make_runtime({
        "fomc": {"statement": {"url": "u", "date": "2026-10-28"},
                 "minutes": {"url": "u", "date": "2026-10-28"}},
        "fomc_outlook": {"pre_meeting": {"2026-12-09": {
            "cut_prob": 70.0, "hike_prob": 0.0, "expected_rate": 3.45, "as_of": "x"}}},
    })
    monkeypatch.setattr(fomc_alerts.fomc, "fetch_fomc_documents", lambda: {
        "statement": {"url": "https://x/monetary20261209a.htm", "date": "2026-12-09"},
        "minutes": {"url": "u", "date": "2026-10-28"}})
    monkeypatch.setattr(
        fomc_alerts.fomc, "fetch_content",
        lambda url: "The Committee decided to maintain the target range for the federal funds "
                    "rate at 3-1/2 to 3-3/4 percent.")
    fomc_alerts.check_documents(rt)
    text = sender.messages[0]
    assert "FOMC声明（12/9）🔴 弱気" in text
    assert "決定：据え置き（誘導目標 3.50〜3.75%）" in text
    assert "会合前の織り込み：利下げ確率 70%、期待金利 3.45%" in text
    assert "決定との差 +17.5bp → 市場の予想より引き締め的" in text


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 12, 9, 18, 59, tzinfo=timezone.utc), "2026-12-09"),  # 米東部 13:59
        (datetime(2026, 12, 9, 19, 0, tzinfo=timezone.utc), "2026-12-10"),   # 声明の時刻から外す
        (datetime(2026, 12, 8, 22, 47, tzinfo=timezone.utc), "2026-12-09"),  # 会合当日の朝のまとめ
    ],
)
def test_upcoming_from_drops_the_meeting_after_the_statement(moment, expected):
    assert fomc_alerts.upcoming_from(moment) == expected


def test_no_outlook_alert_right_after_the_decision(make_runtime, sender, monkeypatch):
    """決定後は次の会合に基準を移すだけで、確率の崩れを通知しない。"""
    rt = make_runtime({"fomc_outlook": {"notified": _outlook(hike=84.5)}})
    raw = {**RAW, "meetings": [
        {"meeting_date": "2026-12-09", "probabilities": ["0.0", "100.0"]},
        {"meeting_date": "2027-01-27", "probabilities": ["0.0", "50.0", "50.0"]}]}
    monkeypatch.setattr(fomc_alerts.fomc_outlook, "fetch", lambda: fomc_outlook.normalize(raw))
    monkeypatch.setattr(fomc_alerts.fred, "fetch_fed_target", lambda: TARGET)
    after = datetime(2026, 12, 9, 19, 23, tzinfo=timezone.utc)  # 米東部 14:23
    fomc_alerts.check_outlook(rt, after)
    assert sender.messages == []
    assert rt.store.get("fomc_outlook", "notified")["meeting"] == "2027-01-27"

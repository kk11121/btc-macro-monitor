"""発表日程と、毎時の監視の中の発表待ち受け。"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import config
import release_calendar
import release_watch

EASTERN = ZoneInfo("America/New_York")
JST = ZoneInfo("Asia/Tokyo")
WINTER_DAY = "2026-12-10"  # 冬時間（EST、UTC-5）
SUMMER_DAY = "2027-06-10"  # 夏時間（EDT、UTC-4）


def _state(day, extra_releases=None, meetings=()):
    releases = {"cpi": [day], **(extra_releases or {})}
    return {"calendar": {"fetched": "x", "releases": releases, "fomc_meetings": list(meetings)},
            "fomc": {"statement": {"url": None, "date": None},
                     "minutes": {"url": None, "date": None}}}


def _eastern(day, hour, minute):
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=EASTERN)


# ── 発表日程 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("day", "utc_hour"), [(WINTER_DAY, 13), (SUMMER_DAY, 12)])
def test_release_time_follows_daylight_saving(day, utc_hour):
    event = release_calendar.all_events(_state(day))[0]
    assert event["at"].astimezone(timezone.utc).hour == utc_hour
    assert event["at"].astimezone(timezone.utc).minute == 30


def test_fomc_statement_and_minutes_dates():
    events = release_calendar.all_events(_state("2026-12-10", meetings=["2026-12-09"]))
    by_key = {e["key"]: e for e in events}
    assert by_key["fomc_statement"]["date_et"] == "2026-12-09"
    assert by_key["fomc_statement"]["at"].astimezone(EASTERN).hour == 14
    assert by_key["fomc_minutes"]["date_et"] == "2026-12-30"  # 3週間後


def test_last_statement_date_counts_as_meeting():
    state = _state("2026-12-10")
    state["fomc"]["statement"]["date"] = "2026-10-28"
    assert release_calendar.meeting_dates(state) == ["2026-10-28"]


def test_calendar_staleness():
    now = datetime(2026, 11, 13, 3, tzinfo=timezone.utc)
    assert release_calendar.is_stale(None, now)
    assert release_calendar.is_stale({"fetched": "2026-11-12T00:00:00+00:00"}, now)
    assert not release_calendar.is_stale({"fetched": "2026-11-13T00:00:00+00:00"}, now)


def test_refresh_keeps_old_dates_for_a_failed_release(make_runtime, sender, monkeypatch):
    rt = make_runtime({"calendar": {"fetched": None, "releases": {"icsa": ["2026-11-12"]}}})

    def fetch(release_id, limit):
        if release_id == 180:
            raise RuntimeError("down")
        return ["2026-12-10"]

    monkeypatch.setattr(release_calendar.fred, "fetch_release_dates", fetch)
    release_calendar.refresh(rt, datetime(2026, 11, 13, tzinfo=timezone.utc))
    releases = rt.store.get("calendar", "releases")
    assert releases["icsa"] == ["2026-11-12"]
    assert releases["cpi"] == ["2026-12-10"]
    assert "calendar_icsa" in rt.store.get("errors")


def test_remember_meetings_merges_and_keeps_recent(make_runtime):
    rt = make_runtime({"calendar": {"fomc_meetings": ["2026-10-28"]}})
    release_calendar.remember_meetings(rt, ["2026-12-09", "2027-01-27"])
    assert rt.store.get("calendar", "fomc_meetings") == ["2026-10-28", "2026-12-09",
                                                         "2027-01-27"]


# ── 待ち受けの対象（夏時間と冬時間）──────────────────────────────────────────────


def _hourly_run_before(release_at):
    """発表の直前の「毎時23分」の回（米東部でも毎時23分になる）。"""
    run = release_at.replace(minute=config.HOURLY_RUN_MINUTE, second=0)
    return run if run <= release_at else run - timedelta(hours=1)


SLOTS = [(8, 30), (10, 0), (14, 0)]


@pytest.mark.parametrize("day", [WINTER_DAY, SUMMER_DAY])
@pytest.mark.parametrize(("hour", "minute"), SLOTS)
def test_the_23_minute_run_before_each_release_watches_it(monkeypatch, day, hour, minute):
    """8:30・10:00・14:00 の発表を、直前の23分の回が拾い、その1つ前の回は拾わない。"""
    monkeypatch.setitem(config.RELEASES, "test_slot",
                        {"release_id": 0, "label": "テスト", "time_et": (hour, minute)})
    state = {"calendar": {"releases": {"test_slot": [day]}}}
    release_at = _eastern(day, hour, minute)
    run = _hourly_run_before(release_at)
    due = release_watch.due_events(state, run.astimezone(timezone.utc))
    assert [e["key"] for e in due] == ["test_slot"]
    earlier = release_watch.due_events(state, (run - timedelta(hours=1)).astimezone(timezone.utc))
    assert earlier == []


def test_late_run_within_45_minutes_still_watches():
    state = _state(WINTER_DAY)
    late = _eastern(WINTER_DAY, 9, 10).astimezone(timezone.utc)
    assert [e["key"] for e in release_watch.due_events(state, late)] == ["cpi"]
    too_late = _eastern(WINTER_DAY, 9, 20).astimezone(timezone.utc)
    assert release_watch.due_events(state, too_late) == []


def test_notified_release_is_not_watched_again():
    state = _state(WINTER_DAY)
    state["cpi"] = {"date": "2026-11-01", "release": WINTER_DAY}
    run = _eastern(WINTER_DAY, 8, 23).astimezone(timezone.utc)
    assert release_watch.due_events(state, run) == []


# ── 朝のまとめと重ならないこと ──────────────────────────────────────────────────


def _jst_minutes(moment):
    local = moment.astimezone(JST)
    return local.hour * 60 + local.minute


@pytest.mark.parametrize("day", [WINTER_DAY, SUMMER_DAY])
@pytest.mark.parametrize(("hour", "minute"), SLOTS)
def test_long_watch_never_overlaps_morning_summary(day, hour, minute):
    """
    待ち受けを含む毎時の回が動きうる最長の時間帯（起動〜発表＋45分＋いつもの確認の余裕）が、
    朝のまとめの起動（日本時間 7:47）から最長20分の実行時間と重ならない。
    """
    release_at = _eastern(day, hour, minute)
    start = _hourly_run_before(release_at)
    end = release_watch.longest_run_end(start, release_at)
    morning_start = config.MORNING_RUN_JST[0] * 60 + config.MORNING_RUN_JST[1]
    morning_end = morning_start + config.MORNING_MAX_MINUTES
    # 日本時間の分に直す。日をまたぐ場合は翌日分として24時間を足す
    s = _jst_minutes(start)
    e = _jst_minutes(end) + (24 * 60 if end.astimezone(JST).date() > start.astimezone(JST).date()
                              else 0)
    for offset in (0, 24 * 60):  # 当日と翌日の朝のまとめ
        assert e <= morning_start + offset or s >= morning_end + offset, (
            f"{day} {hour}:{minute:02d}：待ち受け {start.astimezone(JST):%H:%M}〜"
            f"{end.astimezone(JST):%H:%M} が朝のまとめと重なる")


@pytest.mark.parametrize("day", [WINTER_DAY, SUMMER_DAY])
def test_long_watch_hours_stay_between_20_23_and_04_55_jst(day):
    """待ち受けで長く動くのは日本時間でおよそ 20:23〜翌4:55 の間だけ。"""
    for hour, minute in SLOTS:
        release_at = _eastern(day, hour, minute)
        start = _hourly_run_before(release_at)
        end = release_watch.longest_run_end(start, release_at)
        assert _jst_minutes(start) >= 20 * 60 + 23 or _jst_minutes(start) <= 4 * 60
        assert _jst_minutes(end) <= 4 * 60 + 55 or _jst_minutes(end) >= 20 * 60 + 23


# ── 待ち受けの動き ──────────────────────────────────────────────────────────────


def _icsa_state():
    return {"icsa": {"date": "2026-11-07", "value": 200000.0, "release": "2026-11-12"},
            "calendar": {"releases": {"icsa": ["2026-11-19"]}}}


def _icsa_event(state, now):
    return release_watch.due_events(state, now)


@pytest.fixture
def no_forecast_fetch(monkeypatch):
    monkeypatch.setattr(release_watch.forecasts.forexfactory, "fetch_week", lambda: [
        {"title": "Unemployment Claims", "date_et": "2026-11-19", "forecast": "199K",
         "previous": "200K"}])


def test_watch_waits_then_polls_every_30_seconds_and_records_latency(
    make_runtime, sender, monkeypatch, fake_clock, no_forecast_fetch, tmp_path
):
    rt = make_runtime(_icsa_state())
    rt.latency_log_path = tmp_path / "latency.jsonl"
    rt.dry_run = False
    attempts = []

    def history(name):
        attempts.append(1)
        actual = "205" if len(attempts) >= 4 else ""  # 予想の取得1回 + 3回目の取得で出る
        return [{"date": "2026-11-19", "actual": actual, "forecast": "199", "previous": "200"}]

    monkeypatch.setattr(release_watch.forecasts.sosovalue, "fetch_event_history", history)
    monkeypatch.setattr(release_watch.macro.sosovalue, "fetch_event_history", history)
    monkeypatch.setattr(release_watch.macro.fred, "fetch_observations",
                        lambda key, limit=1: [{"date": "2026-11-07", "value": 200000.0}])
    monkeypatch.setenv("SOSOVALUE_API_KEY", "x")
    clock = fake_clock(_eastern("2026-11-19", 8, 23).astimezone(timezone.utc))
    release_watch.run(rt, _icsa_event(rt.store.state, clock.now()), clock)

    text = sender.messages[0]
    assert "新規失業保険申請（11/14週）" in text
    assert "申請件数 20.5万件（予想 19.9万件）上振れ" in text
    assert "予想値の出所：SoSoValue・ForexFactory" in text
    assert clock.sleeps[0] == 4 * 60  # 8:23 → 8:27（予想値を取り直す）
    assert clock.sleeps[1] == 3 * 60  # 8:27 → 8:30
    assert clock.sleeps[2:] == [30, 30]
    assert rt.store.get("icsa", "release") == "2026-11-19"
    assert rt.store.get("forecasts", "icsa")["values"]["icsa"] == {
        "sosovalue": 199000.0, "forexfactory": 199000.0}
    latency = rt.latency_log_path.read_text(encoding="utf-8")
    assert '"seconds": 60' in latency and '"source": "sosovalue"' in latency


def test_watch_falls_back_to_fred_when_sosovalue_has_nothing(
    make_runtime, sender, monkeypatch, fake_clock, no_forecast_fetch
):
    rt = make_runtime(_icsa_state())
    monkeypatch.setattr(release_watch.forecasts.sosovalue, "fetch_event_history", lambda n: [
        {"date": "2026-11-19", "actual": "", "forecast": "199", "previous": "200"}])
    monkeypatch.setattr(release_watch.macro.sosovalue, "fetch_event_history", lambda n: [
        {"date": "2026-11-19", "actual": "", "forecast": "199", "previous": "200"}])
    monkeypatch.setattr(release_watch.macro.fred, "fetch_observations", lambda key, limit=1: [
        {"date": "2026-11-14", "value": 203000.0}, {"date": "2026-11-07", "value": 200000.0}])
    monkeypatch.setenv("SOSOVALUE_API_KEY", "x")
    clock = fake_clock(_eastern("2026-11-19", 8, 29).astimezone(timezone.utc))
    release_watch.run(rt, _icsa_event(rt.store.state, clock.now()), clock)
    assert "申請件数 20.3万件（予想 19.9万件）一致" in sender.messages[0]
    assert rt.store.get("icsa", "date") == "2026-11-14"


def test_watch_gives_up_after_45_minutes_with_one_error_notice(
    make_runtime, sender, monkeypatch, fake_clock, no_forecast_fetch
):
    rt = make_runtime(_icsa_state())
    empty = [{"date": "2026-11-19", "actual": "", "forecast": "199", "previous": "200"}]
    monkeypatch.setattr(release_watch.forecasts.sosovalue, "fetch_event_history", lambda n: empty)
    monkeypatch.setattr(release_watch.macro.sosovalue, "fetch_event_history", lambda n: empty)
    monkeypatch.setattr(release_watch.macro.fred, "fetch_observations",
                        lambda key, limit=1: [{"date": "2026-11-07", "value": 200000.0}])
    monkeypatch.setenv("SOSOVALUE_API_KEY", "x")
    clock = fake_clock(_eastern("2026-11-19", 8, 23).astimezone(timezone.utc))
    release_watch.run(rt, _icsa_event(rt.store.state, clock.now()), clock)
    assert clock.now() >= _eastern("2026-11-19", 9, 15).astimezone(timezone.utc)
    assert len(sender.messages) == 1
    assert "新規失業保険申請の発表待ち受けの取得に失敗しました" in sender.messages[0]
    assert "watch_icsa" in rt.store.get("errors")
    assert rt.store.get("icsa", "release") == "2026-11-12"  # 次の回の予備確認が拾う


def test_bls_is_skipped_without_key_and_fred_is_used(
    make_runtime, sender, monkeypatch, fake_clock
):
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    monkeypatch.setattr(release_watch.forecasts.forexfactory, "fetch_week", lambda: [])
    monkeypatch.setattr(release_watch.forecasts.sosovalue, "fetch_event_history", lambda n: [
        {"date": "2026-12-04", "actual": "", "forecast": "50", "previous": "165"}])

    def bls_must_not_run(*args, **kwargs):
        raise AssertionError("キーがないときは BLS を呼ばない")

    monkeypatch.setattr(release_watch.macro.bls, "fetch_series", bls_must_not_run)
    monkeypatch.setattr(release_watch.macro.fred, "fetch_observations", lambda key, limit=1: {
        "nfp": [{"date": "2026-11-01", "value": 159343.0}, {"date": "2026-10-01",
                                                            "value": 159293.0}],
        "unrate": [{"date": "2026-11-01", "value": 4.1}, {"date": "2026-10-01", "value": 4.1}],
    }[key])
    rt = make_runtime({"nfp": {"date": "2026-10-01", "value": 159293.0},
                       "calendar": {"releases": {"employment": ["2026-12-04"]}}})
    clock = fake_clock(_eastern("2026-12-04", 8, 31).astimezone(timezone.utc))
    release_watch.run(rt, release_watch.due_events(rt.store.state, clock.now()), clock)
    assert "NFP +5.0万人（予想 5.0万人）一致" in sender.messages[0]


def test_bls_failure_notifies_once_and_uses_fred(make_runtime, sender, monkeypatch, fake_clock):
    monkeypatch.setenv("BLS_API_KEY", "bad")
    monkeypatch.setattr(release_watch.forecasts.forexfactory, "fetch_week", lambda: [])
    monkeypatch.setattr(release_watch.forecasts.sosovalue, "fetch_event_history",
                        lambda n: [{"date": "2026-01-01", "actual": "1", "forecast": "1",
                                    "previous": "1"}])

    def invalid(*args, **kwargs):
        raise RuntimeError("BLS status REQUEST_NOT_PROCESSED")

    monkeypatch.setattr(release_watch.macro.bls, "fetch_series", invalid)
    monkeypatch.setattr(release_watch.macro.fred, "fetch_observations", lambda key, limit=1: {
        "nfp": [{"date": "2026-11-01", "value": 159343.0}, {"date": "2026-10-01",
                                                            "value": 159293.0}],
        "unrate": [{"date": "2026-11-01", "value": 4.1}, {"date": "2026-10-01", "value": 4.1}],
    }[key])
    rt = make_runtime({"nfp": {"date": "2026-10-01", "value": 159293.0},
                       "calendar": {"releases": {"employment": ["2026-12-04"]}}})
    clock = fake_clock(_eastern("2026-12-04", 8, 31).astimezone(timezone.utc))
    release_watch.run(rt, release_watch.due_events(rt.store.state, clock.now()), clock)
    joined = "\n".join(sender.messages)
    assert joined.count("BLS（CPI・雇用統計の実績）の取得に失敗しました") == 1
    assert "雇用統計（11月分）" in joined
    assert "bls" in rt.store.get("errors")


def test_watch_send_failure_leaves_release_for_backup(
    make_runtime, sender, monkeypatch, fake_clock, no_forecast_fetch
):
    rt = make_runtime(_icsa_state())
    rows = [{"date": "2026-11-19", "actual": "205", "forecast": "199", "previous": "200"}]
    monkeypatch.setattr(release_watch.forecasts.sosovalue, "fetch_event_history", lambda n: rows)
    monkeypatch.setattr(release_watch.macro.sosovalue, "fetch_event_history", lambda n: rows)
    monkeypatch.setenv("SOSOVALUE_API_KEY", "x")
    sender.fail_on = ["新規失業保険申請（"]
    clock = fake_clock(_eastern("2026-11-19", 8, 31).astimezone(timezone.utc))
    release_watch.run(rt, _icsa_event(rt.store.state, clock.now()), clock)
    assert rt.notification_failed is True
    assert rt.store.get("icsa", "release") == "2026-11-12"
    assert clock.sleeps == [0, 0] or all(s <= 0 for s in clock.sleeps)  # 打ち切って待たない


def test_watch_fomc_statement_at_14_00(make_runtime, sender, monkeypatch, fake_clock):
    rt = make_runtime({
        "fomc": {"statement": {"url": "u", "date": "2026-10-28"},
                 "minutes": {"url": "u", "date": "2026-10-28"}},
        "calendar": {"releases": {}, "fomc_meetings": ["2026-12-09"]},
    })
    monkeypatch.setattr(release_watch.fomc_alerts, "fetch_outlook", lambda rt, now: {
        "meeting": "2026-12-09", "cut_prob": 0.0, "hike_prob": 80.0, "expected_rate": 4.05,
        "as_of": "2026-12-09T18:40:00+00:00"})
    calls = []

    def documents():
        calls.append(1)
        date = "2026-12-09" if len(calls) >= 2 else "2026-10-28"
        return {"statement": {"url": "https://x/monetary20261209a.htm", "date": date},
                "minutes": {"url": "u", "date": "2026-10-28"}}

    monkeypatch.setattr(release_watch.fomc, "fetch_fomc_documents", documents)
    monkeypatch.setattr(release_watch.fomc_alerts.fomc, "fetch_content", lambda url:
                        "The Committee decided to raise the target range for the federal funds "
                        "rate to 4 to 4-1/4 percent.")
    clock = fake_clock(_eastern("2026-12-09", 13, 23).astimezone(timezone.utc))
    events = release_watch.due_events(rt.store.state, clock.now())
    assert [e["key"] for e in events] == ["fomc_statement"]
    release_watch.run(rt, events, clock)
    text = sender.messages[0]
    assert "FOMC声明（12/9）" in text
    assert "期待金利 4.05%" in text
    assert "決定との差 +7.5bp → 市場の予想どおり" in text
    assert rt.store.get("fomc", "statement", "date") == "2026-12-09"

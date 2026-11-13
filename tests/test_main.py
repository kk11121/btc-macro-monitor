import json

import pytest

import main
import notifier


@pytest.fixture
def paths(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(main, "STATE_FILE", state)
    monkeypatch.setattr(main, "ETF_HISTORY_FILE", tmp_path / "history" / "etf_flows.json")
    monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")
    return state


def test_runs_selected_job(paths, monkeypatch):
    ran = []
    monkeypatch.setattr(
        main.jobs_morning, "run_morning", lambda rt, force=False: ran.append("morning")
    )
    monkeypatch.setattr(main.jobs_hourly, "run_hourly", lambda rt: ran.append("hourly"))
    assert main.main(["--mode", "morning"]) == 0
    assert main.main(["--mode", "hourly"]) == 0
    assert ran == ["morning", "hourly"]


def test_default_mode_is_hourly(paths, monkeypatch):
    ran = []
    monkeypatch.setattr(main.jobs_hourly, "run_hourly", lambda rt: ran.append("hourly"))
    assert main.main([]) == 0
    assert ran == ["hourly"]


def test_removed_modes_are_rejected(paths):
    with pytest.raises(SystemExit):
        main.main(["--mode", "sec"])


def test_exit_code_is_one_when_a_notification_failed(paths, monkeypatch):
    def job(rt):
        rt.notification_failed = True

    monkeypatch.setattr(main.jobs_hourly, "run_hourly", job)
    assert main.main(["--mode", "hourly"]) == 1


def test_uncaught_job_failure_is_isolated_in_morning(paths, monkeypatch):
    def job(rt, force=False):
        raise RuntimeError("bug")

    sent = []
    monkeypatch.setattr(main.notifier, "send", sent.append)
    monkeypatch.setattr(main.jobs_morning, "run_morning", job)
    assert main.main(["--mode", "morning"]) == 0
    assert "朝のまとめ" in sent[0]


def test_corrupt_state_stops_and_notifies_discord(paths, monkeypatch):
    paths.write_text("{broken", encoding="utf-8")
    sent = []
    monkeypatch.setattr(main.notifier, "send", sent.append)
    monkeypatch.setattr(main.jobs_hourly, "run_hourly", _must_not_run)
    assert main.main(["--mode", "hourly"]) == 1
    assert "state.json" in sent[0]
    assert paths.read_text(encoding="utf-8") == "{broken"


def _must_not_run(rt):
    raise AssertionError("壊れた state では実行しない")


def test_corrupt_state_survives_discord_failure(paths, monkeypatch):
    paths.write_text("{broken", encoding="utf-8")

    def fail(message):
        raise notifier.NotificationError("HTTP 500")

    monkeypatch.setattr(main.notifier, "send", fail)
    monkeypatch.setattr(main.jobs_hourly, "run_hourly", _must_not_run)
    assert main.main(["--mode", "hourly"]) == 1


def test_corrupt_state_in_dry_run_does_not_send(paths, monkeypatch):
    paths.write_text("{broken", encoding="utf-8")
    sent = []
    monkeypatch.setattr(main.notifier, "send", sent.append)
    assert main.main(["--mode", "hourly", "--dry-run"]) == 1
    assert sent == []


def test_dry_run_writes_nothing_and_sends_nothing(paths, monkeypatch):
    text = json.dumps({"sec": {"date": "x", "seen_ids": []}})
    paths.write_text(text, encoding="utf-8")
    sent = []
    monkeypatch.setattr(main.notifier, "send", sent.append)

    def job(rt):
        rt.send("hello")
        rt.store.set(("cpi",), {"date": "d", "value": 1})

    monkeypatch.setattr(main.jobs_hourly, "run_hourly", job)
    assert main.main(["--mode", "hourly", "--dry-run"]) == 0
    assert sent == []
    assert paths.read_text(encoding="utf-8") == text


def test_force_is_passed_to_morning(paths, monkeypatch):
    calls = []
    monkeypatch.setattr(
        main.jobs_morning, "run_morning", lambda rt, force=False: calls.append(force)
    )
    assert main.main(["--mode", "morning"]) == 0
    assert main.main(["--mode", "morning", "--force"]) == 0
    assert calls == [False, True]


def test_force_is_ignored_for_hourly(paths, monkeypatch):
    ran = []
    monkeypatch.setattr(main.jobs_hourly, "run_hourly", lambda rt: ran.append("hourly"))
    assert main.main(["--mode", "hourly", "--force"]) == 0
    assert ran == ["hourly"]


def test_monthly_mode_runs_report_with_force(paths, monkeypatch):
    calls = []
    monkeypatch.setattr(main.monthly_report, "run",
                        lambda rt, today, force=False: calls.append((today, force)))
    assert main.main(["--mode", "monthly", "--force"]) == 0
    assert calls and calls[0][1] is True


def test_sosovalue_usage_is_recorded_after_the_job(paths, monkeypatch):
    def job(rt):
        main.sosovalue._last_usage = {"remaining_month": 1500, "limit_month": 10000,
                                      "checked_at": "x"}

    monkeypatch.setattr(main.jobs_hourly, "run_hourly", job)
    assert main.main(["--mode", "hourly"]) == 0
    saved = json.loads(paths.read_text(encoding="utf-8"))
    assert saved["sosovalue"]["remaining_month"] == 1500

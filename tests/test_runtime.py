import notifier
import runtime


def _boom():
    raise RuntimeError("fetch failed")


def test_guard_notifies_first_failure_only(make_runtime, sender):
    rt = make_runtime()
    assert rt.guard("vix", "VIX", _boom) is False
    assert rt.guard("vix", "VIX", _boom) is False
    assert rt.guard("vix", "VIX", _boom) is False
    assert len(sender.messages) == 1
    assert "VIX" in sender.messages[0]
    assert "vix" in rt.store.get("errors")


def test_guard_notifies_recovery_once(make_runtime, sender):
    rt = make_runtime()
    rt.guard("vix", "VIX", _boom)
    sender.messages.clear()
    assert rt.guard("vix", "VIX", lambda: None) is True
    assert rt.guard("vix", "VIX", lambda: None) is True
    assert len(sender.messages) == 1
    assert "復旧" in sender.messages[0]
    assert rt.store.get("errors") == {}


def test_guard_success_without_prior_failure_is_silent(make_runtime, sender):
    rt = make_runtime()
    assert rt.guard("vix", "VIX", lambda: None) is True
    assert sender.messages == []


def test_one_failure_does_not_stop_the_next_indicator(make_runtime):
    rt = make_runtime()
    ran = []
    rt.guard("a", "A", _boom)
    rt.guard("b", "B", lambda: ran.append("b"))
    assert ran == ["b"]


def test_notification_failure_is_isolated_and_flagged(make_runtime):
    rt = make_runtime()

    def send_a():
        raise notifier.NotificationError("HTTP 500")

    ran = []
    assert rt.guard("a", "A", send_a) is False
    rt.guard("b", "B", lambda: ran.append("b"))
    assert ran == ["b"]
    assert rt.notification_failed is True
    assert rt.store.get("errors") == {}  # 取得の失敗としては数えない


def test_failure_is_retried_when_error_notice_could_not_be_sent(make_runtime, sender):
    rt = make_runtime()
    sender.fail_on = ["VIX"]
    rt.guard("vix", "VIX", _boom)
    assert rt.store.get("errors") == {}
    assert rt.notification_failed is True
    sender.fail_on = []
    rt.guard("vix", "VIX", _boom)
    assert len(sender.messages) == 1
    assert "vix" in rt.store.get("errors")


def test_recovery_stays_pending_when_notice_could_not_be_sent(make_runtime, sender):
    rt = make_runtime()
    rt.guard("vix", "VIX", _boom)
    sender.fail_on = ["復旧"]
    rt.guard("vix", "VIX", lambda: None)
    assert "vix" in rt.store.get("errors")
    sender.fail_on = []
    rt.guard("vix", "VIX", lambda: None)
    assert rt.store.get("errors") == {}


def test_errors_state_holds_no_exception_text(make_runtime):
    rt = make_runtime()

    def leak():
        raise RuntimeError("https://x/?api_key=SECRET")

    rt.guard("fred", "FRED", leak)
    assert "SECRET" not in str(rt.store.get("errors"))


def test_redact_removes_secret_values(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "abc123secret")
    assert "abc123secret" not in runtime.redact("url?api_key=abc123secret")


def test_redact_ignores_unset_secrets(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert runtime.redact("plain text") == "plain text"


def test_dry_run_prints_instead_of_sending(make_runtime, sender, capsys):
    rt = make_runtime(dry_run=True)
    rt.send("hello")
    assert sender.messages == []
    assert "hello" in capsys.readouterr().out

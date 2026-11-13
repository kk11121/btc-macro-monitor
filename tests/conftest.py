import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime import Runtime  # noqa: E402
from state_store import StateStore  # noqa: E402


class FakeSender:
    """Discord の代わりに本文を記録する。fail_on に含む語があると送信失敗を起こす。"""

    def __init__(self):
        self.messages: list[str] = []
        self.fail_on: list[str] = []

    def __call__(self, message: str) -> None:
        import notifier

        if any(word in message for word in self.fail_on):
            raise notifier.NotificationError("HTTP 500")
        self.messages.append(message)


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state.json"


@pytest.fixture
def history_path(tmp_path):
    return tmp_path / "history" / "etf_flows.json"


@pytest.fixture
def sender():
    return FakeSender()


@pytest.fixture
def make_runtime(state_path, history_path, sender):
    def _make(state: dict | None = None, dry_run: bool = False) -> Runtime:
        if state is not None:
            state_path.write_text(json.dumps(state), encoding="utf-8")
        store = StateStore(state_path, dry_run=dry_run)
        store.load()
        return Runtime(store, history_path, sender=sender, dry_run=dry_run)

    return _make


class _NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """
    テストから外部に通信しない（ForexFactory は取りすぎると遮断されるため）。
    個々のテストが requests.get などを差し替えた場合はそちらが優先される。
    """
    import requests

    def blocked(*args, **kwargs):
        raise _NetworkBlocked("network is disabled in tests")

    monkeypatch.setattr(requests, "get", blocked)
    monkeypatch.setattr(requests, "post", blocked)
    monkeypatch.setattr(requests.Session, "request", blocked)


@pytest.fixture(autouse=True)
def reset_sosovalue_usage():
    from fetchers import sosovalue

    sosovalue.reset_usage()
    yield
    sosovalue.reset_usage()


class FakeClock:
    """時刻を進めるだけの時計。sleep は待たずに時刻を進める。"""

    def __init__(self, start):
        self.current = start
        self.sleeps: list[float] = []

    def now(self):
        return self.current

    def sleep(self, seconds):
        from datetime import timedelta

        self.sleeps.append(seconds)
        if seconds > 0:
            self.current = self.current + timedelta(seconds=seconds)


@pytest.fixture
def fake_clock():
    return FakeClock

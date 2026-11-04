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

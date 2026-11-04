"""1回の実行の文脈。指標ごとの切り離しと、失敗の1回目・復旧時だけの通知を担う。"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import notifier
from state_store import StateStore

logger = logging.getLogger(__name__)

_SECRET_ENV_NAMES = (
    "FRED_API_KEY",
    "SOSOVALUE_API_KEY",
    "ANTHROPIC_API_KEY",
    "COINGECKO_API_KEY",
    "DISCORD_WEBHOOK_URL",
)


def redact(text: str) -> str:
    """ログに秘密の値が残らないように伏せる（公開リポジトリの Actions ログに出るため）。"""
    for name in _SECRET_ENV_NAMES:
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, f"<{name}>")
    return text


class Runtime:
    def __init__(
        self,
        store: StateStore,
        history_path: Path,
        sender: Callable[[str], None] = notifier.send,
        dry_run: bool = False,
    ) -> None:
        self.store = store
        self.history_path = history_path
        self.dry_run = dry_run
        self.notification_failed = False
        self._sender = sender

    def send(self, message: str) -> None:
        if self.dry_run:
            sys.stdout.write(f"--- [dry-run] 送信しない通知 ---\n{message}\n")
            return
        self._sender(message)

    def guard(self, key: str, label: str, action: Callable[[], None]) -> bool:
        """
        action を実行し、失敗しても他の指標を止めない。

        取得の失敗は1回目だけ通知し、復旧時にもう一度通知する。
        通知の失敗は取得の失敗として数えず、状態を進めないまま次回に任せる。
        """
        try:
            action()
        except notifier.NotificationError as exc:
            logger.error("[%s] 通知に失敗: %s", key, redact(str(exc)))
            self.notification_failed = True
            return False
        except Exception as exc:  # 1指標の想定外の失敗で全体を止めないため、広く受ける
            logger.error("[%s] 失敗: %s: %s", key, type(exc).__name__, redact(str(exc)))
            self._record_failure(key, label)
            return False
        self._record_success(key, label)
        return True

    def _try_send(self, key: str, message: str) -> bool:
        try:
            self.send(message)
        except notifier.NotificationError as exc:
            logger.error("[%s] 通知に失敗: %s", key, redact(str(exc)))
            self.notification_failed = True
            return False
        return True

    def _record_failure(self, key: str, label: str) -> None:
        if key in self.store.get("errors", default={}):
            return
        if self._try_send(key, notifier.format_error(label)):
            since = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.store.set(("errors", key), {"label": label, "since": since})

    def _record_success(self, key: str, label: str) -> None:
        if key not in self.store.get("errors", default={}):
            return
        if self._try_send(key, notifier.format_recovery(label)):
            self.store.delete(("errors", key))

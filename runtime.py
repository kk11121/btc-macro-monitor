"""1回の実行の文脈。指標ごとの切り離しと、失敗の1回目・復旧時だけの通知を担う。"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import alert_log
import notifier
from state_store import StateStore

logger = logging.getLogger(__name__)

_SECRET_ENV_NAMES = (
    "FRED_API_KEY",
    "SOSOVALUE_API_KEY",
    "ANTHROPIC_API_KEY",
    "COINGECKO_API_KEY",
    "DISCORD_WEBHOOK_URL",
    "BLS_API_KEY",
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
        log_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.history_path = history_path
        self.dry_run = dry_run
        self.notification_failed = False
        self._sender = sender
        self.alert_log_path = log_dir / "alerts.jsonl" if log_dir else None
        self.latency_log_path = log_dir / "release_latency.jsonl" if log_dir else None

    def send(self, message: str, record: dict | None = None) -> None:
        """送れたら logs/alerts.jsonl に1行記録する。dry-run では送らず、記録もしない。"""
        if self.dry_run:
            sys.stdout.write(f"--- [dry-run] 送信しない通知 ---\n{message}\n")
            return
        self._sender(message)
        if self.alert_log_path is None:
            return
        try:
            alert_log.append(self.alert_log_path, alert_log.new_record(record or {}))
        except (OSError, TypeError, ValueError) as exc:  # 記録の失敗で通知の流れを止めない
            logger.error("通知の記録に失敗: %s", type(exc).__name__)

    def log_latency(self, entry: dict) -> None:
        """発表時刻から実績が取れるまでの時間を logs/release_latency.jsonl に書く。"""
        if self.dry_run or self.latency_log_path is None:
            logger.info("発表までの時間（記録しない）: %s", entry)
            return
        try:
            alert_log.append_latency(self.latency_log_path, entry)
        except OSError as exc:
            logger.error("発表までの時間の記録に失敗: %s", type(exc).__name__)

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

    def _try_send(self, key: str, message: str, kind: str) -> bool:
        try:
            self.send(message, {"indicator": key, "kind": kind})
        except notifier.NotificationError as exc:
            logger.error("[%s] 通知に失敗: %s", key, redact(str(exc)))
            self.notification_failed = True
            return False
        return True

    def _record_failure(self, key: str, label: str) -> None:
        if key in self.store.get("errors", default={}):
            return
        if self._try_send(key, notifier.format_error(label), "error"):
            since = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.store.set(("errors", key), {"label": label, "since": since})

    def _record_success(self, key: str, label: str) -> None:
        if key not in self.store.get("errors", default={}):
            return
        if self._try_send(key, notifier.format_recovery(label), "recovery"):
            self.store.delete(("errors", key))

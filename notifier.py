"""Discord への送信と、通知本文の組み立て。"""

import logging
import os
from datetime import date

import requests

from rules import Verdict

logger = logging.getLogger(__name__)

DISCORD_MESSAGE_LIMIT = 1900  # Discord の上限 2000 字より少し小さくする
_WEEKDAYS = "月火水木金土日"


class NotificationError(Exception):
    """Discord に送れなかった。メッセージに Webhook の URL を含めない。"""


# ── 送信 ───────────────────────────────────────────────────────────────────


def split_message(message: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """上限を超える本文を、なるべく行の切れ目で分ける。"""
    if len(message) <= limit:
        return [message]
    chunks: list[str] = []
    current = ""
    for line in message.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    return [*chunks, current] if current else chunks


def _payload(chunk: str) -> dict:
    """本文に外部由来の文字列が入るので、@everyone などのメンションを無効にする。"""
    return {"content": chunk, "allowed_mentions": {"parse": []}}


def send(message: str) -> None:
    """送信する。失敗したら NotificationError を出す（呼び出し側で指標ごとに切り離す）。"""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        raise NotificationError("DISCORD_WEBHOOK_URL が未設定です")
    for chunk in split_message(message):
        try:
            resp = requests.post(webhook_url, json=_payload(chunk), timeout=10)
        except requests.RequestException as exc:
            raise NotificationError(f"Discord 接続失敗: {type(exc).__name__}") from None
        if resp.status_code not in (200, 204):
            raise NotificationError(f"Discord HTTP {resp.status_code}")
    logger.info("Discord Webhook sent successfully")


# ── 表示の部品 ─────────────────────────────────────────────────────────────


def format_month(date_str: str) -> str:
    return f"{int(date_str[5:7])}月分"


def format_day(date_str: str) -> str:
    return f"{int(date_str[5:7])}/{int(date_str[8:10])}"


def format_week(date_str: str) -> str:
    return f"{format_day(date_str)}週"


def format_long_date(date_str: str) -> str:
    day = date.fromisoformat(date_str)
    return f"{day.month}月{day.day}日({_WEEKDAYS[day.weekday()]})"


def format_signed_percent(value: float | None) -> str:
    return "―" if value is None else f"{value:+.1f}%"


def format_usd_flow(value: float) -> str:
    sign = "-" if value < 0 else "+"
    size = abs(value)
    if size >= 1_000_000_000:
        return f"{sign}${size / 1_000_000_000:.2f}B"
    return f"{sign}${size / 1_000_000:.1f}M"


def format_btc_flow(value: float) -> str:
    return f"{value:+,.0f} BTC"


def flow_direction_text(value: float) -> str:
    return "流入" if value >= 0 else "流出"


def flow_band_text(outside: bool | None, band: dict | None) -> str:
    if band is None:
        return "普段の範囲は算出できず"
    return "⚠️ 普段の範囲を超えた" if outside else "普段の範囲内"


# ── 通知本文 ───────────────────────────────────────────────────────────────


def format_alert(title: str, verdict: Verdict, lines: list[str]) -> str:
    body = "\n".join(lines)
    return f"**【BTC Monitor】{title}{verdict.emoji} {verdict.label}**\n{body}"


def format_error(label: str) -> str:
    return f"**【BTC Monitor】** ⚠️ {label}の取得に失敗しました"


def format_recovery(label: str) -> str:
    return f"**【BTC Monitor】** ✅ {label}の取得が復旧しました"


def format_state_corrupt() -> str:
    return (
        "**【BTC Monitor】** 🚨 state.json を読めないため実行を止めました。"
        "git の履歴から復元してください"
    )

"""
SoSoValue 公式API の共通の呼び出し。

応答は {code, message, data, details} で包まれて返る。上限は実測で月1万回なので、
呼ぶたびに応答ヘッダーの残り回数を控え、ジョブの最後に state.json へ記録する。
"""

import logging
import os
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://openapi.sosovalue.com/openapi/v1"
TIMEOUT_SECONDS = 30
HISTORY_LIMIT = 3  # 次回分（実績が空）と直近の発表が入る件数

# 直近の応答の残り回数。ジョブの最後に main が state.json に写す
_last_usage: dict | None = None


class MissingApiKey(Exception):
    """SOSOVALUE_API_KEY が未設定。"""


def last_usage() -> dict | None:
    return _last_usage


def reset_usage() -> None:
    global _last_usage
    _last_usage = None


def _record_usage(headers: requests.structures.CaseInsensitiveDict) -> None:
    global _last_usage
    remaining = headers.get("x-ratelimit-remaining-month")
    limit = headers.get("x-ratelimit-limit-month")
    if remaining is None or limit is None:
        return
    try:
        _last_usage = {
            "remaining_month": int(remaining),
            "limit_month": int(limit),
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    except ValueError:
        logger.warning("SoSoValue の残り回数のヘッダーを読めません")


def get(path: str, params: dict | None = None):
    """data の中身を返す。キーは例外の文面に入れない。"""
    api_key = os.environ.get("SOSOVALUE_API_KEY")
    if not api_key:
        raise MissingApiKey("SOSOVALUE_API_KEY is not set")
    try:
        resp = requests.get(
            f"{BASE_URL}{path}",
            params=params,
            headers={"x-soso-api-key": api_key},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"SoSoValue request failed: {type(exc).__name__}") from None
    _record_usage(resp.headers)
    if not resp.ok:
        raise RuntimeError(f"SoSoValue HTTP {resp.status_code}")
    body = resp.json()
    if body.get("code") != 0:
        raise ValueError(f"SoSoValue error code {body.get('code')}")
    return body.get("data")


def fetch_event_history(event: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    """
    指標ごとの実績・予想・前回を新しい順に返す（値は文字列のまま）。

    指標名を間違えてもエラーにならず空が返るので、0件は名前の誤りとして例外にする。
    次回分の行がまだないだけなら、過去の行は返るので例外にならない。
    """
    data = get(f"/macro/events/{event}/history", {"limit": limit})
    rows = [
        {
            "date": str(item.get("date") or ""),
            "actual": str(item.get("actual") or "").strip(),
            "forecast": str(item.get("forecast") or "").strip(),
            "previous": str(item.get("previous") or "").strip(),
        }
        for item in data or []
        if isinstance(item, dict)
    ]
    if not rows:
        raise ValueError(f"SoSoValue has no history for {event!r} (wrong event name?)")
    return sorted(rows, key=lambda row: row["date"], reverse=True)


def fetch_fomc_probabilities() -> dict:
    """{"as_of": ミリ秒, "rate_ranges": [...], "meetings": [{meeting_date, probabilities}]}"""
    data = get("/macro/fomc-probabilities")
    if not isinstance(data, dict) or not data.get("meetings") or not data.get("rate_ranges"):
        raise ValueError("SoSoValue FOMC probabilities are empty")
    return data

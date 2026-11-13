"""
ForexFactory の週次カレンダー（非公式、今週分のみ）。

取りすぎると遮断されうるので、呼ぶのは朝のまとめと発表の直前だけにする（1日数回まで）。
"""

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
HEADERS = {"User-Agent": "btc-macro-monitor"}
COUNTRY = "USD"


def fetch_week() -> list[dict]:
    """
    米国の行だけを返す。

    Returns:
        [{"title", "date_et": "YYYY-MM-DD", "forecast", "previous"}, ...]
    """
    try:
        resp = requests.get(CALENDAR_URL, headers=HEADERS, timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(f"ForexFactory request failed: {type(exc).__name__}") from None
    if not resp.ok:
        raise RuntimeError(f"ForexFactory HTTP {resp.status_code}")
    events = resp.json()
    if not isinstance(events, list):
        raise TypeError("ForexFactory calendar is not a list")
    rows = []
    for event in events:
        if not isinstance(event, dict) or event.get("country") != COUNTRY:
            continue
        try:
            # 例：2026-11-10T08:30:00-05:00（米東部の時差付き）
            when = datetime.fromisoformat(str(event.get("date")))
        except ValueError:
            continue
        rows.append(
            {
                "title": str(event.get("title") or ""),
                "date_et": when.date().isoformat(),
                "forecast": str(event.get("forecast") or "").strip(),
                "previous": str(event.get("previous") or "").strip(),
            }
        )
    return rows


def find(events: list[dict], title: str, date_et: str) -> dict | None:
    """名前と米東部の日付が合う行。名前は大文字と小文字を区別しない。"""
    wanted = title.casefold()
    return next(
        (e for e in events if e["title"].casefold() == wanted and e["date_et"] == date_et),
        None,
    )

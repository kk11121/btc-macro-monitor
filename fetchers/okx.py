"""OKX の BTC 1時間足（検証用の価格）。確定した足の終値だけを使う。"""

import logging
from datetime import datetime, timedelta, timezone

import requests

from fetchers.validation import finite_float

logger = logging.getLogger(__name__)

HISTORY_URL = "https://www.okx.com/api/v5/market/history-candles"
INSTRUMENT = "BTC-USDT"
PAGE_LIMIT = 100       # OKX の1回の上限
MAX_PAGES = 60         # 1回の呼び出しで取る最大ページ数（約250日分）
HOUR = timedelta(hours=1)


def floor_hour(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _page(after_ms: int) -> list[list]:
    """after より古い足を新しい順に最大100本。"""
    try:
        resp = requests.get(
            HISTORY_URL,
            params={"instId": INSTRUMENT, "bar": "1H", "after": str(after_ms), "limit": PAGE_LIMIT},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"OKX request failed: {type(exc).__name__}") from None
    if not resp.ok:
        raise RuntimeError(f"OKX HTTP {resp.status_code}")
    body = resp.json()
    if body.get("code") != "0":
        raise RuntimeError(f"OKX error code {body.get('code')}")
    return body.get("data") or []


def fetch_hourly_closes(start: datetime, end: datetime) -> dict[datetime, float]:
    """
    start から end までに始まった1時間足の終値を、足の開始時刻（UTC）ごとに返す。
    確定していない足（confirm が 0）は入れない。
    """
    first, last = floor_hour(start), floor_hour(end)
    closes: dict[datetime, float] = {}
    after_ms = int((last + HOUR).timestamp() * 1000)
    for _ in range(MAX_PAGES):
        candles = _page(after_ms)
        if not candles:
            break
        for candle in candles:
            opened = datetime.fromtimestamp(int(candle[0]) / 1000, timezone.utc)
            if first <= opened <= last and str(candle[8]) == "1":
                closes[opened] = finite_float(candle[4])
        oldest_ms = min(int(candle[0]) for candle in candles)
        if oldest_ms <= int(first.timestamp() * 1000):
            break
        after_ms = oldest_ms
    return closes

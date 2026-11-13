"""外部データの検証。不正な値を state や履歴に入れないよう、取得の境界で弾く。"""

import math
from datetime import date, datetime, timedelta, timezone

FUTURE_TOLERANCE_DAYS = 1  # タイムゾーン差のぶんだけ許す
ISO_DATE_LENGTH = 10


def valid_date(text: object) -> str:
    """
    YYYY-MM-DD の実在する日付で、未来でないものを返す。それ以外は ValueError。

    未来の日付を一度保存すると「それより新しい日」が来なくなり、通知が黙って止まる。
    """
    if not isinstance(text, str) or len(text) != ISO_DATE_LENGTH:
        raise ValueError(f"invalid date: {text!r}")
    parsed = date.fromisoformat(text)
    latest_allowed = datetime.now(timezone.utc).date() + timedelta(days=FUTURE_TOLERANCE_DAYS)
    if parsed > latest_allowed:
        raise ValueError(f"future date: {text}")
    return text


def finite_float(value: object) -> float:
    """有限の数値を返す。NaN や無限大は ValueError（統計と JSON を壊すため）。"""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite value: {value!r}")
    return number


def valid_calendar_date(text: object) -> str:
    """YYYY-MM-DD の実在する日付を返す。発表予定日など、未来の日付を許す場面で使う。"""
    if not isinstance(text, str) or len(text) != ISO_DATE_LENGTH:
        raise ValueError(f"invalid date: {text!r}")
    date.fromisoformat(text)
    return text

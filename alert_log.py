"""
通知の記録（logs/alerts.jsonl、1通知1行）と、通知後の BTC 価格の書き足し。
発表から実績が取れるまでの時間の記録（logs/release_latency.jsonl）もここで扱う。

価格の決め方：その時刻を含む OKX の1時間足の終値（確定した足だけ）。通知時点も同じ方法で
埋めるので、1時間後との差は1時間ちょうどの値動きになる。取れなかった欄は空のまま残し、
次の実行で再試行する。
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import config
from fetchers import okx

logger = logging.getLogger(__name__)

PRICE_FIELDS = {0: "btc_price", **{h: f"btc_price_{h}h" for h in config.PRICE_HORIZONS_HOURS}}
HOUR = timedelta(hours=1)


def new_record(record: dict, now: datetime | None = None) -> dict:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base = {"id": uuid.uuid4().hex[:16], "ts": moment.isoformat(timespec="seconds"),
            "indicator": None, "kind": "other", "actual": None, "compared": None,
            "verdict": None}
    return {**base, **record, **{field: None for field in PRICE_FIELDS.values()}}


def append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, allow_nan=False, default=str)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("%s の %d 行目を読めないので飛ばします", path.name, number)
    return rows


def write(path: Path, rows: list[dict]) -> None:
    """途中で落ちても壊れないよう、一時ファイルに書いてから置き換える。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = "".join(json.dumps(r, ensure_ascii=False, allow_nan=False, default=str) + "\n"
                   for r in rows)
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _targets(row: dict, now: datetime) -> dict[str, datetime]:
    """空欄で、足が確定している（時刻を含む1時間足が閉じた）ものだけ。"""
    ts = datetime.fromisoformat(row["ts"])
    targets = {}
    for hours, field in PRICE_FIELDS.items():
        if row.get(field) is not None:
            continue
        start = okx.floor_hour(ts + timedelta(hours=hours))
        if start + HOUR <= now:
            targets[field] = start
    return targets


def fill_prices(
    path: Path,
    now: datetime | None = None,
    fetch_closes: Callable[[datetime, datetime], dict] = okx.fetch_hourly_closes,
) -> int:
    """空欄の価格を埋め、埋めた欄の数を返す。古すぎる行は相手にしない。"""
    moment = now or datetime.now(timezone.utc)
    rows = read(path)
    oldest = moment - timedelta(days=config.PRICE_FILL_MAX_AGE_DAYS)
    wanted = {}
    for index, row in enumerate(rows):
        if datetime.fromisoformat(row["ts"]) < oldest:
            continue
        targets = _targets(row, moment)
        if targets:
            wanted[index] = targets
    if not wanted:
        return 0
    starts = [start for targets in wanted.values() for start in targets.values()]
    closes = fetch_closes(min(starts), max(starts))
    filled = 0
    for index, targets in wanted.items():
        for field, start in targets.items():
            if start in closes:
                rows[index] = {**rows[index], field: closes[start]}
                filled += 1
    if filled:
        write(path, rows)
    return filled


def append_latency(path: Path, entry: dict) -> None:
    append(path, entry)

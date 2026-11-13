"""
BLS 公式API v2（CPI・雇用統計の実績を FRED より早く取る）。

BLS はエラーの本文に登録キーをそのまま返すので、本文やメッセージは決してログに出さない。
キーがないときは呼ばない（キーなしは1日25回までで、待ち受けには足りない）。
"""

import logging
import os
from datetime import date

import requests

from fetchers.validation import finite_float

logger = logging.getLogger(__name__)

API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
SUCCESS = "REQUEST_SUCCEEDED"
MONTHLY_PERIODS = {f"M{month:02d}" for month in range(1, 13)}  # M13 は年平均なので除く


class MissingApiKey(Exception):
    """BLS_API_KEY が未設定。"""


def _rows(series: dict) -> list[dict]:
    """新しい順の月次の行。"-"（欠測）は除く。"""
    rows = []
    for item in series.get("data") or []:
        if item.get("period") not in MONTHLY_PERIODS:
            continue
        raw = str(item.get("value", "")).strip()
        if raw in ("", "-"):
            continue
        month = int(item["period"][1:])
        rows.append({"date": f"{int(item['year'])}-{month:02d}-01", "value": finite_float(raw)})
    return sorted(rows, key=lambda row: row["date"], reverse=True)


def fetch_series(series_ids: list[str], today: date | None = None) -> dict[str, list[dict]]:
    """
    前年と今年の月次の値を系列ごとに返す（前年比の基準月まで届く）。

    Returns:
        {series_id: [{"date": "YYYY-MM-01", "value": float}, ...]}
    """
    api_key = os.environ.get("BLS_API_KEY")
    if not api_key:
        raise MissingApiKey("BLS_API_KEY is not set")
    year = (today or date.today()).year
    payload = {
        "seriesid": series_ids,
        "startyear": str(year - 1),
        "endyear": str(year),
        "registrationkey": api_key,
    }
    try:
        resp = requests.post(API_URL, json=payload, timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(f"BLS request failed: {type(exc).__name__}") from None
    if not resp.ok:
        raise RuntimeError(f"BLS HTTP {resp.status_code}")
    body = resp.json()
    status = body.get("status")
    if status != SUCCESS:
        # message にはキーが入ることがあるので、状態の名前だけを出す
        raise RuntimeError(f"BLS status {status}")
    found = {s.get("seriesID"): _rows(s) for s in body.get("Results", {}).get("series", [])}
    missing = [sid for sid in series_ids if not found.get(sid)]
    if missing:
        raise ValueError(f"BLS returned no data for {missing}")
    return found

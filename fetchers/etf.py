"""現物BTC ETFの資金流出入。SoSoValue を主とし、TFTC を代替にする。"""

import logging

import requests

from fetchers import sosovalue
from fetchers.validation import finite_float, valid_date

logger = logging.getLogger(__name__)

TFTC_LATEST_URL = "https://www.tftc.io/bitcoin-etf-flows/latest.json"
TFTC_HISTORY_URL = "https://www.tftc.io/bitcoin-etf-flows/data.json"
TFTC_ATTRIBUTION = "The Commoner Bitcoin ETF Tracker by TFTC, tftc.io/bitcoin-etf-flows"
SOSOVALUE_ROWS = 30  # 実際に返るのは直近1か月（約22行）まで


MissingApiKey = sosovalue.MissingApiKey


def _row(date_str: object, value: object, source: str) -> dict:
    if value is None:
        raise ValueError(f"incomplete {source} row")
    return {"date": valid_date(date_str), "value": finite_float(value), "source": source}


def _newest_first(rows: list[dict], source: str) -> list[dict]:
    if not rows:
        raise ValueError(f"{source} returned no rows")
    return sorted(rows, key=lambda row: row["date"], reverse=True)


def fetch_sosovalue_rows() -> list[dict]:
    """直近の日次合計を新しい順に返す。"""
    data = sosovalue.get(
        "/etfs/summary-history",
        {"symbol": "BTC", "country_code": "US", "limit": SOSOVALUE_ROWS},
    )
    rows = [
        _row(item.get("date"), item.get("total_net_inflow"), "sosovalue")
        for item in data or []
    ]
    return _newest_first(rows, "SoSoValue")


def _fetch_tftc(url: str) -> dict:
    resp = requests.get(url, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"TFTC HTTP {resp.status_code}")
    return resp.json()


def fetch_tftc_latest_rows() -> list[dict]:
    """代替：TFTC の直近5取引日を新しい順に返す。"""
    sessions = _fetch_tftc(TFTC_LATEST_URL).get("recentSessions") or []
    rows = [_row(item.get("date"), item.get("netFlowUsd"), "tftc") for item in sessions]
    return _newest_first(rows, "TFTC")


def fetch_tftc_history() -> list[dict]:
    """過去分の取り込み用：TFTC の全履歴を新しい順に返す（値がない日は除く）。"""
    days = _fetch_tftc(TFTC_HISTORY_URL).get("days") or []
    rows = [
        _row(item.get("date"), item.get("netFlowUsd"), "tftc")
        for item in days
        if item.get("netFlowUsd") is not None
    ]
    return _newest_first(rows, "TFTC")

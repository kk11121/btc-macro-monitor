import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from fetchers.validation import finite_float, valid_date

logger = logging.getLogger(__name__)

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES_URL = "https://api.stlouisfed.org/fred/series"
EASTERN = ZoneInfo("America/New_York")
MISSING_ROW_MARGIN = 3  # FRED の limit は欠測（"."）の行も数えるので、余分に取って切り詰める

# FRED から取得する全系列。
SERIES: dict[str, dict] = {
    "cpi": {"id": "CPIAUCSL", "name": "CPI（総合・季節調整あり）"},
    "cpi_nsa": {"id": "CPIAUCNS", "name": "CPI（総合・季節調整なし）"},
    "core_cpi": {"id": "CPILFESL", "name": "コアCPI（季節調整あり）"},
    "core_cpi_nsa": {"id": "CPILFENS", "name": "コアCPI（季節調整なし）"},
    "nfp": {"id": "PAYEMS", "name": "NFP（非農業部門雇用者数）"},
    "unrate": {"id": "UNRATE", "name": "失業率"},
    "icsa": {"id": "ICSA", "name": "新規失業保険申請件数"},
    "core_pce": {"id": "PCEPILFE", "name": "コアPCE"},
    "m2sl": {"id": "M2SL", "name": "M2"},
    "walcl": {"id": "WALCL", "name": "FRBバランスシート"},
}


def series_url(key: str) -> str:
    return f"https://fred.stlouisfed.org/series/{SERIES[key]['id']}"


def _get_json(url: str, series_id: str, params: dict) -> dict:
    """
    FRED は API キーを URL に載せる。requests の例外文には URL が入るので、
    そのまま外に出すと公開の Actions ログにキーが残る。ここで文面を置き換える。
    """
    query = {
        **params,
        "series_id": series_id,
        "api_key": os.environ["FRED_API_KEY"],
        "file_type": "json",
    }
    try:
        resp = requests.get(url, params=query, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"FRED request failed for {series_id}: {type(exc).__name__}") from None
    return resp.json()


def fetch_observations(key: str, limit: int = 1) -> list[dict]:
    """
    直近の観測値を新しい順に返す。欠測（FRED の "."）は除く。

    Returns:
        [{"date": "YYYY-MM-DD", "value": float}, ...]
    """
    series_id = SERIES[key]["id"]
    body = _get_json(
        FRED_OBSERVATIONS_URL,
        series_id,
        {"sort_order": "desc", "limit": limit + MISSING_ROW_MARGIN},
    )
    rows = [
        {"date": valid_date(obs["date"]), "value": finite_float(obs["value"])}
        for obs in body.get("observations", [])
        if obs.get("value") not in (None, ".")
    ]
    if not rows:
        raise ValueError(f"No observations returned for {series_id}")
    return rows[:limit]


def fetch_release_date(key: str) -> str:
    """FRED の最終更新時刻を米東部の日付（YYYY-MM-DD）にして返す。発表日の表示に使う。"""
    series_id = SERIES[key]["id"]
    body = _get_json(FRED_SERIES_URL, series_id, {})
    updated = datetime.fromisoformat(body["seriess"][0]["last_updated"])
    return updated.astimezone(EASTERN).date().isoformat()

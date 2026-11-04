import logging
from datetime import datetime, timedelta, timezone

import requests

import config
from fetchers.validation import finite_float, valid_date

logger = logging.getLogger(__name__)

BASE = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
PAGE_SIZE = 200  # 既定の100だと期間の古い側だけが返り、最新の行が落ちる
SUPPLY_LOOKBACK_DAYS = 3


def _fetch_rows(metrics: str, days: int) -> list[dict]:
    """BTC の指標を古い順に返す（キー不要）。"""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    resp = requests.get(
        BASE,
        params={
            "assets": "btc",
            "metrics": metrics,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "page_size": PAGE_SIZE,
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"CoinMetrics {metrics} returned HTTP {resp.status_code}")
    return resp.json().get("data", [])


def fetch_exchange_supply() -> dict:
    """取引所のBTC残高（SplyExNtv）の最新値を返す。"""
    rows = [r for r in _fetch_rows("SplyExNtv", SUPPLY_LOOKBACK_DAYS) if "SplyExNtv" in r]
    if not rows:
        raise ValueError("CoinMetrics returned empty data for SplyExNtv")
    return {
        "value": finite_float(rows[-1]["SplyExNtv"]),
        "date": valid_date(rows[-1]["time"][:10]),
    }


def fetch_exchange_netflow_history() -> list[dict]:
    """
    取引所へのBTC純流入（流入 − 流出、プラスは売り圧力）を新しい順に返す。
    普段の振れ幅の計算に使うので、過去90日分より少し多めに取る。
    """
    days = config.FLOW_LOOKBACK_DAYS + 10
    rows = [
        {
            "date": valid_date(row["time"][:10]),
            "value": finite_float(row["FlowInExNtv"]) - finite_float(row["FlowOutExNtv"]),
        }
        for row in _fetch_rows("FlowInExNtv,FlowOutExNtv", days)
        if "FlowInExNtv" in row and "FlowOutExNtv" in row
    ]
    if not rows:
        raise ValueError("CoinMetrics returned empty data for exchange net flow")
    return sorted(rows, key=lambda row: row["date"], reverse=True)

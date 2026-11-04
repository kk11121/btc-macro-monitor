import os
import logging
import requests
from datetime import datetime, timezone

from fetchers.validation import finite_float

logger = logging.getLogger(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

STABLECOINS: dict[str, dict] = {
    "usdt": {
        "id": "tether",
        "name": "USDT供給量",
        "symbol": "USDT",
        "url": "https://www.coingecko.com/en/coins/tether",
    },
    "usdc": {
        "id": "usd-coin",
        "name": "USDC供給量",
        "symbol": "USDC",
        "url": "https://www.coingecko.com/en/coins/usd-coin",
    },
}


def fetch_stablecoin(key: str) -> dict:
    """
    Fetch stablecoin circulating supply from CoinGecko.

    Args:
        key: 'usdt' or 'usdc'

    Returns:
        dict with: key, name, symbol, value (float), url, timestamp
    """
    info = STABLECOINS[key]
    headers = {}
    api_key = os.environ.get("COINGECKO_API_KEY")
    if api_key:
        headers["x-cg-demo-api-key"] = api_key

    resp = requests.get(
        f"{COINGECKO_BASE}/coins/{info['id']}",
        params={
            "localization": "false",
            "tickers": "false",
            "community_data": "false",
            "developer_data": "false",
        },
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    supply = finite_float(resp.json()["market_data"]["circulating_supply"])

    return {
        "key": key,
        "name": info["name"],
        "symbol": info["symbol"],
        "value": supply,
        "url": info["url"],
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }



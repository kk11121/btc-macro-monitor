import logging
from datetime import datetime, timezone

import yfinance as yf

from fetchers.validation import finite_float

logger = logging.getLogger(__name__)

TICKERS: dict[str, dict] = {
    "dxy": {
        "ticker": "DX-Y.NYB",
        "name": "DXY（ドル指数）",
        "url": "https://finance.yahoo.com/quote/DX-Y.NYB",
    },
    "us10y": {
        "ticker": "^TNX",
        "name": "米国10年債利回り",
        "url": "https://finance.yahoo.com/quote/%5ETNX",
    },
    "nasdaq": {
        "ticker": "^IXIC",
        "name": "NASDAQ",
        "url": "https://finance.yahoo.com/quote/%5EIXIC",
    },
    "vix": {
        "ticker": "^VIX",
        "name": "VIX（恐怖指数）",
        "url": "https://finance.yahoo.com/quote/%5EVIX",
    },
}


def fetch_market(key: str) -> dict:
    """
    Fetch the latest price for a market indicator via Yahoo Finance.

    Args:
        key: one of 'dxy', 'us10y', 'nasdaq', 'vix'

    Returns:
        dict with keys: key, name, value (float), url, timestamp
    """
    info = TICKERS[key]
    ticker_obj = yf.Ticker(info["ticker"])
    price = ticker_obj.fast_info.last_price

    if price is None:
        raise ValueError(f"No price data available for {info['ticker']}")

    return {
        "key": key,
        "name": info["name"],
        "value": finite_float(price),
        "url": info["url"],
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

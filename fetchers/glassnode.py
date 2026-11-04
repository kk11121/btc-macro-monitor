import logging
import requests
from datetime import date, datetime, timezone

from fetchers.validation import finite_float

logger = logging.getLogger(__name__)


# ── Fear & Greed Index (Alternative.me — free, no key) ───────────────────────

_FG_ZONE_MAP = {
    "Extreme Fear":  ("extreme_fear",  "極度の恐怖"),
    "Fear":          ("fear",          "恐怖"),
    "Neutral":       ("neutral",       "中立"),
    "Greed":         ("greed",         "強欲"),
    "Extreme Greed": ("extreme_greed", "極度の強欲"),
}


def fetch_fear_greed() -> dict:
    """
    Fetch the latest Crypto Fear & Greed Index from Alternative.me.
    No API key required.

    Returns:
        dict with: key, name, value (0-100), zone, zone_label, date, url
    """
    resp = requests.get(
        "https://api.alternative.me/fng/",
        params={"limit": 1},
        timeout=30,
    )
    resp.raise_for_status()
    entry = resp.json()["data"][0]
    value = finite_float(entry["value"])
    data_date = date.fromtimestamp(int(entry["timestamp"])).isoformat()
    zone, zone_label = _FG_ZONE_MAP.get(
        entry["value_classification"], ("unknown", "不明")
    )

    return {
        "key": "fear_greed",
        "name": "恐怖&強欲指数",
        "value": value,
        "zone": zone,
        "zone_label": zone_label,
        "date": data_date,
        "url": "https://alternative.me/crypto/fear-and-greed-index/",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ── Funding Rate Perpetual (OKX — free, no key, globally accessible) ─────────

def fetch_funding_rate() -> dict:
    """
    Fetch the latest BTC perpetual futures funding rate from OKX.
    No API key required. Globally accessible (no geo-restriction).
    Value is in decimal form (e.g. 0.0001 = 0.01% per 8h).

    Returns:
        dict with: key, name, value (decimal float), date, url
    """
    resp = requests.get(
        "https://www.okx.com/api/v5/public/funding-rate",
        params={"instId": "BTC-USDT-SWAP"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX API error: {data.get('msg')}")
    entry = data["data"][0]
    value = finite_float(entry["fundingRate"])
    data_date = datetime.fromtimestamp(
        int(entry["fundingTime"]) / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    return {
        "key": "funding_rate",
        "name": "BTCパーペチュアルFunding Rate",
        "value": value,
        "date": data_date,
        "url": "https://www.okx.com/trade-swap/btc-usdt-swap",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

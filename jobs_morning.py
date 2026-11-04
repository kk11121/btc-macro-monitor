"""朝のまとめ：全指標を取り、前回まとめとの比較を1通にして送る。"""

import logging
import time
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import config
import notifier
import rules
from fetchers import coinmetrics, etf, fred, glassnode, market, stablecoin
from jobs_hourly import run_daily_flows
from runtime import Runtime

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
COINGECKO_INTERVAL_SECONDS = 2  # CoinGecko 無料枠の制限に合わせる
LIQUIDITY_ROWS = 2              # 最新と、比べる相手の前回発表

_MARKET_ROWS = (
    ("dxy", "DXY", "{:.2f}"),
    ("us10y", "米10年債", "{:.2f}%"),
    ("nasdaq", "NASDAQ", "{:,.0f}"),
    ("vix", "VIX", "{:.1f}"),
)
_LIQUIDITY_ROWS = (
    ("m2sl", "M2", "十億ドル", notifier.format_month),
    ("walcl", "FRBバランスシート", "百万ドル", lambda d: f"{notifier.format_day(d)}時点"),
)


def _fetch(rt: Runtime, key: str, label: str, fetcher: Callable[[], Any]) -> Any:
    """取得する。失敗は1回目だけ通知され、None が返る。"""
    box: list[Any] = []
    rt.guard(key, label, lambda: box.append(fetcher()))
    return box[0] if box else None


def _arrow(change: float) -> str:
    if change > 0:
        return "↑"
    return "↓" if change < 0 else "→"


def _percent_row(key: str, text: str, current: float, previous: float | None) -> str:
    if previous is None:
        return f"{text}（初回）"
    change = round((current / previous - 1) * 100, 1)
    verdict = rules.judge_change(key, change, 0.0)
    return f"{text} {_arrow(change)}{abs(change):.1f}% {verdict.emoji}"


def _yield_row(text: str, current: float, previous: float | None) -> str:
    if previous is None:
        return f"{text}（初回）"
    change = round((current - previous) * 100)
    verdict = rules.judge_change("us10y", change, 0.0)
    return f"{text} {_arrow(change)}{abs(change):.0f}bp {verdict.emoji}"


def _market_lines(rt: Runtime, previous: dict, fresh: dict) -> list[str]:
    lines = []
    for key, label, template in _MARKET_ROWS:
        value = _fetch(rt, key, label, lambda key=key: market.fetch_market(key)["value"])
        if value is None:
            lines.append(f" {label} 取得失敗")
            continue
        fresh[key] = value
        text = f"{label} {template.format(value)}"
        row = (
            _yield_row(text, value, previous.get(key))
            if key == "us10y"
            else _percent_row(key, text, value, previous.get(key))
        )
        lines.append(f" {row}")
    return lines


def _flow_line(flow: dict | None, key: str, label: str, format_value: Callable) -> str:
    if not flow or flow.get("value") is None:
        return f" {label} データなし"
    value = flow["value"]
    verdict = rules.judge_flow(key, value)
    line = (
        f" {label} {format_value(value)}（{notifier.format_day(flow['date'])}分）"
        f"{notifier.flow_direction_text(value)} {verdict.emoji}"
    )
    if "band" in flow:
        line += f" {notifier.flow_band_text(flow.get('outside'), flow['band'])}"
    if flow.get("source") == "tftc":
        line += f"\n 　出所：{etf.TFTC_ATTRIBUTION}"
    return line


def _fear_greed_line(rt: Runtime, previous: dict, fresh: dict) -> str:
    data = _fetch(rt, "fear_greed", "恐怖&強欲指数", glassnode.fetch_fear_greed)
    if data is None:
        return " 恐怖&強欲 取得失敗"
    fresh["fear_greed"] = {"value": data["value"], "zone": data["zone"], "date": data["date"]}
    last = (previous.get("fear_greed") or {}).get("value")
    last_text = "―" if last is None else f"{last:.0f}"
    note = "⚠️" if data["zone"] in config.FEAR_GREED_NOTE_ZONES else ""
    return f" 恐怖&強欲 {data['value']:.0f} {data['zone_label']}（前回 {last_text}）{note}"


def _funding_line(rt: Runtime, fresh: dict) -> str:
    data = _fetch(rt, "funding_rate", "Funding Rate", glassnode.fetch_funding_rate)
    if data is None:
        return " Funding Rate（OKX）取得失敗"
    value = data["value"]
    fresh["funding_rate"] = value
    note = " ⚠️" if abs(value) > config.FUNDING_NOTE_THRESHOLD else ""
    return f" Funding Rate（OKX）{value * 100:+.3f}%{note}"


def _stablecoin_line(rt: Runtime, previous: dict, fresh: dict) -> str:
    parts = []
    for index, (key, label) in enumerate((("usdt", "USDT"), ("usdc", "USDC"))):
        if index:
            time.sleep(COINGECKO_INTERVAL_SECONDS)
        data = _fetch(rt, key, label, lambda key=key: stablecoin.fetch_stablecoin(key))
        if data is None:
            parts.append(f"{label} 取得失敗")
            continue
        fresh[key] = data["value"]
        text = f"{label} {data['value'] / 1e9:.1f}B"
        parts.append(_percent_row(key, text, data["value"], previous.get(key)))
    return " " + " ／ ".join(parts)


def _exchange_balance_line(rt: Runtime, previous: dict, fresh: dict) -> str:
    data = _fetch(rt, "btc_exchange", "取引所BTC残高", coinmetrics.fetch_exchange_supply)
    if data is None:
        return " 取引所BTC残高 取得失敗"
    fresh["btc_exchange"] = data["value"]
    text = f"取引所BTC残高 {data['value'] / 1e6:.3f}M"
    return " " + _percent_row("btc_exchange", text, data["value"], previous.get("btc_exchange"))


def _crypto_lines(rt: Runtime, previous: dict, fresh: dict) -> list[str]:
    return [
        _flow_line(
            rt.store.get("flows", "spot_etf"), "spot_etf", "現物BTC ETF", notifier.format_usd_flow
        ),
        _flow_line(
            rt.store.get("flows", "exchange_netflow"),
            "exchange_netflow",
            "取引所BTC純流入",
            notifier.format_btc_flow,
        ),
        _fear_greed_line(rt, previous, fresh),
        _funding_line(rt, fresh),
        _stablecoin_line(rt, previous, fresh),
        _exchange_balance_line(rt, previous, fresh),
    ]


def _liquidity_lines(rt: Runtime, updates: dict) -> list[str]:
    """
    新しい発表があった指標だけ載せる。変化幅にかかわらず必ず保存し、前回の発表と比べる。
    発表の間隔が空いても比較がずれないよう、相手は FRED の1つ前の観測値にする。
    """
    lines = []
    for key, label, unit, format_period in _LIQUIDITY_ROWS:
        rows = _fetch(
            rt, key, label, lambda key=key: fred.fetch_observations(key, limit=LIQUIDITY_ROWS)
        )
        if rows is None:
            lines.append(f" {label} 取得失敗")
            continue
        latest = rows[0]
        stored_date = rt.store.get("liquidity", key, "date")
        if stored_date is not None and latest["date"] <= stored_date:
            continue
        updates[key] = latest
        previous = rows[1]["value"] if len(rows) >= LIQUIDITY_ROWS else None
        text = f"{label} {latest['value']:,.0f}{unit}"
        row = _percent_row(key, text, latest["value"], previous)
        lines.append(f" {row}（{format_period(latest['date'])}）")
    return lines


def _status_lines(rt: Runtime) -> list[str]:
    errors = rt.store.get("errors", default={})
    if not errors:
        return ["■ 稼働状況：すべて正常"]
    lines = [f"■ 稼働状況：失敗中 {len(errors)}件"]
    for entry in errors.values():
        since = datetime.fromisoformat(entry["since"]).astimezone(JST)
        lines.append(f" ・{entry['label']}（{since.month}/{since.day}から）")
    return lines


def run_morning(rt: Runtime, today: str | None = None) -> None:
    today = today or datetime.now(JST).date().isoformat()
    run_daily_flows(rt)

    previous = rt.store.get("summary", "values", default={})
    fresh: dict = {}
    liquidity_updates: dict = {}
    market_lines = _market_lines(rt, previous, fresh)
    crypto_lines = _crypto_lines(rt, previous, fresh)
    liquidity_lines = _liquidity_lines(rt, liquidity_updates)

    sections = [
        f"**【BTC Monitor】朝のまとめ {notifier.format_long_date(today)}**",
        "■ マクロ・市場（前回まとめ比）",
        *market_lines,
        "■ 暗号資産",
        *crypto_lines,
    ]
    if liquidity_lines:
        sections += ["■ 流動性（前回の発表比）", *liquidity_lines]
    sections += _status_lines(rt)
    rt.send("\n".join(sections))

    # 送れた場合だけ基準を進める。送信に失敗したら次回も同じ比較でやり直せる
    rt.store.set(("summary",), {"last_date": today, "values": {**previous, **fresh}})
    for key, latest in liquidity_updates.items():
        rt.store.set(("liquidity", key), latest)

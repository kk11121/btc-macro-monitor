"""
毎時の監視：VIX、FOMC（声明・議事録・金利見通し）、日次フロー、経済指標の予備確認、
発表待ち受け（その後60分以内に発表があるとき）、通知後の BTC 価格の書き足し。
"""

import logging
import os
from datetime import datetime, timezone

import alert_log
import config
import fomc_alerts
import macro
import notifier
import release_calendar
import release_watch
import rules
from fetchers import coinmetrics, etf, market
from runtime import Runtime
from state_store import load_etf_history, merge_etf_rows, save_etf_history

logger = logging.getLogger(__name__)


# ── VIX ───────────────────────────────────────────────────────────────────


def check_vix(rt: Runtime) -> None:
    value = market.fetch_market("vix")["value"]
    baseline = rt.store.get("market", "vix", "value")
    result = rules.evaluate_vix(value, baseline)
    action = result["action"]

    if action == "alert":
        verdict = rules.judge_change("vix", value, baseline)
        lines = [f"VIX {value:.1f}（前回通知 {baseline:.1f}）{result['change_pct']:+.1f}%"]
        record = {"indicator": "vix", "kind": "immediate", "actual": value,
                  "compared": {"previous": baseline}, "verdict": verdict.label}
        rt.send(notifier.format_alert("VIX急騰", verdict, lines), record)
    if action in ("alert", "rebase", "init"):
        rt.store.set(("market", "vix"), {"value": value})


# ── 日次フロー ─────────────────────────────────────────────────────────────


def _publish_flow(
    rt: Runtime,
    key: str,
    latest: dict,
    baseline: list[float],
    display: dict,
) -> None:
    """通知してから保存する。送れなければ日付を進めず、次回の実行でやり直す。"""
    value = latest["value"]
    band = rules.flow_band(baseline)
    outside = rules.is_outside_band(value, band)
    format_value = display["format_value"]

    lines = [
        f"{format_value(value)} {notifier.flow_direction_text(value)} ／ "
        f"{notifier.flow_band_text(outside, band)}"
    ]
    if band is not None:
        lines.append(
            f"普段の範囲（過去{band['samples']}{display['unit']}）"
            f"{format_value(band['low'])} 〜 {format_value(band['high'])}"
        )
    lines.append(f"出所：{display['source']}")

    title = f"{display['label']}（{notifier.format_day(latest['date'])}分）"
    verdict = rules.judge_flow(key, value)
    record = {"indicator": key, "kind": "daily_flow", "actual": value,
              "compared": {"band": band}, "verdict": verdict.label, "date": latest["date"]}
    rt.send(notifier.format_alert(title, verdict, lines), record)
    rt.store.set(
        ("flows", key),
        {
            "date": latest["date"],
            "value": value,
            "source": latest.get("source"),
            "outside": outside,
            "band": band,
        },
    )


def _fetch_etf_rows(rt: Runtime) -> list[dict]:
    """SoSoValue を主に使う。キー未設定や失敗のときは TFTC で代替する。"""
    if not os.environ.get("SOSOVALUE_API_KEY"):
        logger.warning("SOSOVALUE_API_KEY が未設定なので TFTC を使います")
        return etf.fetch_tftc_latest_rows()
    rows: list[dict] = []
    rt.guard(
        "etf_sosovalue",
        "SoSoValue（ETF）",
        lambda: rows.extend(etf.fetch_sosovalue_rows()),
    )
    return rows or etf.fetch_tftc_latest_rows()


def _update_etf_history(rt: Runtime, rows: list[dict]) -> list[dict]:
    """履歴に今回の行を重ねる。過去分の TFTC からの取り込みは成功するまでの1回だけ。"""
    history = load_etf_history(rt.history_path)
    base_rows, backfilled = history["rows"], history["backfilled"]
    if not backfilled:
        imported: list[dict] = []
        backfilled = rt.guard(
            "etf_backfill",
            "ETF の過去分（TFTC）",
            lambda: imported.extend(etf.fetch_tftc_history()),
        )
        base_rows = merge_etf_rows(imported, base_rows, config.ETF_HISTORY_KEEP_DAYS)

    merged = merge_etf_rows(base_rows, rows, config.ETF_HISTORY_KEEP_DAYS)
    if not rt.dry_run:
        save_etf_history(rt.history_path, {"backfilled": backfilled, "rows": merged})
    return merged


def check_spot_etf(rt: Runtime) -> None:
    rows = _fetch_etf_rows(rt)
    history = _update_etf_history(rt, rows)
    latest = rows[0]
    stored_date = rt.store.get("flows", "spot_etf", "date")
    # TFTC は SoSoValue より遅れるので、「違う日」ではなく「新しい日」だけを送る
    if stored_date is not None and latest["date"] <= stored_date:
        logger.info("[spot_etf] 新しいデータなし（最新 %s）", latest["date"])
        return

    older = [row["value"] for row in history if row["date"] < latest["date"]]
    source = etf.TFTC_ATTRIBUTION if latest["source"] == "tftc" else "SoSoValue"
    display = {
        "label": "現物BTC ETF",
        "unit": "取引日",
        "source": source,
        "format_value": notifier.format_usd_flow,
    }
    _publish_flow(rt, "spot_etf", latest, older[-config.FLOW_LOOKBACK_DAYS:], display)


def check_exchange_netflow(rt: Runtime) -> None:
    rows = coinmetrics.fetch_exchange_netflow_history()
    latest = rows[0]
    stored_date = rt.store.get("flows", "exchange_netflow", "date")
    if stored_date is not None and latest["date"] <= stored_date:
        logger.info("[exchange_netflow] 新しいデータなし（最新 %s）", latest["date"])
        return

    older = [row["value"] for row in rows[1:]]
    display = {
        "label": "取引所BTC純流入",
        "unit": "日",
        "source": "CoinMetrics",
        "format_value": notifier.format_btc_flow,
    }
    _publish_flow(
        rt, "exchange_netflow", latest, older[: config.FLOW_LOOKBACK_DAYS], display
    )


def run_daily_flows(rt: Runtime) -> None:
    rt.guard("spot_etf", "現物BTC ETF", lambda: check_spot_etf(rt))
    rt.guard("exchange_netflow", "取引所BTC純流入", lambda: check_exchange_netflow(rt))


# ── 毎時の監視 ─────────────────────────────────────────────────────────────


def fill_alert_prices(rt: Runtime, now: datetime) -> None:
    if rt.dry_run or rt.alert_log_path is None:
        return
    filled = alert_log.fill_prices(rt.alert_log_path, now)
    logger.info("通知後の BTC 価格を %d 欄埋めました", filled)


def run_hourly(rt: Runtime, clock: release_watch.Clock | None = None) -> None:
    """
    いつもの確認を先に済ませてから、発表を待つ（待っている間に VIX などが遅れないように）。
    予備確認は、この回の待ち受けの対象になった発表を見ない（二重通知を防ぐ）。
    """
    clock = clock or release_watch.Clock()
    now = clock.now()
    if release_calendar.is_stale(rt.store.get("calendar"), now):
        release_calendar.refresh(rt, now)
    due = release_watch.due_events(rt.store.state, now)
    watched = {event["key"] for event in due}

    rt.guard("vix", "VIX", lambda: check_vix(rt))
    rt.guard("fomc", "FOMC", lambda: fomc_alerts.check_documents(rt))
    rt.guard("fomc_outlook", "FOMCの利下げ確率", lambda: fomc_alerts.check_outlook(rt, now))
    run_daily_flows(rt)
    for release, spec in config.RELEASES.items():
        if release in watched:
            continue
        rt.guard(release, spec["label"], lambda release=release: macro.backup_check(rt, release))

    if due:
        release_watch.run(rt, due, clock)
    rt.guard("alert_prices", "通知後の BTC 価格（OKX）",
             lambda: fill_alert_prices(rt, datetime.now(timezone.utc)))

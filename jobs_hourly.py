"""毎時の監視：VIX、FOMC、日次フロー、経済指標（経済指標は Phase B で発表待ち受けへ移す）。"""

import logging
import os
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import config
import notifier
import rules
import summarizer
from fetchers import coinmetrics, etf, fomc, fred, market
from rules import Verdict
from runtime import Runtime, redact
from state_store import load_etf_history, merge_etf_rows, save_etf_history

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
EASTERN = ZoneInfo("America/New_York")
NO_FORECAST = "予想値なし（前回値と比較）"
CPI_ROWS = 15     # 前年比の基準月まで届くように多めに取る
MONTHLY_ROWS = 3  # 今月・先月・先々月
FOMC_ACTIONS = {"lower": "利下げ", "raise": "利上げ", "maintain": "据え置き"}


# ── VIX ───────────────────────────────────────────────────────────────────


def check_vix(rt: Runtime) -> None:
    value = market.fetch_market("vix")["value"]
    baseline = rt.store.get("market", "vix", "value")
    result = rules.evaluate_vix(value, baseline)
    action = result["action"]

    if action == "alert":
        verdict = rules.judge_change("vix", value, baseline)
        lines = [f"VIX {value:.1f}（前回通知 {baseline:.1f}）{result['change_pct']:+.1f}%"]
        rt.send(notifier.format_alert("VIX急騰", verdict, lines))
    if action in ("alert", "rebase", "init"):
        rt.store.set(("market", "vix"), {"value": value})


# ── 経済指標（Phase B までは前回値との比較）────────────────────────────────


def _release_line(key: str) -> str:
    """対象月とは別に、発表時刻を示す行。取れなければ検知時刻を出す。"""
    try:
        release_date = fred.fetch_release_date(key)
    except Exception as exc:  # 時刻が取れないだけで通知を止めない
        logger.warning("[%s] 発表日を取得できず: %s", key, redact(str(exc)))
        detected = datetime.now(JST)
        return f"検知 {detected.month}/{detected.day} {detected:%H:%M}（日本時間）"
    hour, minute = config.RELEASE_TIME_ET
    released = (
        datetime.fromisoformat(release_date)
        .replace(hour=hour, minute=minute, tzinfo=EASTERN)
        .astimezone(JST)
    )
    return f"発表 {released.month}/{released.day} {released:%H:%M}（日本時間）"


def _macro_message(key: str, title: str, verdict: Verdict, lines: list[str]) -> str:
    footer = [NO_FORECAST, _release_line(key), f"🔗 {fred.series_url(key)}"]
    return notifier.format_alert(title, verdict, [*lines, *footer])


def _check_release(
    rt: Runtime,
    key: str,
    limit: int,
    build_message: Callable[[list[dict], dict], str],
) -> None:
    """新しい対象期間が出ていたら通知して保存する。初めての指標は記録だけする。"""
    rows = fred.fetch_observations(key, limit=limit)
    latest = rows[0]
    stored = rt.store.get(key, default={})
    stored_date = stored.get("date")

    if stored_date is None:
        rt.store.set((key,), latest)
        return
    if latest["date"] <= stored_date:
        logger.info("[%s] 新しいデータなし（最新 %s）", key, latest["date"])
        return
    rt.send(build_message(rows, stored))
    rt.store.set((key,), latest)


def _rate_line(label: str, calculate: Callable, rows: list[dict], date_str: str) -> str:
    current = notifier.format_signed_percent(calculate(rows, date_str))
    previous = notifier.format_signed_percent(calculate(rows, rules.months_before(date_str, 1)))
    return f"{label} {current}（先月 {previous}）"


def _cpi_message(rows: list[dict], stored: dict) -> str:
    date_str = rows[0]["date"]
    core = fred.fetch_observations("core_cpi", limit=CPI_ROWS)
    core_nsa = fred.fetch_observations("core_cpi_nsa", limit=CPI_ROWS)
    headline_nsa = fred.fetch_observations("cpi_nsa", limit=CPI_ROWS)

    verdict = rules.judge_change(
        "core_cpi",
        rules.month_over_month(core, date_str),
        rules.month_over_month(core, rules.months_before(date_str, 1)),
    )
    lines = [
        _rate_line("コア前月比", rules.month_over_month, core, date_str) + " ← 判定に使用",
        _rate_line("コア前年比", rules.year_over_year, core_nsa, date_str),
        _rate_line("総合前月比", rules.month_over_month, rows, date_str),
        _rate_line("総合前年比", rules.year_over_year, headline_nsa, date_str),
    ]
    title = f"CPI（{notifier.format_month(date_str)}）"
    return _macro_message("core_cpi", title, verdict, lines)


def _format_jobs_change(value: float | None) -> str:
    return "―" if value is None else f"{value:+,.0f}千人"


def _nfp_message(rows: list[dict], stored: dict) -> str:
    date_str = rows[0]["date"]
    current = rules.monthly_change(rows, date_str)
    previous = rules.monthly_change(rows, rules.months_before(date_str, 1))
    line = (
        f"雇用者数の増減 {_format_jobs_change(current)}"
        f"（先月 {_format_jobs_change(previous)}）"
    )
    title = f"雇用統計（{notifier.format_month(date_str)}）"
    return _macro_message("nfp", title, rules.judge_change("nfp", current, previous), [line])


def _unrate_message(rows: list[dict], stored: dict) -> str:
    current, previous = rows[0]["value"], stored.get("value")
    previous_text = "―" if previous is None else f"{previous:.1f}%"
    line = f"失業率 {current:.1f}%（前回 {previous_text}）"
    title = f"失業率（{notifier.format_month(rows[0]['date'])}）"
    return _macro_message("unrate", title, rules.judge_change("unrate", current, previous), [line])


def _icsa_message(rows: list[dict], stored: dict) -> str:
    current, previous = rows[0]["value"], stored.get("value")
    previous_text = "―" if previous is None else f"{previous:,.0f}件"
    line = f"申請件数 {current:,.0f}件（前回 {previous_text}）"
    title = f"新規失業保険申請（{notifier.format_week(rows[0]['date'])}）"
    return _macro_message("icsa", title, rules.judge_change("icsa", current, previous), [line])


def _core_pce_message(rows: list[dict], stored: dict) -> str:
    date_str = rows[0]["date"]
    verdict = rules.judge_change(
        "core_pce",
        rules.month_over_month(rows, date_str),
        rules.month_over_month(rows, rules.months_before(date_str, 1)),
    )
    line = _rate_line("コアPCE前月比", rules.month_over_month, rows, date_str)
    title = f"コアPCE（{notifier.format_month(date_str)}）"
    return _macro_message("core_pce", title, verdict, [line])


def check_cpi(rt: Runtime) -> None:
    _check_release(rt, "cpi", CPI_ROWS, _cpi_message)


def check_nfp(rt: Runtime) -> None:
    _check_release(rt, "nfp", MONTHLY_ROWS, _nfp_message)


def check_unrate(rt: Runtime) -> None:
    _check_release(rt, "unrate", 1, _unrate_message)


def check_icsa(rt: Runtime) -> None:
    _check_release(rt, "icsa", 1, _icsa_message)


def check_core_pce(rt: Runtime) -> None:
    _check_release(rt, "core_pce", MONTHLY_ROWS, _core_pce_message)


# ── FOMC ──────────────────────────────────────────────────────────────────


def _is_new_document(rt: Runtime, section: str, document: dict | None) -> bool:
    """未通知の文書なら True。初めての場合は記録だけして False を返す。"""
    if document is None:
        return False
    stored_date = rt.store.get("fomc", section, "date")
    if stored_date is None:
        rt.store.set(("fomc", section), document)
        return False
    return document["date"] > stored_date


def _check_statement(rt: Runtime, document: dict | None) -> None:
    if not _is_new_document(rt, "statement", document):
        return
    decision = rules.parse_fomc_decision(fomc.fetch_content(document["url"]))
    if decision is None:
        decision_line = "決定を本文から読み取れませんでした"
    else:
        action = FOMC_ACTIONS[decision["action"]]
        decision_line = (
            f"決定：{action}（誘導目標 {decision['lower']:.2f}〜{decision['upper']:.2f}%）"
        )
    title = f"FOMC声明（{notifier.format_day(document['date'])}）"
    lines = [decision_line, "予想値なし（市場の織り込みとの比較は未対応）", f"🔗 {document['url']}"]
    rt.send(notifier.format_alert(title, rules.judge_fomc(decision), lines))
    rt.store.set(("fomc", "statement"), document)


def _check_minutes(rt: Runtime, document: dict | None) -> None:
    if not _is_new_document(rt, "minutes", document):
        return
    summary = summarizer.summarize_minutes(fomc.fetch_content(document["url"]), document["date"])
    verdict = Verdict(summary["judgment"], summary["emoji"])
    title = f"FOMC議事録（{notifier.format_day(document['date'])}会合）"
    rt.send(notifier.format_alert(title, verdict, [summary["summary"], f"🔗 {document['url']}"]))
    rt.store.set(("fomc", "minutes"), document)


def check_fomc(rt: Runtime) -> None:
    documents = fomc.fetch_fomc_documents()
    rt.guard(
        "fomc_statement", "FOMC声明", lambda: _check_statement(rt, documents.get("statement"))
    )
    rt.guard("fomc_minutes", "FOMC議事録", lambda: _check_minutes(rt, documents.get("minutes")))


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
    rt.send(notifier.format_alert(title, rules.judge_flow(key, value), lines))
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

_MACRO_CHECKS = (
    ("cpi", "CPI", check_cpi),
    ("nfp", "雇用統計", check_nfp),
    ("unrate", "失業率", check_unrate),
    ("icsa", "新規失業保険申請", check_icsa),
    ("core_pce", "コアPCE", check_core_pce),
)


def run_hourly(rt: Runtime) -> None:
    rt.guard("vix", "VIX", lambda: check_vix(rt))
    rt.guard("fomc", "FOMC", lambda: check_fomc(rt))
    run_daily_flows(rt)
    for key, label, check in _MACRO_CHECKS:
        rt.guard(key, label, lambda check=check: check(rt))

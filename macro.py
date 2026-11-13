"""
経済指標の実績・判定・通知文。発表待ち受けと、毎時の予備確認で共通に使う。

発表（release）ごとに、取得先から「スナップショット」を作り、そこから通知を組み立てる。
- CPI・雇用統計：BLS（キーがあるとき）か FRED の系列
- 新規失業保険申請・コアPCE：SoSoValue の実績か FRED の系列
"""

import logging
import os
from datetime import date, timedelta
from typing import Callable

import config
import forecasts
import notifier
import rules
from fetchers import bls, fred, sosovalue
from release_calendar import JST, at_eastern
from runtime import Runtime

logger = logging.getLogger(__name__)

CPI_ROWS = 15     # 前年比の基準月まで届くように多めに取る
MONTHLY_ROWS = 3  # 今月・先月・先々月
WEEKLY_ROWS = 2   # 今週・前週
YOY_BASE_MISSING = "—（基準月のデータなし）"  # 2025年10月など、欠測の月が基準になるとき
NO_FORECAST = "予想値なし（前回値と比較）"
SATURDAY = 5

# 発表ごとの state のキー。先頭が新着の判定に使うキー
STATE_KEYS = {
    "cpi": ("cpi",),
    "employment": ("nfp", "unrate"),
    "icsa": ("icsa",),
    "core_pce": ("core_pce",),
}
FRED_KEYS = {
    "cpi": ("cpi", "cpi_nsa", "core_cpi", "core_cpi_nsa"),
    "employment": ("nfp", "unrate"),
    "icsa": ("icsa",),
    "core_pce": ("core_pce",),
}
FRED_ROWS = {"cpi": CPI_ROWS, "employment": MONTHLY_ROWS, "icsa": WEEKLY_ROWS,
             "core_pce": MONTHLY_ROWS}
LINK_KEY = {"cpi": "core_cpi", "employment": "nfp", "icsa": "icsa", "core_pce": "core_pce"}
SOSOVALUE_ACTUAL = {"icsa": "icsa", "core_pce": "core_pce_mom"}  # 発表 → 予想の指標名
BLS_RELEASES = ("cpi", "employment")


def primary_key(release: str) -> str:
    return STATE_KEYS[release][0]


def is_notified(state: dict, release: str, release_date: str) -> bool:
    return (state.get(primary_key(release)) or {}).get("release") == release_date


# ── スナップショット ────────────────────────────────────────────────────────


def fred_snapshot(release: str) -> dict:
    rows = {key: fred.fetch_observations(key, limit=FRED_ROWS[release])
            for key in FRED_KEYS[release]}
    return {"release": release, "source": "fred", "rows": rows}


def bls_available() -> bool:
    return bool(os.environ.get("BLS_API_KEY"))


def bls_snapshot(release: str, today: date | None = None) -> dict:
    keys = FRED_KEYS[release]
    series = bls.fetch_series([config.BLS_SERIES[key] for key in keys], today)
    rows = {key: series[config.BLS_SERIES[key]] for key in keys}
    return {"release": release, "source": "bls", "rows": rows}


def sosovalue_snapshot(release: str, release_date: str) -> dict | None:
    """発表日の行に実績が入っていれば返す。まだ空なら None。"""
    metric = SOSOVALUE_ACTUAL[release]
    spec = config.FORECAST_METRICS[metric]
    row = forecasts.sosovalue_row_on(sosovalue.fetch_event_history(spec["sosovalue"]),
                                     release_date)
    if row is None or not row["actual"]:
        return None
    actual = forecasts.parse_sosovalue(row["actual"], spec["unit"])
    if actual is None:
        return None
    previous = forecasts.parse_sosovalue(row["previous"], spec["unit"])
    return {"release": release, "source": "sosovalue", "actual": actual, "previous": previous}


def period_of(snapshot: dict | None) -> str | None:
    """系列の最新の対象期間。CPI などで系列の更新がそろっていなければ None（まだ途中）。"""
    if snapshot is None or "rows" not in snapshot:
        return None
    latest = {rows[0]["date"] for rows in snapshot["rows"].values() if rows}
    if len(latest) != 1 or len(snapshot["rows"]) != len(FRED_KEYS[snapshot["release"]]):
        return None
    return latest.pop()


def is_new(state: dict, snapshot: dict | None) -> bool:
    """スナップショットに、まだ保存していない新しい実績があるか。"""
    if snapshot is None:
        return False
    if snapshot["source"] == "sosovalue":
        return True
    period = period_of(snapshot)
    stored = (state.get(primary_key(snapshot["release"])) or {}).get("date")
    return period is not None and (stored is None or period > stored)


# ── 表示の部品 ─────────────────────────────────────────────────────────────


def _percent(value: float, signed: bool = True) -> str:
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def _man(thousands: float) -> str:
    """千人単位を「万」で表す（63 → 6.3万）。"""
    return f"{thousands / 10:.1f}万"


def _format(metric: str, value: float, for_range: bool = False) -> str:
    unit = config.FORECAST_METRICS[metric]["unit"]
    if metric == "unrate":
        return _percent(value, signed=False)
    if unit == "percent":
        return _percent(value)
    if unit == "thousands":
        text = _man(value)
        return text if for_range else (f"+{text}" if value > 0 else text) + "人"
    return f"{value / 10_000:.1f}万" + ("" if for_range else "件")


def range_text(metric: str, result: rules.ForecastResult) -> str:
    unit = config.FORECAST_METRICS[metric]["unit"]
    suffix = {"thousands": "人", "count": "件"}.get(unit, "")
    low, high = _format(metric, result.low, True), _format(metric, result.high, True)
    body = low if result.low == result.high else f"{low}〜{high}"
    return body + suffix


def _metric_line(entry: dict, forecast_values: dict) -> tuple[str, rules.ForecastResult | None]:
    """
    entry：{"metric", "label", "actual", "actual_text", "previous_text", "previous_label"}
    予想があれば予想と比べ、なければ前回の値を並べる。
    """
    metric = entry["metric"]
    result = rules.compare_with_forecasts(
        metric, entry["actual"], forecasts.available(forecast_values.get(metric)).values()
    )
    if result is not None:
        label = rules.OUTCOME_LABELS[result.outcome]
        expected = range_text(metric, result)
        return f"{entry['label']} {entry['actual_text']}（予想 {expected}）{label}", result
    previous = f"（{entry['previous_label']} {entry['previous_text']}）"
    return f"{entry['label']} {entry['actual_text']}{previous}", None


def release_time_text(release: str, release_date: str) -> str:
    released = at_eastern(release_date, config.RELEASES[release]["time_et"]).astimezone(JST)
    return f"発表 {released.month}/{released.day} {released:%H:%M}（日本時間）"


# ── CPI ───────────────────────────────────────────────────────────────────


def _mom_text(rows: list[dict], date_str: str) -> str:
    return notifier.format_signed_percent(rules.month_over_month(rows, date_str))


def _yoy_value(rows: list[dict], date_str: str) -> tuple[float | None, str]:
    """前年比と表示。対象月はあるのに基準月（12か月前）が欠測なら、その理由を書く。"""
    value = rules.year_over_year(rows, date_str)
    if value is not None:
        return value, notifier.format_signed_percent(value)
    has_target = any(row["date"] == date_str for row in rows)
    if has_target and rules.is_missing_month(rows, rules.months_before(date_str, 12)):
        return None, YOY_BASE_MISSING
    return None, notifier.format_signed_percent(None)


def _cpi_entries(rows: dict, period: str) -> list[dict]:
    previous_month = rules.months_before(period, 1)
    entries = []
    for metric, label, key, kind in (
        ("core_cpi_mom", "コア前月比", "core_cpi", "mom"),
        ("core_cpi_yoy", "コア前年比", "core_cpi_nsa", "yoy"),
        ("cpi_mom", "総合前月比", "cpi", "mom"),
        ("cpi_yoy", "総合前年比", "cpi_nsa", "yoy"),
    ):
        series = rows[key]
        if kind == "mom":
            actual = rules.month_over_month(series, period)
            actual_text = notifier.format_signed_percent(actual)
            previous_text = _mom_text(series, previous_month)
        else:
            actual, actual_text = _yoy_value(series, period)
            previous_text = _yoy_value(series, previous_month)[1]
        entries.append({"metric": metric, "label": label, "actual": actual,
                        "actual_text": actual_text, "previous_text": previous_text,
                        "previous_label": "先月"})
    return entries


def _cpi_report(snapshot: dict, values: dict) -> dict:
    rows = snapshot["rows"]
    period = period_of(snapshot)
    entries = _cpi_entries(rows, period)
    lines, results = [], []
    for entry in entries:
        line, result = _metric_line(entry, values)
        lines.append(line)
        results.append(result)
    lines[0] += " ← 判定に使用"
    core = rows["core_cpi"]
    decisive, fallback_previous = results[0], rules.month_over_month(
        core, rules.months_before(period, 1))
    verdict = decisive.verdict if decisive else rules.judge_change(
        "core_cpi", entries[0]["actual"], fallback_previous)
    if decisive and results[1] and rules.is_opposite(decisive.verdict, results[1].verdict):
        lines.append("（前年比は逆方向）")
    return {
        "title": f"CPI（{notifier.format_month(period)}）",
        "period": period, "lines": lines, "verdict": verdict,
        "decisive": _decisive_record(entries[0], decisive, fallback_previous),
        "has_forecast": decisive is not None,
        "state": {"cpi": {"date": period, "value": rows["cpi"][0]["value"]}},
    }


# ── 雇用統計 ──────────────────────────────────────────────────────────────


def _value_at(rows: list[dict], date_str: str) -> float | None:
    return next((row["value"] for row in rows if row["date"] == date_str), None)


def _employment_report(snapshot: dict, values: dict) -> dict:
    rows = snapshot["rows"]
    period = period_of(snapshot)
    previous_month = rules.months_before(period, 1)
    nfp_rows, unrate_rows = rows["nfp"], rows["unrate"]
    change = rules.monthly_change(nfp_rows, period)
    previous_change = rules.monthly_change(nfp_rows, previous_month)
    unrate = _value_at(unrate_rows, period)
    previous_unrate = _value_at(unrate_rows, previous_month)

    def jobs_text(value: float | None) -> str:
        return "―" if value is None else _format("nfp", value)

    nfp_entry = {"metric": "nfp", "label": "NFP", "actual": change,
                 "actual_text": jobs_text(change), "previous_text": jobs_text(previous_change),
                 "previous_label": "先月"}
    unrate_entry = {"metric": "unrate", "label": "失業率", "actual": unrate,
                    "actual_text": "―" if unrate is None else _format("unrate", unrate),
                    "previous_text": "―" if previous_unrate is None
                    else _format("unrate", previous_unrate),
                    "previous_label": "先月"}
    nfp_line, nfp_result = _metric_line(nfp_entry, values)
    unrate_line, unrate_result = _metric_line(unrate_entry, values)
    verdict = nfp_result.verdict if nfp_result else rules.judge_change(
        "nfp", change, previous_change)
    unrate_verdict = unrate_result.verdict if unrate_result else rules.judge_change(
        "unrate", unrate, previous_unrate)
    lines = [nfp_line + " ← 判定に使用", unrate_line]
    if rules.is_opposite(verdict, unrate_verdict):
        lines.append("（失業率は逆方向）")
    return {
        "title": f"雇用統計（{notifier.format_month(period)}）",
        "period": period, "lines": lines, "verdict": verdict,
        "decisive": _decisive_record(nfp_entry, nfp_result, previous_change),
        "has_forecast": nfp_result is not None,
        "state": {"nfp": {"date": period, "value": nfp_rows[0]["value"]},
                  "unrate": {"date": period, "value": unrate}},
    }


# ── 新規失業保険申請・コアPCE ───────────────────────────────────────────────


def week_ending_before(release_date: str) -> str:
    """申請件数の対象週の終わり（発表日の前の土曜日）。"""
    day = date.fromisoformat(release_date) - timedelta(days=1)
    while day.weekday() != SATURDAY:
        day -= timedelta(days=1)
    return day.isoformat()


def month_before_release(release_date: str) -> str:
    return rules.months_before(release_date[:7] + "-01", 1)


def _single_report(snapshot: dict, values: dict, release_date: str) -> dict:
    release = snapshot["release"]
    metric = SOSOVALUE_ACTUAL[release]
    if snapshot["source"] == "sosovalue":
        actual, previous = snapshot["actual"], snapshot["previous"]
        period = (week_ending_before(release_date) if release == "icsa"
                  else month_before_release(release_date))
        state = {}  # 対象期間と水準は FRED の更新で予備確認が記録する
    else:
        rows = snapshot["rows"][release]
        period = rows[0]["date"]
        if release == "icsa":
            actual = rows[0]["value"]
            previous = rows[1]["value"] if len(rows) > 1 else None
        else:
            actual = rules.month_over_month(rows, period)
            previous = rules.month_over_month(rows, rules.months_before(period, 1))
        state = {release: {"date": period, "value": rows[0]["value"]}}

    def text(value: float | None) -> str:
        return "―" if value is None else _format(metric, value)

    label = "申請件数" if release == "icsa" else "コアPCE前月比"
    entry = {"metric": metric, "label": label, "actual": actual, "actual_text": text(actual),
             "previous_text": text(previous),
             "previous_label": "前回" if release == "icsa" else "先月"}
    line, result = _metric_line(entry, values)
    judge_key = "icsa" if release == "icsa" else "core_pce"
    verdict = result.verdict if result else rules.judge_change(judge_key, actual, previous)
    if release == "icsa":
        title = f"新規失業保険申請（{notifier.format_week(period)}）"
    else:
        title = f"コアPCE（{notifier.format_month(period)}）"
    return {"title": title, "period": period, "lines": [line], "verdict": verdict,
            "decisive": _decisive_record(entry, result, previous),
            "has_forecast": result is not None, "state": state}


# ── 組み立てと送信 ─────────────────────────────────────────────────────────


def _decisive_record(entry: dict, result: rules.ForecastResult | None,
                     previous: float | None) -> dict:
    compared = ({"forecast_low": result.low, "forecast_high": result.high}
                if result else {"previous": previous})
    return {"metric": entry["metric"], "actual": entry["actual"], "compared": compared,
            "outcome": result.outcome if result else None}


_BUILDERS: dict[str, Callable] = {
    "cpi": lambda s, v, d: _cpi_report(s, v),
    "employment": lambda s, v, d: _employment_report(s, v),
    "icsa": _single_report,
    "core_pce": _single_report,
}


def build_report(snapshot: dict, forecast_values: dict, release_date: str) -> dict:
    release = snapshot["release"]
    report = _BUILDERS[release](snapshot, forecast_values, release_date)
    footer = [] if report["has_forecast"] else [NO_FORECAST]
    timing = release_time_text(release, release_date)
    sources = forecasts.sources_text(
        {m: forecast_values.get(m) for m in forecasts.metrics_for(release)})
    footer.append(f"{timing} ／ 予想値の出所：{sources}" if sources else timing)
    footer.append(f"🔗 {fred.series_url(LINK_KEY[release])}")
    message = notifier.format_alert(report["title"], report["verdict"],
                                    [*report["lines"], *footer])
    return {**report, "release": release, "release_date": release_date,
            "source": snapshot["source"], "message": message}


def alert_record(report: dict) -> dict:
    decisive = report["decisive"]
    return {
        "indicator": report["release"], "kind": "immediate",
        "actual": decisive["actual"], "compared": decisive["compared"],
        "verdict": report["verdict"].label, "outcome": decisive["outcome"],
        "period": report["period"], "release_date": report["release_date"],
        "source": report["source"],
    }


def publish(rt: Runtime, report: dict) -> None:
    """通知してから保存する。送れなければ何も保存せず、次の回に任せる。"""
    rt.send(report["message"], alert_record(report))
    save_release(rt, report["release"], report["release_date"], report["state"])


def save_release(rt: Runtime, release: str, release_date: str, updates: dict) -> None:
    """通知済みの発表日と、分かっていれば対象期間・水準を保存する。"""
    for key in STATE_KEYS[release]:
        current = rt.store.get(key, default={}) or {}
        rt.store.set((key,), {**current, **updates.get(key, {}), "release": release_date})


# ── 毎時の予備確認 ─────────────────────────────────────────────────────────


def backup_check(rt: Runtime, release: str) -> None:
    """
    待ち受けの取りこぼしを FRED で拾う。

    - 初めての指標は記録だけする
    - FRED に新しい期間が出ていても、その発表日の分を通知済みなら、記録だけして通知しない
    """
    snapshot = fred_snapshot(release)
    period = period_of(snapshot)
    stored = rt.store.get(primary_key(release), default={}) or {}
    if period is None:
        logger.info("[%s] 系列の更新がそろっていない", release)
        return
    state_updates = _state_from_rows(snapshot, period)
    if stored.get("date") is None:
        for key, value in state_updates.items():
            rt.store.set((key,), {**(rt.store.get(key, default={}) or {}), **value})
        return
    if period <= stored["date"]:
        logger.info("[%s] 新しいデータなし（最新 %s）", release, period)
        return
    release_date = fred.fetch_release_date(LINK_KEY[release])
    if stored.get("release") == release_date:
        logger.info("[%s] %s の発表は待ち受けで通知済み。記録だけ更新", release, release_date)
        save_release(rt, release, release_date, state_updates)
        return
    values = forecasts.stored_for(rt.store.get("forecasts", release), release_date)
    publish(rt, build_report(snapshot, values, release_date))


def _state_from_rows(snapshot: dict, period: str) -> dict:
    rows = snapshot["rows"]
    if snapshot["release"] == "employment":
        return {"nfp": {"date": period, "value": rows["nfp"][0]["value"]},
                "unrate": {"date": period, "value": _value_at(rows["unrate"], period)}}
    key = primary_key(snapshot["release"])
    return {key: {"date": period, "value": rows[key][0]["value"]}}

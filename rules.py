"""強気・弱気の判定と、その材料の計算。通信も保存もしない純粋関数だけを置く。"""

import re
import statistics
from typing import Iterable, NamedTuple

import config


class Verdict(NamedTuple):
    label: str
    emoji: str


BULLISH = Verdict("強気", "🟢")
BEARISH = Verdict("弱気", "🔴")
NEUTRAL = Verdict("中立", "🟡")
UNKNOWN = Verdict("判定不可", "⚪")

_VERDICT_BY_NAME = {"bullish": BULLISH, "bearish": BEARISH, "neutral": NEUTRAL}


# ── 判定 ───────────────────────────────────────────────────────────────────


def judge_change(key: str, current: float | None, previous: float | None) -> Verdict:
    """前回の値からの上下で判定する。向きと「同じ」の幅は config に従う。"""
    if current is None or previous is None:
        return UNKNOWN
    diff = current - previous
    if abs(diff) <= config.FLAT_BAND.get(key, 0.0):
        return NEUTRAL
    return BULLISH if (diff > 0) == (config.DIRECTION[key] > 0) else BEARISH


def judge_flow(key: str, value: float) -> Verdict:
    """流れの量は、前日との差ではなく、その日の符号で判定する。"""
    if value == 0:
        return NEUTRAL
    return BULLISH if (value > 0) == (config.FLOW_DIRECTION[key] > 0) else BEARISH


def judge_fomc(decision: dict | None) -> Verdict:
    if decision is None:
        return UNKNOWN
    return _VERDICT_BY_NAME[config.FOMC_DECISION_VERDICT[decision["action"]]]


# ── 普段の振れ幅 ───────────────────────────────────────────────────────────


def flow_band(values: Iterable[float]) -> dict | None:
    """平均 ± FLOW_SIGMA × 標準偏差。履歴が足りなければ None。"""
    samples = list(values)
    if len(samples) < config.FLOW_MIN_SAMPLES:
        return None
    mean = statistics.fmean(samples)
    margin = config.FLOW_SIGMA * statistics.stdev(samples)
    return {
        "mean": mean,
        "low": mean - margin,
        "high": mean + margin,
        "samples": len(samples),
    }


def is_outside_band(value: float, band: dict | None) -> bool | None:
    if band is None:
        return None
    return value < band["low"] or value > band["high"]


# ── VIX ───────────────────────────────────────────────────────────────────


def evaluate_vix(current: float, baseline: float | None) -> dict:
    """
    action の意味：
      init   基準がないので記録だけする
      alert  前回通知値から +10% 以上、かつ 20 超。通知して基準を更新する
      rebase 10% 以上下がった。通知せず基準だけ下げる（次の急騰を見逃さないため）
      hold   何もしない
    """
    if baseline is None:
        return {"action": "init", "change_pct": 0.0}
    change_pct = (current - baseline) / baseline * 100 if baseline else 0.0
    if change_pct >= config.VIX_ALERT_RISE_PCT and current > config.VIX_ALERT_LEVEL:
        return {"action": "alert", "change_pct": change_pct}
    if change_pct <= -config.VIX_BASELINE_DROP_PCT:
        return {"action": "rebase", "change_pct": change_pct}
    return {"action": "hold", "change_pct": change_pct}


# ── 月次系列の計算 ─────────────────────────────────────────────────────────


def months_before(date_str: str, months: int) -> str:
    """'YYYY-MM-01' の n か月前を同じ形で返す。"""
    year, month = int(date_str[:4]), int(date_str[5:7])
    index = year * 12 + (month - 1) - months
    return f"{index // 12}-{index % 12 + 1:02d}-01"


def _value_at(rows: list[dict], date_str: str) -> float | None:
    return next((row["value"] for row in rows if row["date"] == date_str), None)


def _percent_change(current: float | None, base: float | None) -> float | None:
    if current is None or not base:
        return None
    return round((current / base - 1) * 100, 1)


def month_over_month(rows: list[dict], date_str: str) -> float | None:
    """前月比（%、小数1桁）。前の月が欠測なら None。"""
    return _percent_change(_value_at(rows, date_str), _value_at(rows, months_before(date_str, 1)))


def year_over_year(rows: list[dict], date_str: str) -> float | None:
    """前年比（%、小数1桁）。基準の月が欠測なら None。"""
    return _percent_change(_value_at(rows, date_str), _value_at(rows, months_before(date_str, 12)))


def monthly_change(rows: list[dict], date_str: str) -> float | None:
    """前月からの増減（NFP）。前の月が欠測なら None。"""
    current = _value_at(rows, date_str)
    previous = _value_at(rows, months_before(date_str, 1))
    if current is None or previous is None:
        return None
    return current - previous


# ── FOMC声明の読み取り ─────────────────────────────────────────────────────

# 「decided to」で始まる形だけを読む。反対票の文（preferred to raise ...）を拾わないため。
_FOMC_DECISION_RE = re.compile(
    r"decided to (maintain|lower|raise) the target range for the federal funds rate"
    r"(?: by [\d/\- ]+ percentage points?)? (?:at|to) ([\d/\-]+) to ([\d/\-]+) percent",
    re.IGNORECASE,
)


def _parse_rate(text: str) -> float:
    """'3-1/2' や '4' を数値にする。"""
    whole, _, fraction = text.partition("-")
    if not fraction:
        return float(whole)
    numerator, denominator = fraction.split("/")
    return float(whole) + int(numerator) / int(denominator)


def parse_fomc_decision(text: str | None) -> dict | None:
    """声明の本文から決定と誘導目標の範囲を読み取る。読めなければ None。"""
    if not text:
        return None
    match = _FOMC_DECISION_RE.search(" ".join(text.split()))
    if match is None:
        return None
    try:
        lower, upper = _parse_rate(match.group(2)), _parse_rate(match.group(3))
    except ValueError:
        return None
    return {"action": match.group(1).lower(), "lower": lower, "upper": upper}

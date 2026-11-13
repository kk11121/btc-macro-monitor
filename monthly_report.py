"""
月次レポート（reports/YYYY-MM.md と Discord）。1日（日本時間）の朝のまとめの中で前月分を送る。

- 指標ごとの通知件数
- 当たり率（24時間後・72時間後）：強気のあとに上がり、弱気のあとに下がった割合。
  中立と価格がない行は除く
- 動きの大きさ：通知後の値動き幅の平均を、その月の全時間帯の平均で割った倍率
- 見直し候補：当たり率が低い指標
"""

import logging
import os
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import alert_log
import config
from fetchers import okx
from release_calendar import JST
from runtime import Runtime

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent / "reports"
SCORED_KINDS = ("immediate", "daily_flow")
HORIZONS = (24, 72)
INDICATOR_LABELS = {
    "vix": "VIX", "cpi": "CPI", "employment": "雇用統計", "icsa": "新規失業保険申請",
    "core_pce": "コアPCE", "fomc_statement": "FOMC声明", "fomc_minutes": "FOMC議事録",
    "fomc_outlook": "FOMC金利見通し", "spot_etf": "現物BTC ETF",
    "exchange_netflow": "取引所BTC純流入",
}
KIND_LABELS = {"immediate": "即時通知", "daily_flow": "日次フロー", "morning": "朝のまとめ",
               "monthly": "月次レポート", "error": "取得エラー", "recovery": "復旧"}


def previous_month(today: str) -> str:
    first = date.fromisoformat(today).replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """日本時間の月の始まりと終わり（UTC）。"""
    start = datetime.fromisoformat(f"{month}-01T00:00:00").replace(tzinfo=JST)
    following = (start + timedelta(days=32)).replace(day=1)
    return start.astimezone(timezone.utc), following.astimezone(timezone.utc)


def rows_in_month(rows: list[dict], month: str) -> list[dict]:
    start, end = month_bounds(month)
    return [r for r in rows if start <= datetime.fromisoformat(r["ts"]) < end]


def _move(row: dict, hours: int) -> float | None:
    base, later = row.get("btc_price"), row.get(f"btc_price_{hours}h")
    if not base or later is None:
        return None
    return later / base - 1


def _is_hit(row: dict, hours: int) -> bool | None:
    move = _move(row, hours)
    if move is None or row.get("verdict") not in ("強気", "弱気"):
        return None
    return move > 0 if row["verdict"] == "強気" else move < 0


def baseline_moves(closes: dict[datetime, float], month: str) -> dict[int, float | None]:
    """その月の全時間帯から、24時間後・72時間後の値動き幅（絶対値）の平均を出す。"""
    start, end = month_bounds(month)
    result = {}
    for hours in HORIZONS:
        moves = [abs(closes[t + timedelta(hours=hours)] / price - 1)
                 for t, price in closes.items()
                 if start <= t < end and (t + timedelta(hours=hours)) in closes and price]
        result[hours] = statistics.fmean(moves) if moves else None
    return result


def _rate(values: list[bool]) -> str:
    return "―" if not values else f"{sum(values) / len(values):.0%}（{len(values)}件）"


def summarize(rows: list[dict], baseline: dict[int, float | None]) -> list[dict]:
    """指標ごとの件数・当たり率・倍率。"""
    scored = [r for r in rows if r.get("kind") in SCORED_KINDS]
    stats = []
    for indicator in sorted({r.get("indicator") for r in scored}, key=str):
        group = [r for r in scored if r.get("indicator") == indicator]
        entry = {"indicator": indicator, "count": len(group),
                 "verdicts": {v: sum(1 for r in group if r.get("verdict") == v)
                              for v in ("強気", "弱気", "中立")}}
        for hours in HORIZONS:
            hits = [h for h in (_is_hit(r, hours) for r in group) if h is not None]
            moves = [abs(m) for m in (_move(r, hours) for r in group) if m is not None]
            base = baseline.get(hours)
            entry[f"hits_{hours}"] = hits
            entry[f"ratio_{hours}"] = (statistics.fmean(moves) / base
                                       if moves and base else None)
        stats.append(entry)
    return stats


def review_candidates(stats: list[dict]) -> list[dict]:
    result = []
    for entry in stats:
        hits = entry["hits_24"]
        if len(hits) >= config.MONTHLY_MIN_SAMPLES and \
                sum(hits) / len(hits) < config.MONTHLY_REVIEW_HIT_RATE:
            result.append(entry)
    return result


def _label(indicator: str | None) -> str:
    return INDICATOR_LABELS.get(indicator or "", indicator or "不明")


def _ratio(value: float | None) -> str:
    return "―" if value is None else f"{value:.2f}倍"


def build_markdown(month: str, rows: list[dict], stats: list[dict]) -> str:
    year, number = month.split("-")
    pending = sum(1 for r in rows if r.get("kind") in SCORED_KINDS
                  and r.get("btc_price_72h") is None)
    lines = [
        f"# 月次レポート {year}年{int(number)}月",
        "",
        f"対象：{month} の日本時間の1か月。当たり率と倍率は即時通知と日次フローで集計し、"
        "中立と価格がそろわない通知は除く。",
        "",
        "## 指標ごとの件数と当たり率",
        "",
        "| 指標 | 件数 | 強気 | 弱気 | 中立 | 当たり率（24時間後） | 当たり率（72時間後） "
        "| 値動き幅の倍率（24時間） | 値動き幅の倍率（72時間） |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for e in stats:
        v = e["verdicts"]
        lines.append(
            f"| {_label(e['indicator'])} | {e['count']} | {v['強気']} | {v['弱気']} | {v['中立']} "
            f"| {_rate(e['hits_24'])} | {_rate(e['hits_72'])} "
            f"| {_ratio(e['ratio_24'])} | {_ratio(e['ratio_72'])} |"
        )
    if not stats:
        lines.append("| （通知なし） | 0 | 0 | 0 | 0 | ― | ― | ― | ― |")
    candidates = review_candidates(stats)
    lines += ["", "## 見直し候補（判定と逆に動くことが多い指標）", ""]
    lines += ([f"- {_label(e['indicator'])}：24時間後の当たり率 {_rate(e['hits_24'])}。"
               "config.py の向きを見直す候補" for e in candidates]
              or [f"- なし（{config.MONTHLY_MIN_SAMPLES}件以上で当たり率が"
                  f"{config.MONTHLY_REVIEW_HIT_RATE:.0%}未満の指標はない）"])
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r.get("kind", "other")] = kinds.get(r.get("kind", "other"), 0) + 1
    lines += ["", "## 通知の種類ごとの件数", ""]
    lines += [f"- {KIND_LABELS.get(k, k)}：{n}件" for k, n in sorted(kinds.items())] or ["- なし"]
    lines += ["", "## 集計待ち", "",
              f"- 72時間後の価格がまだない通知：{pending}件（月末近くの通知は翌月に出そろう）", ""]
    return "\n".join(lines)


def build_discord(month: str, stats: list[dict], link: str | None) -> str:
    year, number = month.split("-")
    lines = [f"**【BTC Monitor】月次レポート {year}年{int(number)}月**"]
    if not stats:
        lines.append(" 集計対象の通知なし")
    for e in stats:
        lines.append(f" {_label(e['indicator'])} {e['count']}件 ／ "
                     f"当たり率 24h {_rate(e['hits_24'])}・72h {_rate(e['hits_72'])} ／ "
                     f"倍率 {_ratio(e['ratio_24'])}")
    candidates = review_candidates(stats)
    if candidates:
        lines.append(" 見直し候補：" + "、".join(_label(e["indicator"]) for e in candidates))
    if link:
        lines.append(f"🔗 {link}")
    return "\n".join(lines)


def _report_link(month: str) -> str | None:
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        return None
    return f"https://github.com/{repository}/blob/main/reports/{month}.md"


def run(
    rt: Runtime,
    today: str,
    force: bool = False,
    fetch_closes: Callable[[datetime, datetime], dict] = okx.fetch_hourly_closes,
    reports_dir: Path = REPORTS_DIR,
) -> None:
    """前月分を送る。同じ月は2回送らない（force なら送り直す）。"""
    month = previous_month(today)
    if not force and rt.store.get("monthly", "last_month") == month:
        logger.info("月次レポート（%s）は送信済み", month)
        return
    rows = rows_in_month(alert_log.read(rt.alert_log_path) if rt.alert_log_path else [], month)
    start, end = month_bounds(month)
    closes = fetch_closes(start, end + timedelta(hours=max(HORIZONS)))
    stats = summarize(rows, baseline_moves(closes, month))
    markdown = build_markdown(month, rows, stats)
    if not rt.dry_run:
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / f"{month}.md").write_text(markdown, encoding="utf-8")
    message = build_discord(month, stats, _report_link(month))
    rt.send(message, {"indicator": "monthly_report", "kind": "monthly", "actual": month})
    rt.store.set(("monthly",), {"last_month": month})

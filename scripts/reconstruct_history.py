"""
state.json の git 履歴から、過去の通知の日時と指標を復元し、その後の BTC の値動きを集計する。

使い方（リポジトリの直下で）：
    python scripts/reconstruct_history.py                  # 2026-05-24 から今日まで
    python scripts/reconstruct_history.py --until 2026-11-13

結果は reports/history-2026-05-24.md（集計）と reports/history-events.csv（一覧）に書く。
state.json は読むだけで書き換えない。

決め方：
- 旧形式（Phase A の 8cb19a9 まで）は、通知したときだけ状態を書き換えていた。
  値が変わったら通知とみなし、SEC は新しい seen_ids の数、恐怖&強欲はゾーンの変化、
  Funding Rate は当時の閾値（符号の反転か ±0.05% をまたいだとき）で数える
- 新形式（Phase A 以降）は、経済指標・FOMC・日次フローの更新、errors の増減、
  朝のまとめ（summary.last_date）を数える。VIX は上がったときだけ（下がったのは基準の付け替え）
- 形式が変わったコミット（移行）では数えない
- 時刻はボットのコミット時刻（通知から数分遅れる）
"""

import argparse
import csv
import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fetchers import okx  # noqa: E402

FUNDING_RATE_THRESHOLD = 0.0005  # 旧コードの閾値（8時間あたり 0.05%）
HORIZONS = (1, 24, 72)
OLD_DATED = ("cpi", "nfp", "unrate", "icsa")
NEW_DATED = ("cpi", "nfp", "unrate", "icsa", "core_pce")
LABELS = {
    "cpi": "CPI", "nfp": "雇用統計（NFP）", "unrate": "失業率", "icsa": "新規失業保険申請",
    "core_pce": "コアPCE", "fomc": "FOMC（声明・議事録）", "fomc_statement": "FOMC声明",
    "fomc_minutes": "FOMC議事録", "dxy": "DXY", "us10y": "米10年債", "nasdaq": "NASDAQ",
    "vix": "VIX", "usdt": "USDT供給量", "usdc": "USDC供給量", "btc_exchange": "取引所BTC残高",
    "m2sl": "M2", "walcl": "FRBバランスシート", "exchange_netflow": "取引所BTC純流入",
    "spot_etf": "現物BTC ETF", "fear_greed": "恐怖&強欲指数", "funding_rate": "Funding Rate",
    "sec": "SEC", "morning": "朝のまとめ", "error": "取得エラー", "recovery": "復旧",
}


# ── git ───────────────────────────────────────────────────────────────────────


def list_commits(since: str, until: str | None) -> list[tuple[str, str]]:
    command = ["git", "log", "--reverse", "--format=%H %cI", f"--since={since}"]
    if until:
        command.append(f"--until={until}")
    output = subprocess.run([*command, "--", "state.json"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout
    return [tuple(line.split(" ", 1)) for line in output.splitlines() if line.strip()]


def load_states(commits: list[tuple[str, str]]):
    """git cat-file --batch で各版の state.json を読む。読めない版は飛ばす。"""
    request = "".join(f"{sha}:state.json\n" for sha, _ in commits).encode()
    process = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT, input=request,
                             check=True, capture_output=True)
    data, position = process.stdout, 0
    for sha, committed in commits:
        header_end = data.index(b"\n", position)
        header = data[position:header_end].decode()
        position = header_end + 1
        if header.endswith("missing"):
            continue
        size = int(header.split()[-1])
        body = data[position:position + size]
        position += size + 1
        try:
            state = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(state, dict):
            yield datetime.fromisoformat(committed).astimezone(timezone.utc), state


# ── 差分から通知を数える ────────────────────────────────────────────────────────


def _get(state: dict, *path):
    node = state
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def is_new_format(state: dict) -> bool:
    return "schema_version" in state


def diff_old(prev: dict, cur: dict) -> list[tuple[str, int]]:
    events: list[tuple[str, int]] = []

    def changed(*path) -> bool:
        before, after = _get(prev, *path), _get(cur, *path)
        return after is not None and before != after

    for key in OLD_DATED:
        if changed(key, "date"):
            events.append((key, 1))
    if changed("fomc", "url") or changed("fomc", "date"):
        events.append(("fomc", 1))
    for key in ("dxy", "us10y", "nasdaq", "vix"):
        if changed("market", key, "value"):
            events.append((key, 1))
    for key in ("usdt", "usdc", "btc_exchange"):
        if changed("stablecoin", key, "value"):
            events.append((key, 1))
    for key in ("m2sl", "walcl"):
        if changed("liquidity", key, "date") or changed("liquidity", key, "value"):
            events.append((key, 1))
    if changed("glassnode", "etf_flow", "date"):
        events.append(("exchange_netflow", 1))
    before_zone = _get(prev, "glassnode", "fear_greed", "zone")
    if before_zone is not None and changed("glassnode", "fear_greed", "zone"):
        events.append(("fear_greed", 1))
    if _funding_notified(_get(prev, "glassnode", "funding_rate", "value"),
                         _get(cur, "glassnode", "funding_rate", "value")):
        events.append(("funding_rate", 1))
    sec = _new_sec_ids(prev.get("sec") or {}, cur.get("sec") or {})
    if sec:
        events.append(("sec", sec))
    return events


def _funding_notified(before, after) -> bool:
    if before is None or after is None:
        return False
    sign_changed = (after >= 0) != (before >= 0)
    crossed = (abs(before) >= FUNDING_RATE_THRESHOLD) != (abs(after) >= FUNDING_RATE_THRESHOLD)
    return sign_changed or crossed


def _new_sec_ids(prev: dict, cur: dict) -> int:
    """旧コードは日付が変わると seen_ids を数え直した。"""
    after = set(cur.get("seen_ids") or [])
    if not after:
        return 0
    if cur.get("date") != prev.get("date"):
        return len(after)
    return len(after - set(prev.get("seen_ids") or []))


def diff_new(prev: dict, cur: dict) -> list[tuple[str, int]]:
    events: list[tuple[str, int]] = []

    def updated(*path) -> bool:
        before, after = _get(prev, *path), _get(cur, *path)
        return before is not None and after is not None and before != after

    for key in NEW_DATED:
        if updated(key, "date"):
            events.append((key, 1))
    for section in ("statement", "minutes"):
        if updated("fomc", section, "date"):
            events.append((f"fomc_{section}", 1))
    for key in ("spot_etf", "exchange_netflow"):
        after = _get(cur, "flows", key, "date")
        if after is not None and after != _get(prev, "flows", key, "date"):
            events.append((key, 1))
    before_vix, after_vix = _get(prev, "market", "vix", "value"), _get(cur, "market", "vix",
                                                                        "value")
    if before_vix is not None and after_vix is not None and after_vix > before_vix:
        events.append(("vix", 1))
    before_errors, after_errors = set(prev.get("errors") or {}), set(cur.get("errors") or {})
    if after_errors - before_errors:
        events.append(("error", len(after_errors - before_errors)))
    if before_errors - after_errors:
        events.append(("recovery", len(before_errors - after_errors)))
    last_date = _get(cur, "summary", "last_date")
    if last_date is not None and last_date != _get(prev, "summary", "last_date"):
        events.append(("morning", 1))
    return events


def reconstruct(snapshots) -> list[dict]:
    """[(コミット時刻, state)] を古い順に受け取り、通知の一覧を返す。"""
    events, previous = [], None
    for committed, state in snapshots:
        if previous is not None and is_new_format(previous) == is_new_format(state):
            differ = diff_new if is_new_format(state) else diff_old
            for indicator, count in differ(previous, state):
                events.append({"ts": committed, "indicator": indicator, "count": count})
        previous = state
    return events


# ── BTC の値動き ────────────────────────────────────────────────────────────────


def price_at(closes: dict, moment: datetime) -> float | None:
    return closes.get(okx.floor_hour(moment))


def attach_moves(events: list[dict], closes: dict) -> list[dict]:
    result = []
    for event in events:
        base = price_at(closes, event["ts"])
        moves = {}
        for hours in HORIZONS:
            later = price_at(closes, event["ts"] + timedelta(hours=hours))
            moves[hours] = abs(later / base - 1) if base and later else None
        result.append({**event, "btc_price": base, "moves": moves})
    return result


def baseline(closes: dict) -> dict[int, float | None]:
    out = {}
    for hours in HORIZONS:
        values = [abs(closes[t + timedelta(hours=hours)] / p - 1) for t, p in closes.items()
                  if (t + timedelta(hours=hours)) in closes and p]
        out[hours] = statistics.fmean(values) if values else None
    return out


def aggregate(events: list[dict], base: dict) -> list[dict]:
    groups = defaultdict(list)
    for event in events:
        groups[event["indicator"]].append(event)
    rows = []
    for indicator, group in groups.items():
        entry = {"indicator": indicator, "count": sum(e["count"] for e in group),
                 "commits": len(group)}
        for hours in HORIZONS:
            moves = [e["moves"][hours] for e in group if e["moves"][hours] is not None]
            mean = statistics.fmean(moves) if moves else None
            entry[f"move_{hours}"] = mean
            entry[f"ratio_{hours}"] = mean / base[hours] if mean is not None and base[hours] \
                else None
        rows.append(entry)
    return sorted(rows, key=lambda r: -r["count"])


# ── 出力 ───────────────────────────────────────────────────────────────────────


def _pct(value: float | None) -> str:
    return "―" if value is None else f"{value:.2%}"


def _ratio(value: float | None) -> str:
    return "―" if value is None else f"{value:.2f}倍"


def render_markdown(since: str, until: str, events: list[dict], rows: list[dict],
                    base: dict) -> str:
    total = sum(e["count"] for e in events)
    months = sorted({e["ts"].strftime("%Y-%m") for e in events})
    by_month = Counter()
    for e in events:
        by_month[(e["indicator"], e["ts"].strftime("%Y-%m"))] += e["count"]
    lines = [
        f"# 過去の通知の復元（{since}〜{until}）",
        "",
        "state.json の git 履歴から、通知の日時と指標を復元した。当時の判定（強気・弱気）は"
        "記録がないので当たり率は出さず、通知のあとに BTC がどれだけ動いたかだけを測る。",
        "",
        f"- 復元した通知：{total}件（コミット {len(events)}件。SEC など1回のコミットで複数の"
        "通知があるものは件数に数える）",
        "- 時刻はボットのコミット時刻。通知から数分の遅れがある",
        "- 値動きは OKX の BTC-USDT 1時間足の終値で、通知時刻を含む足から1・24・72時間後の足"
        "までの変化の絶対値。全時間帯の平均と比べた倍率も出す（1倍より大きいほど、通知のあとに"
        "普段より大きく動いた）",
        f"- 全時間帯の平均の値動き幅：1時間 {_pct(base[1])}、24時間 {_pct(base[24])}、"
        f"72時間 {_pct(base[72])}",
        "",
        "## 指標ごと",
        "",
        "| 指標 | 件数 | 1時間後 | 24時間後 | 72時間後 | 倍率（1時間） | 倍率（24時間） "
        "| 倍率（72時間） |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {LABELS.get(r['indicator'], r['indicator'])} | {r['count']} "
            f"| {_pct(r['move_1'])} | {_pct(r['move_24'])} | {_pct(r['move_72'])} "
            f"| {_ratio(r['ratio_1'])} | {_ratio(r['ratio_24'])} | {_ratio(r['ratio_72'])} |")
    lines += ["", "## 月ごとの件数", "",
              "| 指標 | " + " | ".join(months) + " |",
              "|---|" + "---|" * len(months)]
    for r in rows:
        counts = [str(by_month[(r["indicator"], m)]) for m in months]
        lines.append(f"| {LABELS.get(r['indicator'], r['indicator'])} | " + " | ".join(counts)
                     + " |")
    lines += ["", "## 限界", "",
              "- 旧コードの Funding Rate の閾値は 0.05% として判定し直した",
              "- 旧コードの SEC は1日ごとに seen_ids を数え直したので、日付が変わった最初の"
              "コミットはその日の件数をまとめて数える",
              "- 形式が変わったコミット（Phase A の移行）では数えていない",
              "- Phase A 以降の朝のまとめと取得エラーも件数に入れている（値動きの比較の参考）", ""]
    return "\n".join(lines)


def write_csv(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts_utc", "indicator", "count", "btc_price", "move_1h", "move_24h",
                         "move_72h"])
        for e in events:
            writer.writerow([e["ts"].isoformat(timespec="seconds"), e["indicator"], e["count"],
                             e["btc_price"], *(e["moves"][h] for h in HORIZONS)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--since", default="2026-05-24")
    parser.add_argument("--until", default=None, help="この日まで（既定は今日）")
    parser.add_argument("--out", default=str(ROOT / "reports"))
    args = parser.parse_args(argv)
    until = args.until or datetime.now(timezone.utc).date().isoformat()

    commits = list_commits(args.since, f"{until}T23:59:59Z")
    events = reconstruct(load_states(commits))
    if not events:
        print("通知を復元できませんでした")
        return 1
    start = okx.floor_hour(events[0]["ts"])
    end = min(okx.floor_hour(events[-1]["ts"]) + timedelta(hours=max(HORIZONS)),
              datetime.now(timezone.utc))
    closes = okx.fetch_hourly_closes(start, end)
    enriched = attach_moves(events, closes)
    base = baseline(closes)
    rows = aggregate(enriched, base)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"history-{args.since}.md").write_text(
        render_markdown(args.since, until, enriched, rows, base), encoding="utf-8")
    write_csv(out / "history-events.csv", enriched)
    print(f"{len(commits)} commits / {sum(e['count'] for e in events)} notifications")
    return 0


if __name__ == "__main__":
    sys.exit(main())

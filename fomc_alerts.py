"""
FOMC：声明・議事録の通知と、利下げ確率（金利見通し）の確認。

- 声明は、決定後の誘導目標の中央と会合前の期待金利の差で判定する（±10bp）
- 金利見通しは、利下げ確率か利上げ確率が前回通知から15ポイント以上動いたら通知する
"""

import logging
from datetime import datetime, time, timedelta

import config
import notifier
import release_calendar
import rules
import summarizer
from fetchers import fomc, fomc_outlook, fred
from release_calendar import EASTERN, JST, at_eastern
from rules import Verdict
from runtime import Runtime

logger = logging.getLogger(__name__)

FOMC_ACTIONS = {"lower": "利下げ", "raise": "利上げ", "maintain": "据え置き"}


# ── 声明・議事録 ───────────────────────────────────────────────────────────


def is_new_document(rt: Runtime, section: str, document: dict | None) -> bool:
    """未通知の文書なら True。初めての場合は記録だけして False を返す。"""
    if document is None:
        return False
    stored_date = rt.store.get("fomc", section, "date")
    if stored_date is None:
        rt.store.set(("fomc", section), document)
        return False
    return document["date"] > stored_date


def _surprise_lines(decision: dict, pre: dict) -> tuple[Verdict, list[str]]:
    verdict, diff_bp = rules.judge_fomc_surprise(decision, pre["expected_rate"])
    if verdict == rules.BEARISH:
        reading = "市場の予想より引き締め的"
    elif verdict == rules.BULLISH:
        reading = "市場の予想より緩和的"
    else:
        reading = "市場の予想どおり"
    priced = f"利下げ確率 {pre['cut_prob']:.0f}%"
    if pre.get("hike_prob"):
        priced += f"、利上げ確率 {pre['hike_prob']:.0f}%"
    return verdict, [
        f"会合前の織り込み：{priced}、期待金利 {pre['expected_rate']:.2f}%",
        f"決定との差 {diff_bp:+.1f}bp → {reading}",
    ]


def publish_statement(rt: Runtime, document: dict) -> None:
    decision = rules.parse_fomc_decision(fomc.fetch_content(document["url"]))
    pre = rt.store.get("fomc_outlook", "pre_meeting", document["date"])
    if decision is None:
        lines, verdict = ["決定を本文から読み取れませんでした"], rules.UNKNOWN
    else:
        action = FOMC_ACTIONS[decision["action"]]
        lines = [f"決定：{action}（誘導目標 {decision['lower']:.2f}〜{decision['upper']:.2f}%）"]
        if pre:
            verdict, extra = _surprise_lines(decision, pre)
            lines += extra
        else:
            verdict = rules.judge_fomc(decision)
            lines.append("会合前の織り込みの控えなし（決定の内容で判定）")
    lines.append(f"🔗 {document['url']}")
    title = f"FOMC声明（{notifier.format_day(document['date'])}）"
    record = {"indicator": "fomc_statement", "kind": "immediate",
              "actual": decision, "compared": pre, "verdict": verdict.label}
    rt.send(notifier.format_alert(title, verdict, lines), record)
    rt.store.set(("fomc", "statement"), document)


def publish_minutes(rt: Runtime, document: dict) -> None:
    summary = summarizer.summarize_minutes(fomc.fetch_content(document["url"]), document["date"])
    verdict = Verdict(summary["judgment"], summary["emoji"])
    title = f"FOMC議事録（{notifier.format_day(document['date'])}会合）"
    record = {"indicator": "fomc_minutes", "kind": "immediate", "actual": None,
              "compared": None, "verdict": verdict.label}
    rt.send(notifier.format_alert(title, verdict, [summary["summary"], f"🔗 {document['url']}"]),
            record)
    rt.store.set(("fomc", "minutes"), document)


def check_documents(rt: Runtime) -> None:
    documents = fomc.fetch_fomc_documents()

    def statement() -> None:
        if is_new_document(rt, "statement", documents.get("statement")):
            publish_statement(rt, documents["statement"])

    def minutes() -> None:
        if is_new_document(rt, "minutes", documents.get("minutes")):
            publish_minutes(rt, documents["minutes"])

    rt.guard("fomc_statement", "FOMC声明", statement)
    rt.guard("fomc_minutes", "FOMC議事録", minutes)


# ── 金利見通し ─────────────────────────────────────────────────────────────


def upcoming_from(now: datetime) -> str:
    """
    「次回会合」に数える最初の日（米東部）。声明（14:00）が出たあとは、その日の会合を外す。
    決定後は確率が 0% か 100% に寄るので、外さないと声明の直後に金利見通しの通知が重なる。
    """
    local = now.astimezone(EASTERN)
    if local.time() >= time(*config.FOMC_TIME_ET):
        return (local.date() + timedelta(days=1)).isoformat()
    return local.date().isoformat()


def fetch_outlook(rt: Runtime, now: datetime) -> dict:
    """利下げ確率を取り、会合日を控え、次回会合の数字をまとめる。"""
    normalized = fomc_outlook.fetch()
    release_calendar.remember_meetings(rt, [m["date"] for m in normalized["meetings"]])
    target = fred.fetch_fed_target()
    rt.store.set(("fed_target",), target)
    return fomc_outlook.summarize(normalized, target, upcoming_from(now))


def remember_pre_meeting(rt: Runtime, outlook: dict, meeting_day: str, now: datetime) -> None:
    """
    会合前の期待金利を控える。声明（米東部 14:00）より前の値だけを使う。
    朝のまとめ（会合当日の日本時間の朝）と、当日の毎時の監視（より直前）が上書きする。
    """
    if outlook["meeting"] != meeting_day:
        return
    if now >= at_eastern(meeting_day, config.FOMC_TIME_ET):
        return
    snapshot = {key: outlook[key] for key in ("cut_prob", "hike_prob", "expected_rate", "as_of")}
    rt.store.set(("fomc_outlook", "pre_meeting", meeting_day), snapshot)


def _as_of_text(as_of: str) -> str:
    moment = datetime.fromisoformat(as_of).astimezone(JST)
    return f"{moment.month}/{moment.day} {moment:%H:%M}"


def _moved_pt(current: dict, base: dict) -> float:
    return max(abs(current["cut_prob"] - base["cut_prob"]),
               abs(current["hike_prob"] - base["hike_prob"]))


def _outlook_verdict(current: dict, base: dict | None) -> Verdict:
    if base is None:
        return rules.UNKNOWN
    return rules.judge_change("fomc_expected", current["expected_rate"], base["expected_rate"])


def check_outlook(rt: Runtime, now: datetime) -> None:
    """毎時：前回通知した値から15ポイント以上動いたら通知する。初回と会合の切り替わりは記録だけ。"""
    current = fetch_outlook(rt, now)
    remember_pre_meeting(rt, current, now.astimezone(EASTERN).date().isoformat(), now)
    rt.store.set(("fomc_outlook", "latest"), current)
    notified = rt.store.get("fomc_outlook", "notified")
    if not notified or notified.get("meeting") != current["meeting"]:
        rt.store.set(("fomc_outlook", "notified"), current)
        return
    if _moved_pt(current, notified) < config.FOMC_OUTLOOK_ALERT_PT:
        return
    verdict = _outlook_verdict(current, notified)
    meeting = notifier.format_day(current["meeting"])
    lines = [
        f"次回会合（{meeting}）の利下げ確率 {current['cut_prob']:.0f}%"
        f"（前回通知 {notified['cut_prob']:.0f}%）"
        f"{current['cut_prob'] - notified['cut_prob']:+.0f}pt",
        f"次回会合の利上げ確率 {current['hike_prob']:.0f}%"
        f"（前回通知 {notified['hike_prob']:.0f}%）"
        f"{current['hike_prob'] - notified['hike_prob']:+.0f}pt",
        f"{config.FOMC_OUTLOOK_HORIZON}会合先までの織り込み {current['priced_bp']:+.0f}bp"
        f"（前回通知 {notified['priced_bp']:+.0f}bp）",
        f"出所：SoSoValue（{_as_of_text(current['as_of'])} 時点）",
    ]
    record = {"indicator": "fomc_outlook", "kind": "immediate",
              "actual": current, "compared": notified, "verdict": verdict.label}
    rt.send(notifier.format_alert("FOMC金利見通し", verdict, lines), record)
    rt.store.set(("fomc_outlook", "notified"), current)


def morning_lines(rt: Runtime, now: datetime, today_jst: str) -> list[str]:
    """朝のまとめの「金利見通し」。前日比は前回の朝のまとめと比べる（会合が変わったら出さない）。"""
    current = fetch_outlook(rt, now)
    remember_pre_meeting(rt, current, today_jst, now)
    previous = rt.store.get("fomc_outlook", "morning")
    same = previous if previous and previous.get("meeting") == current["meeting"] else None

    def change(key: str, unit: str) -> str:
        return "" if same is None else f"（前日比 {current[key] - same[key]:+.0f}{unit}）"

    verdict = _outlook_verdict(current, same)
    emoji = f" {verdict.emoji}" if same else ""
    rates = f" 利下げ確率 {current['cut_prob']:.0f}%{change('cut_prob', 'pt')}"
    if current["hike_prob"] or (same and same["hike_prob"]):
        rates += f" ／ 利上げ確率 {current['hike_prob']:.0f}%{change('hike_prob', 'pt')}"
    rt.store.set(("fomc_outlook", "morning"), current)
    return [
        f"■ 金利見通し（次回FOMC {notifier.format_day(current['meeting'])}）",
        rates + emoji,
        f" {config.FOMC_OUTLOOK_HORIZON}会合先までの織り込み {current['priced_bp']:+.0f}bp"
        f"{change('priced_bp', 'bp')}",
    ]

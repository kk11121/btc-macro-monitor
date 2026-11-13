"""
発表待ち受け（毎時の監視の中で動く）。

毎時の監視が起動したとき、その後60分以内に発表があれば、発表時刻まで待ち、
発表時刻から30秒間隔で取りに行く（最長45分）。予備の定時実行が遅れて動いた回のために、
発表時刻から45分以内でまだ取れていない発表も対象にする。

取れなかった発表は、次の回の毎時の監視の予備確認（FRED）が拾う。
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from itertools import groupby

import config
import fomc_alerts
import forecasts
import macro
import release_calendar
from fetchers import fomc
from release_calendar import EASTERN
from runtime import Runtime

logger = logging.getLogger(__name__)

MACRO_RELEASES = tuple(config.RELEASES)


class Clock:
    """時刻と待機。テストでは差し替える。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


def window(now: datetime) -> tuple[datetime, datetime]:
    """待ち受けの対象にする発表時刻の範囲。"""
    return (now - timedelta(minutes=config.WATCH_MAX_MINUTES),
            now + timedelta(minutes=config.WATCH_LOOKAHEAD_MINUTES))


def is_done(state: dict, event: dict) -> bool:
    if event["key"] in MACRO_RELEASES:
        return macro.is_notified(state, event["key"], event["date_et"])
    section = "statement" if event["key"] == "fomc_statement" else "minutes"
    stored = ((state.get("fomc") or {}).get(section) or {}).get("date")
    return stored is not None and stored >= event["meeting"]


def due_events(state: dict, now: datetime) -> list[dict]:
    start, end = window(now)
    return [e for e in release_calendar.events_between(state, start, end)
            if not is_done(state, e)]


def longest_run_end(start: datetime, release_at: datetime) -> datetime:
    """
    待ち受けを含む毎時の回が動きうる最長の終わり。
    朝のまとめと重ならないことのテストに使う。
    """
    overhead = timedelta(minutes=config.HOURLY_OVERHEAD_MINUTES)
    return max(start, release_at) + timedelta(minutes=config.WATCH_MAX_MINUTES) + overhead


# ── 取得 ───────────────────────────────────────────────────────────────────


def _fetch(rt: Runtime, key: str, label: str, fetcher) -> object:
    box: list = []
    rt.guard(key, label, lambda: box.append(fetcher()))
    return box[0] if box else None


def _primary_snapshot(rt: Runtime, event: dict) -> dict | None:
    release = event["key"]
    if release in macro.BLS_RELEASES:
        if not macro.bls_available():
            return None
        return _fetch(rt, "bls", "BLS（CPI・雇用統計の実績）",
                      lambda: macro.bls_snapshot(release))
    return _fetch(rt, f"sosovalue_actual_{release}", f"SoSoValue（{event['label']}の実績）",
                  lambda: macro.sosovalue_snapshot(release, event["date_et"]))


def _capture_macro(rt: Runtime, event: dict, clock: Clock) -> bool:
    """新しい実績が取れたら通知して True。まだなら False。"""
    release, release_date = event["key"], event["date_et"]
    if macro.is_notified(rt.store.state, release, release_date):
        return True
    snapshot = _primary_snapshot(rt, event)
    if not macro.is_new(rt.store.state, snapshot):
        snapshot = _fetch(rt, f"fred_{release}", f"FRED（{event['label']}）",
                          lambda: macro.fred_snapshot(release))
        if not macro.is_new(rt.store.state, snapshot):
            return False
    captured_at = clock.now()
    values = forecasts.stored_for(rt.store.get("forecasts", release), release_date)
    report = macro.build_report(snapshot, values, release_date)
    try:
        macro.publish(rt, report)
    except Exception as exc:  # 送れなくても打ち切り、次の回の予備確認に任せる
        logger.error("[%s] 通知に失敗: %s", release, type(exc).__name__)
        rt.notification_failed = True
        return True
    rt.log_latency({
        "release": release, "release_date": release_date,
        "scheduled_at": event["at"].astimezone(timezone.utc).isoformat(timespec="seconds"),
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "seconds": round((captured_at - event["at"]).total_seconds()),
        "source": snapshot["source"],
    })
    return True


def _capture_fomc(rt: Runtime, event: dict) -> bool:
    if is_done(rt.store.state, event):
        return True
    documents = _fetch(rt, "fomc_watch", "FOMC（発表待ち受け）", fomc.fetch_fomc_documents)
    section = "statement" if event["key"] == "fomc_statement" else "minutes"
    document = (documents or {}).get(section)
    if not document or document["date"] < event["meeting"]:
        return False
    publisher = (fomc_alerts.publish_statement if section == "statement"
                 else fomc_alerts.publish_minutes)
    return rt.guard(f"fomc_{section}", event["label"], lambda: publisher(rt, document))


def _attempt(rt: Runtime, event: dict, clock: Clock) -> bool:
    if event["key"] in MACRO_RELEASES:
        return _capture_macro(rt, event, clock)
    return _capture_fomc(rt, event)


# ── 待ち受け ───────────────────────────────────────────────────────────────


def _sleep_until(clock: Clock, moment: datetime) -> None:
    clock.sleep((moment - clock.now()).total_seconds())


def _prepare(rt: Runtime, events: list[dict], clock: Clock) -> None:
    """発表の直前に予想値と、FOMC 声明なら会合前の期待金利を取り直す。"""
    collector = forecasts.Collector(rt.guard)
    for event in events:
        if event["key"] in MACRO_RELEASES:
            collected = collector.collect(event["key"], event["date_et"])
            previous = rt.store.get("forecasts", event["key"])
            rt.store.set(("forecasts", event["key"]), forecasts.merge(previous, collected))
        elif event["key"] == "fomc_statement":
            rt.guard("fomc_outlook", "FOMCの利下げ確率",
                     lambda meeting=event["meeting"], now=clock.now():
                     fomc_alerts.remember_pre_meeting(
                         rt, fomc_alerts.fetch_outlook(rt, now), meeting, now))


def _watch_slot(rt: Runtime, release_at: datetime, events: list[dict], clock: Clock) -> None:
    deadline = release_at + timedelta(minutes=config.WATCH_MAX_MINUTES)
    if clock.now() >= deadline:
        return
    _sleep_until(clock, release_at - timedelta(minutes=config.WATCH_PREFETCH_MINUTES))
    _prepare(rt, events, clock)
    _sleep_until(clock, release_at)
    logger.info("発表待ち受け：%s（米東部 %s）",
                "、".join(e["label"] for e in events), f"{release_at.astimezone(EASTERN):%H:%M}")
    pending = list(events)
    while pending and clock.now() < deadline:
        pending = [e for e in pending if not _attempt(rt, e, clock)]
        if pending:
            remaining = (deadline - clock.now()).total_seconds()
            clock.sleep(min(config.WATCH_POLL_SECONDS, max(remaining, 0)))
    for event in events:
        key, label = f"watch_{event['key']}", f"{event['label']}の発表待ち受け"
        if event in pending:
            logger.warning("[%s] %d分で実績を取れず。次の回の予備確認に任せる",
                           event["key"], config.WATCH_MAX_MINUTES)
            rt.guard(key, label, _raise_timeout)
        else:
            rt.guard(key, label, lambda: None)  # 前回失敗していたら復旧を知らせる


def _raise_timeout() -> None:
    raise TimeoutError("release data did not appear in time")


def run(rt: Runtime, events: list[dict], clock: Clock | None = None) -> None:
    clock = clock or Clock()
    ordered = sorted(events, key=lambda e: e["at"])
    for release_at, group in groupby(ordered, key=lambda e: e["at"]):
        _watch_slot(rt, release_at, list(group), clock)

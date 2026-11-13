"""
発表日程。経済指標は FRED の公式の予定日、FOMC は SoSoValue の会合日から作る。
時刻は config.py に固定で置いた米東部の時刻を使う（夏時間・冬時間は zoneinfo が吸収する）。
"""

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import config
from fetchers import fred
from runtime import Runtime

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
JST = ZoneInfo("Asia/Tokyo")
KEEP_MEETINGS = 16  # 議事録の日付を出すため、過去の会合日も少し残す
FOMC_LABELS = {"fomc_statement": "FOMC声明", "fomc_minutes": "FOMC議事録"}


def is_stale(calendar: dict | None, now: datetime) -> bool:
    fetched = (calendar or {}).get("fetched")
    if not fetched:
        return True
    try:
        age = now - datetime.fromisoformat(fetched)
    except (TypeError, ValueError):  # 読めない控えは古いものとして取り直す
        return True
    return age > timedelta(hours=config.CALENDAR_REFRESH_HOURS)


def refresh(rt: Runtime, now: datetime) -> None:
    """FRED から発表日程を取り直す。指標ごとに失敗を切り離し、取れた分だけ更新する。"""
    calendar = rt.store.get("calendar", default={}) or {}
    releases = dict(calendar.get("releases") or {})
    for key, spec in config.RELEASES.items():
        box: list[str] = []
        ok = rt.guard(
            f"calendar_{key}", f"発表日程（{spec['label']}）",
            lambda spec=spec, box=box: box.extend(
                fred.fetch_release_dates(spec["release_id"], config.CALENDAR_ROWS)),
        )
        if ok:
            releases[key] = box
    rt.store.set(
        ("calendar",),
        {**calendar, "releases": releases, "fetched": now.isoformat(timespec="seconds")},
    )


def remember_meetings(rt: Runtime, meeting_dates: list[str]) -> None:
    """SoSoValue の会合日を控える。終わった会合も議事録の日付のために残す。"""
    calendar = rt.store.get("calendar", default={}) or {}
    merged = sorted({*calendar.get("fomc_meetings", []), *meeting_dates})[-KEEP_MEETINGS:]
    rt.store.set(("calendar",), {**calendar, "fomc_meetings": merged})


def meeting_dates(state: dict) -> list[str]:
    calendar = state.get("calendar") or {}
    known = set(calendar.get("fomc_meetings") or [])
    last_statement = ((state.get("fomc") or {}).get("statement") or {}).get("date")
    if last_statement:
        known.add(last_statement)
    return sorted(known)


def at_eastern(day: str, hour_minute: tuple[int, int]) -> datetime:
    hour, minute = hour_minute
    return datetime.combine(date.fromisoformat(day), time(hour, minute), tzinfo=EASTERN)


def all_events(state: dict) -> list[dict]:
    """
    分かっている全ての発表を時刻順に返す。

    Returns:
        [{"key", "label", "date_et", "at"（米東部の datetime）}]
    """
    events = []
    releases = (state.get("calendar") or {}).get("releases") or {}
    for key, spec in config.RELEASES.items():
        for day in releases.get(key, []):
            events.append({"key": key, "label": spec["label"], "date_et": day,
                           "at": at_eastern(day, spec["time_et"])})
    for meeting in meeting_dates(state):
        minutes_day = (date.fromisoformat(meeting)
                       + timedelta(days=config.FOMC_MINUTES_DELAY_DAYS)).isoformat()
        events.append({"key": "fomc_statement", "label": FOMC_LABELS["fomc_statement"],
                       "date_et": meeting, "meeting": meeting,
                       "at": at_eastern(meeting, config.FOMC_TIME_ET)})
        events.append({"key": "fomc_minutes", "label": FOMC_LABELS["fomc_minutes"],
                       "date_et": minutes_day, "meeting": meeting,
                       "at": at_eastern(minutes_day, config.FOMC_TIME_ET)})
    return sorted(events, key=lambda e: e["at"])


def events_between(state: dict, start: datetime, end: datetime) -> list[dict]:
    return [e for e in all_events(state) if start <= e["at"] <= end]


def latest_release_on_or_before(state: dict, key: str, day_et: str) -> str | None:
    days = ((state.get("calendar") or {}).get("releases") or {}).get(key, [])
    past = [d for d in days if d <= day_et]
    return past[-1] if past else None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)

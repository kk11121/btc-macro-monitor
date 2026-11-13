"""
予想値の取得と、単位をそろえる処理。

SoSoValue と ForexFactory の両方から取り、2つの予想を範囲として扱う。
- SoSoValue：CPI 系・コアPCE は "0.3%"（"0%" や "-0.4%" もある）、NFP と申請件数は千単位の数字だけ
- ForexFactory："65K"、"194K"、"0.2%"、"4.2%" の形
内部の単位：percent は %、thousands（NFP）は千人、count（申請件数）は件。
"""

import logging
from datetime import date, datetime, timezone

import config
from fetchers import forexfactory, sosovalue

logger = logging.getLogger(__name__)

SOURCE_LABELS = {"sosovalue": "SoSoValue", "forexfactory": "ForexFactory"}
_SUFFIX_SCALE = {"K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0}


def _number(text: str) -> float | None:
    """数字にならなければ None（"-" や空欄、"N/A" など）。"""
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def parse_sosovalue(raw: str | None, unit: str) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    if unit == "percent":
        return _number(text.rstrip("%"))
    value = _number(text)
    if value is None:
        return None
    return value * 1_000.0 if unit == "count" else value  # count は千件で来る


def parse_forexfactory(raw: str | None, unit: str) -> float | None:
    text = (raw or "").strip().upper()
    if not text:
        return None
    if unit == "percent":
        return _number(text.rstrip("%"))
    scale = _SUFFIX_SCALE.get(text[-1], 1.0)
    value = _number(text[:-1] if text[-1] in _SUFFIX_SCALE else text)
    if value is None:
        return None
    absolute = value * scale
    return absolute / 1_000.0 if unit == "thousands" else absolute


def metrics_for(release: str) -> list[str]:
    return [key for key, spec in config.FORECAST_METRICS.items() if spec["release"] == release]


def _near(row_date: str, release_date: str) -> bool:
    try:
        gap = abs((date.fromisoformat(row_date[:10]) - date.fromisoformat(release_date)).days)
    except ValueError:
        return False
    return gap <= config.SOSOVALUE_DATE_TOLERANCE_DAYS


def sosovalue_next_row(rows: list[dict], release_date: str) -> dict | None:
    """次回分＝実績が空の行。発表日と1日までのずれは同じ回とみなす。"""
    return next((r for r in rows if not r["actual"] and _near(r["date"], release_date)), None)


def sosovalue_row_on(rows: list[dict], release_date: str) -> dict | None:
    """発表日の行（実績の有無を問わない）。"""
    return next((r for r in rows if _near(r["date"], release_date)), None)


class Collector:
    """
    1回の実行の中で予想値を集める。ForexFactory は1回の実行で1回だけ取る。
    取得の失敗は Runtime.guard に任せ、取れた分だけで続ける。
    """

    def __init__(self, guard) -> None:
        self._guard = guard
        self._ff_events: list[dict] | None = None

    def _forexfactory_events(self) -> list[dict]:
        if self._ff_events is None:
            events: list[dict] = []
            self._guard(
                "forexfactory", "ForexFactory（予想値）",
                lambda: events.extend(forexfactory.fetch_week()),
            )
            self._ff_events = events
        return self._ff_events

    def _sosovalue_forecast(self, metric: str, release_date: str) -> float | None:
        spec = config.FORECAST_METRICS[metric]
        if not spec["sosovalue"]:
            return None
        rows: list[dict] = []
        self._guard(
            f"sosovalue_{metric}", f"SoSoValue（{spec['sosovalue']} の予想）",
            lambda: rows.extend(sosovalue.fetch_event_history(spec["sosovalue"])),
        )
        row = sosovalue_next_row(rows, release_date)
        return parse_sosovalue(row["forecast"], spec["unit"]) if row else None

    def collect(self, release: str, release_date: str) -> dict:
        """
        Returns:
            {"release_date", "fetched_at", "values": {metric: {"sosovalue": x, "forexfactory": y}}}
        """
        events = self._forexfactory_events()
        values = {}
        for metric in metrics_for(release):
            spec = config.FORECAST_METRICS[metric]
            ff_row = forexfactory.find(events, spec["forexfactory"], release_date)
            values[metric] = {
                "sosovalue": self._sosovalue_forecast(metric, release_date),
                "forexfactory": parse_forexfactory(ff_row["forecast"], spec["unit"])
                if ff_row else None,
            }
        return {
            "release_date": release_date,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "values": values,
        }


def merge(previous: dict | None, collected: dict) -> dict:
    """
    同じ回の控えがあれば、今回取れなかった出所だけ前の値で補う。
    発表直前の取り直しで SoSoValue が失敗しても、朝のまとめで取れた予想を失わないため。
    """
    if not previous or previous.get("release_date") != collected["release_date"]:
        return collected
    values = {}
    for metric, sources in collected["values"].items():
        old = previous.get("values", {}).get(metric, {})
        values[metric] = {src: value if value is not None else old.get(src)
                          for src, value in sources.items()}
    return {**collected, "values": values}


def stored_for(forecasts: dict | None, release_date: str) -> dict:
    """控えの予想値のうち、その発表日の分。違う回の控えなら空。"""
    if not forecasts or forecasts.get("release_date") != release_date:
        return {}
    return forecasts.get("values", {})


def available(metric_values: dict | None) -> dict[str, float]:
    """取れた予想だけを {出所: 値} で返す。"""
    return {src: v for src, v in (metric_values or {}).items() if v is not None}


def sources_text(values_by_metric: dict) -> str:
    used = {src for v in values_by_metric.values() for src in available(v)}
    names = [SOURCE_LABELS[src] for src in ("sosovalue", "forexfactory") if src in used]
    return "・".join(names)

"""予想値の単位をそろえる処理と、2つの取得先からの収集。"""

import pytest

import forecasts


@pytest.mark.parametrize(
    ("raw", "unit", "expected"),
    [
        ("0.3%", "percent", 0.3),
        ("0%", "percent", 0.0),       # 整数のときは小数点なし
        ("-0.4%", "percent", -0.4),
        ("63", "thousands", 63.0),    # NFP は千人
        ("-23", "thousands", -23.0),
        ("197", "count", 197000.0),   # 申請件数は千件で来る
        ("", "percent", None),        # 次回分の実績は空
        (None, "count", None),
        ("N/A", "thousands", None),
    ],
)
def test_parse_sosovalue(raw, unit, expected):
    assert forecasts.parse_sosovalue(raw, unit) == expected


@pytest.mark.parametrize(
    ("raw", "unit", "expected"),
    [
        ("65K", "thousands", 65.0),
        ("-23K", "thousands", -23.0),
        ("1.2M", "thousands", 1200.0),
        ("194K", "count", 194000.0),
        ("0.2%", "percent", 0.2),
        ("4.2%", "percent", 4.2),
        ("", "count", None),
        ("-", "percent", None),
    ],
)
def test_parse_forexfactory(raw, unit, expected):
    parsed = forecasts.parse_forexfactory(raw, unit)
    if expected is None:
        assert parsed is None
    else:
        assert parsed == pytest.approx(expected)


def test_same_figure_from_both_sources_has_same_unit():
    """SoSoValue の "63"・"197" と ForexFactory の "63K"・"197K" は同じ値になる。"""
    assert forecasts.parse_sosovalue("63", "thousands") == forecasts.parse_forexfactory(
        "63K", "thousands")
    assert forecasts.parse_sosovalue("197", "count") == forecasts.parse_forexfactory(
        "197K", "count")


def test_sosovalue_next_row_is_the_empty_actual_near_release_date():
    rows = [
        {"date": "2026-11-05", "actual": "", "forecast": "197", "previous": "194"},
        {"date": "2026-10-29", "actual": "194", "forecast": "198", "previous": "194"},
    ]
    assert forecasts.sosovalue_next_row(rows, "2026-11-05")["forecast"] == "197"
    assert forecasts.sosovalue_next_row(rows, "2026-11-06")["forecast"] == "197"  # 1日ずれ
    assert forecasts.sosovalue_next_row(rows, "2026-11-12") is None


class _Guard:
    """Runtime.guard の代わり。失敗を記録して握りつぶす。"""

    def __init__(self):
        self.failed: list[str] = []

    def __call__(self, key, label, action):
        try:
            action()
        except Exception:
            self.failed.append(key)
            return False
        return True


def _ff_events():
    return [
        {"title": "Non-Farm Employment Change", "date_et": "2026-11-06", "forecast": "65K",
         "previous": "29K"},
        {"title": "Unemployment Rate", "date_et": "2026-11-06", "forecast": "4.2%",
         "previous": "4.2%"},
    ]


def test_collector_combines_both_sources(monkeypatch):
    calls = []

    def history(name):
        calls.append(name)
        return [{"date": "2026-11-06", "actual": "", "forecast": "63", "previous": "29"}]

    monkeypatch.setattr(forecasts.sosovalue, "fetch_event_history", history)
    monkeypatch.setattr(forecasts.forexfactory, "fetch_week", _ff_events)
    guard = _Guard()
    collected = forecasts.Collector(guard).collect("employment", "2026-11-06")
    assert collected["release_date"] == "2026-11-06"
    assert collected["values"]["nfp"] == {"sosovalue": 63.0, "forexfactory": 65.0}
    assert collected["values"]["unrate"] == {"sosovalue": None, "forexfactory": 4.2}
    assert calls == ["Nonfarm Payrolls"]  # 失業率は SoSoValue にないので呼ばない
    assert guard.failed == []


def test_collector_empty_history_is_an_error_but_forexfactory_still_used(monkeypatch):
    def empty(name):
        raise ValueError("no history")

    monkeypatch.setattr(forecasts.sosovalue, "fetch_event_history", empty)
    monkeypatch.setattr(forecasts.forexfactory, "fetch_week", _ff_events)
    guard = _Guard()
    collected = forecasts.Collector(guard).collect("employment", "2026-11-06")
    assert guard.failed == ["sosovalue_nfp"]
    assert collected["values"]["nfp"] == {"sosovalue": None, "forexfactory": 65.0}


def test_collector_missing_next_row_is_not_an_error(monkeypatch):
    rows = [{"date": "2026-10-02", "actual": "29", "forecast": "89", "previous": "162"}]
    monkeypatch.setattr(forecasts.sosovalue, "fetch_event_history", lambda name: rows)
    monkeypatch.setattr(forecasts.forexfactory, "fetch_week", _ff_events)
    guard = _Guard()
    collected = forecasts.Collector(guard).collect("employment", "2026-11-06")
    assert guard.failed == []
    assert collected["values"]["nfp"]["sosovalue"] is None


def test_collector_fetches_forexfactory_once_per_run(monkeypatch):
    fetched = []
    monkeypatch.setattr(forecasts.sosovalue, "fetch_event_history", lambda name: [
        {"date": "2026-11-06", "actual": "", "forecast": "1", "previous": "1"}])
    monkeypatch.setattr(forecasts.forexfactory, "fetch_week",
                        lambda: fetched.append(1) or _ff_events())
    collector = forecasts.Collector(_Guard())
    collector.collect("employment", "2026-11-06")
    collector.collect("icsa", "2026-11-05")
    assert fetched == [1]


def test_stored_for_and_sources_text():
    stored = {"release_date": "2026-11-06", "values": {"nfp": {"sosovalue": 63.0,
                                                               "forexfactory": None}}}
    assert forecasts.stored_for(stored, "2026-11-06") == stored["values"]
    assert forecasts.stored_for(stored, "2026-12-04") == {}
    assert forecasts.stored_for(None, "2026-12-04") == {}
    assert forecasts.sources_text(stored["values"]) == "SoSoValue"
    assert forecasts.sources_text({"a": {"sosovalue": 1, "forexfactory": 2}}) == \
        "SoSoValue・ForexFactory"
    assert forecasts.sources_text({}) == ""


def test_merge_keeps_previous_value_only_for_failed_source():
    previous = {"release_date": "2026-11-19", "values": {
        "icsa": {"sosovalue": 199000.0, "forexfactory": 198000.0}}}
    collected = {"release_date": "2026-11-19", "fetched_at": "x", "values": {
        "icsa": {"sosovalue": None, "forexfactory": 200000.0}}}
    merged = forecasts.merge(previous, collected)
    assert merged["values"]["icsa"] == {"sosovalue": 199000.0, "forexfactory": 200000.0}


def test_merge_ignores_previous_release():
    previous = {"release_date": "2026-11-12", "values": {"icsa": {"sosovalue": 1.0}}}
    collected = {"release_date": "2026-11-19", "values": {"icsa": {"sosovalue": None}}}
    assert forecasts.merge(previous, collected) is collected
    assert forecasts.merge(None, collected) is collected

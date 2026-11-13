"""Phase B で足した取得先（SoSoValue の共通処理、ForexFactory、BLS、OKX、FRED の日程）。"""

from datetime import datetime, timezone

import pytest
import requests

from fetchers import bls, forexfactory, fred, okx, sosovalue


class _Resp:
    def __init__(self, payload=None, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.ok = status < 400
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} for url https://x?api_key=SECRET")


def _stub(monkeypatch, method, responses):
    calls = []
    queue = list(responses)

    def fake(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(requests, method, fake)
    return calls


USAGE = {"x-ratelimit-remaining-month": "9876", "x-ratelimit-limit-month": "10000"}


# ── SoSoValue ─────────────────────────────────────────────────────────────────


def test_sosovalue_records_remaining_calls(monkeypatch):
    monkeypatch.setenv("SOSOVALUE_API_KEY", "SOSO")
    calls = _stub(monkeypatch, "get", [_Resp({"code": 0, "data": [1]}, headers=USAGE)])
    assert sosovalue.get("/x") == [1]
    assert calls[0]["headers"]["x-soso-api-key"] == "SOSO"
    usage = sosovalue.last_usage()
    assert usage["remaining_month"] == 9876 and usage["limit_month"] == 10000


def test_sosovalue_records_usage_even_on_http_error(monkeypatch):
    monkeypatch.setenv("SOSOVALUE_API_KEY", "SOSO")
    _stub(monkeypatch, "get", [_Resp(None, status=429, headers=USAGE)])
    with pytest.raises(RuntimeError, match="HTTP 429"):
        sosovalue.get("/x")
    assert sosovalue.last_usage()["remaining_month"] == 9876


def test_sosovalue_error_code_raises(monkeypatch):
    monkeypatch.setenv("SOSOVALUE_API_KEY", "SOSO")
    _stub(monkeypatch, "get", [_Resp({"code": 400101, "data": None})])
    with pytest.raises(ValueError):
        sosovalue.get("/x")


def test_sosovalue_network_error_hides_key(monkeypatch):
    monkeypatch.setenv("SOSOVALUE_API_KEY", "SOSO-SECRET")

    def boom(url, **kwargs):
        raise requests.ConnectionError("failed with header SOSO-SECRET")

    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(RuntimeError) as info:
        sosovalue.get("/x")
    assert "SOSO-SECRET" not in str(info.value)


def test_event_history_empty_is_wrong_name(monkeypatch):
    """名前を間違えると HTTP 200・data: [] が返る。0件は名前の誤りとして例外。"""
    monkeypatch.setenv("SOSOVALUE_API_KEY", "SOSO")
    _stub(monkeypatch, "get", [_Resp({"code": 0, "data": []})])
    with pytest.raises(ValueError, match="wrong event name"):
        sosovalue.fetch_event_history("Unemployment Rate")


def test_event_history_sorted_newest_first_with_strings(monkeypatch):
    monkeypatch.setenv("SOSOVALUE_API_KEY", "SOSO")
    data = [{"date": "2026-10-29", "actual": "194", "forecast": "198", "previous": "194"},
            {"date": "2026-11-05", "actual": "", "forecast": "197", "previous": "194"}]
    calls = _stub(monkeypatch, "get", [_Resp({"code": 0, "data": data})])
    rows = sosovalue.fetch_event_history("Initial Jobless Claims")
    assert rows[0] == {"date": "2026-11-05", "actual": "", "forecast": "197", "previous": "194"}
    assert calls[0]["url"].endswith("/macro/events/Initial Jobless Claims/history")


def test_fomc_probabilities_empty_raises(monkeypatch):
    monkeypatch.setenv("SOSOVALUE_API_KEY", "SOSO")
    _stub(monkeypatch, "get", [_Resp({"code": 0, "data": {"meetings": []}})])
    with pytest.raises(ValueError):
        sosovalue.fetch_fomc_probabilities()


def test_sosovalue_missing_key(monkeypatch):
    monkeypatch.delenv("SOSOVALUE_API_KEY", raising=False)
    with pytest.raises(sosovalue.MissingApiKey):
        sosovalue.get("/x")


# ── ForexFactory ──────────────────────────────────────────────────────────────

FF = [
    {"title": "Core CPI m/m", "country": "USD", "date": "2026-11-10T08:30:00-05:00",
     "impact": "High", "forecast": "0.2%", "previous": "0.3%"},
    {"title": "CPI m/m", "country": "EUR", "date": "2026-11-10T05:00:00-05:00",
     "impact": "High", "forecast": "0.1%", "previous": "0.1%"},
    {"title": "Broken", "country": "USD", "date": "not a date"},
]


def test_forexfactory_keeps_usd_rows_with_eastern_date(monkeypatch):
    calls = _stub(monkeypatch, "get", [_Resp(FF)])
    rows = forexfactory.fetch_week()
    assert rows == [{"title": "Core CPI m/m", "date_et": "2026-11-10", "forecast": "0.2%",
                     "previous": "0.3%"}]
    assert calls[0]["url"] == "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    assert forexfactory.find(rows, "core cpi m/m", "2026-11-10")["forecast"] == "0.2%"
    assert forexfactory.find(rows, "Core CPI m/m", "2026-11-11") is None


def test_forexfactory_http_error(monkeypatch):
    _stub(monkeypatch, "get", [_Resp(None, status=429)])
    with pytest.raises(RuntimeError, match="429"):
        forexfactory.fetch_week()


# ── BLS ───────────────────────────────────────────────────────────────────────

BLS_OK = {
    "status": "REQUEST_SUCCEEDED",
    "Results": {"series": [{
        "seriesID": "LNS14000000",
        "data": [
            {"year": "2026", "period": "M10", "value": "4.1", "latest": "true"},
            {"year": "2026", "period": "M13", "value": "4.2"},
            {"year": "2025", "period": "M10", "value": "-"},
            {"year": "2025", "period": "M09", "value": "4.4"},
        ],
    }]},
}


def test_bls_parses_months_and_skips_missing(monkeypatch):
    monkeypatch.setenv("BLS_API_KEY", "BLSKEY")
    calls = _stub(monkeypatch, "post", [_Resp(BLS_OK)])
    rows = bls.fetch_series(["LNS14000000"], today=datetime(2026, 11, 13).date())
    assert rows["LNS14000000"] == [{"date": "2026-10-01", "value": 4.1},
                                   {"date": "2025-09-01", "value": 4.4}]
    assert calls[0]["json"]["startyear"] == "2025"
    assert calls[0]["json"]["registrationkey"] == "BLSKEY"


def test_bls_invalid_key_message_never_leaks_key(monkeypatch):
    """BLS はエラー本文にキーを入れて返す。例外の文面には状態の名前だけを出す。"""
    monkeypatch.setenv("BLS_API_KEY", "BLSKEY-SECRET")
    payload = {"status": "REQUEST_NOT_PROCESSED",
               "message": ["The key:BLSKEY-SECRET provided by the User is invalid."]}
    _stub(monkeypatch, "post", [_Resp(payload)])
    with pytest.raises(RuntimeError) as info:
        bls.fetch_series(["LNS14000000"])
    assert "BLSKEY-SECRET" not in str(info.value)
    assert "REQUEST_NOT_PROCESSED" in str(info.value)


def test_bls_without_key_does_not_call(monkeypatch):
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    with pytest.raises(bls.MissingApiKey):
        bls.fetch_series(["LNS14000000"])


def test_bls_missing_series_raises(monkeypatch):
    monkeypatch.setenv("BLS_API_KEY", "k")
    _stub(monkeypatch, "post", [_Resp({"status": "REQUEST_SUCCEEDED", "Results": {"series": []}})])
    with pytest.raises(ValueError):
        bls.fetch_series(["CES0000000001"])


# ── OKX ───────────────────────────────────────────────────────────────────────


def _candle(hour, close, confirm="1"):
    ts = int(datetime(2026, 11, 13, hour, tzinfo=timezone.utc).timestamp() * 1000)
    return [str(ts), "0", "0", "0", str(close), "0", "0", "0", confirm]


def test_okx_closes_keep_only_confirmed_candles_in_range(monkeypatch):
    page = {"code": "0", "data": [_candle(5, 105, "0"), _candle(4, 104), _candle(3, 103),
                                  _candle(2, 102)]}
    _stub(monkeypatch, "get", [_Resp(page)])
    start = datetime(2026, 11, 13, 3, 10, tzinfo=timezone.utc)
    end = datetime(2026, 11, 13, 5, 0, tzinfo=timezone.utc)
    closes = okx.fetch_hourly_closes(start, end)
    assert closes == {datetime(2026, 11, 13, 3, tzinfo=timezone.utc): 103.0,
                      datetime(2026, 11, 13, 4, tzinfo=timezone.utc): 104.0}


def test_okx_pages_backwards_until_start(monkeypatch):
    pages = [_Resp({"code": "0", "data": [_candle(9, 9), _candle(8, 8)]}),
             _Resp({"code": "0", "data": [_candle(7, 7), _candle(6, 6)]})]
    calls = _stub(monkeypatch, "get", pages)
    closes = okx.fetch_hourly_closes(datetime(2026, 11, 13, 6, tzinfo=timezone.utc),
                                     datetime(2026, 11, 13, 9, tzinfo=timezone.utc))
    assert len(closes) == 4
    assert len(calls) == 2


# ── FRED の発表日程と誘導目標 ───────────────────────────────────────────────────


def test_fred_release_dates_allow_future_dates(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "FRED")
    payload = {"release_dates": [{"release_id": 10, "date": "2026-12-10"},
                                 {"release_id": 10, "date": "2026-11-10"}]}
    calls = _stub(monkeypatch, "get", [_Resp(payload)])
    assert fred.fetch_release_dates(10) == ["2026-11-10", "2026-12-10"]
    assert calls[0]["params"]["include_release_dates_with_no_data"] == "true"
    assert calls[0]["params"]["release_id"] == 10


def test_fred_release_dates_error_hides_key(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "FRED-SECRET")
    _stub(monkeypatch, "get", [_Resp(None, status=500)])
    with pytest.raises(RuntimeError) as info:
        fred.fetch_release_dates(10)
    assert "SECRET" not in str(info.value)


def test_fred_fed_target(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "FRED")

    def fake(url, params=None, **kwargs):
        value = "4" if params["series_id"] == "DFEDTARU" else "3.75"
        return _Resp({"observations": [{"date": "2026-11-12", "value": value}]})

    monkeypatch.setattr(requests, "get", fake)
    assert fred.fetch_fed_target() == {"lower": 3.75, "upper": 4.0, "date": "2026-11-12"}

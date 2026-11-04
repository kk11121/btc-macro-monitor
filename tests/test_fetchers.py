from datetime import date, timedelta

import pytest
import requests

from fetchers import coinmetrics, etf, fomc, fred


class _Resp:
    def __init__(self, payload=None, status=200, text=""):
        self._payload = payload
        self.status_code = status
        self.ok = status < 400
        self.text = text
        self.content = text.encode("utf-8")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} for url https://x?api_key=SECRET")


def _stub_get(monkeypatch, module, payload=None, status=200, text=""):
    calls = []

    def fake(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Resp(payload, status, text)

    monkeypatch.setattr(module.requests, "get", fake)
    return calls


# ── FRED ──────────────────────────────────────────────────────────────────────


def test_fetch_observations_skips_missing_values(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "SECRET")
    payload = {
        "observations": [
            {"date": "2025-11-01", "value": "331.043"},
            {"date": "2025-10-01", "value": "."},
            {"date": "2025-09-01", "value": "330.418"},
        ]
    }
    calls = _stub_get(monkeypatch, fred, payload)
    rows = fred.fetch_observations("core_cpi", limit=3)
    assert rows == [
        {"date": "2025-11-01", "value": 331.043},
        {"date": "2025-09-01", "value": 330.418},
    ]
    assert calls[0]["params"]["series_id"] == "CPILFESL"
    assert calls[0]["params"]["limit"] > 3  # 欠測の行も limit に数えられるので余分に取る


def test_fetch_observations_empty_is_an_error(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "SECRET")
    _stub_get(monkeypatch, fred, {"observations": []})
    with pytest.raises(ValueError):
        fred.fetch_observations("cpi")


def test_fetch_observations_http_error_hides_api_key(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "SECRET")
    _stub_get(monkeypatch, fred, status=500)
    with pytest.raises(RuntimeError) as exc:
        fred.fetch_observations("cpi")
    assert "SECRET" not in str(exc.value)


def test_fetch_observations_connection_error_hides_api_key(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "SECRET")

    def fail(*args, **kwargs):
        raise requests.ConnectionError("https://x?api_key=SECRET")

    monkeypatch.setattr(fred.requests, "get", fail)
    with pytest.raises(RuntimeError) as exc:
        fred.fetch_observations("cpi")
    assert "SECRET" not in str(exc.value)


def test_fetch_release_date_converts_to_eastern(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "SECRET")
    _stub_get(monkeypatch, fred, {"seriess": [{"last_updated": "2026-10-14 08:08:29-05"}]})
    assert fred.fetch_release_date("core_cpi") == "2026-10-14"


def test_fetch_release_date_late_evening_rolls_to_next_eastern_day(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "SECRET")
    _stub_get(monkeypatch, fred, {"seriess": [{"last_updated": "2026-10-14 23:30:00-05"}]})
    assert fred.fetch_release_date("core_cpi") == "2026-10-15"


# ── ETF ───────────────────────────────────────────────────────────────────────

SOSO_OK = {
    "code": 0,
    "message": "success",
    "data": [
        {"date": "2026-11-03", "total_net_inflow": -115563333.62},
        {"date": "2026-11-02", "total_net_inflow": -398617353.4},
    ],
}


def test_sosovalue_rows_parse_and_send_header(monkeypatch):
    monkeypatch.setenv("SOSOVALUE_API_KEY", "SOSO")
    calls = _stub_get(monkeypatch, etf, SOSO_OK)
    rows = etf.fetch_sosovalue_rows()
    assert rows[0] == {"date": "2026-11-03", "value": -115563333.62, "source": "sosovalue"}
    assert calls[0]["headers"]["x-soso-api-key"] == "SOSO"
    assert calls[0]["params"]["symbol"] == "BTC"
    assert calls[0]["params"]["country_code"] == "US"


def test_sosovalue_rows_sorted_newest_first(monkeypatch):
    monkeypatch.setenv("SOSOVALUE_API_KEY", "SOSO")
    reversed_payload = {**SOSO_OK, "data": list(reversed(SOSO_OK["data"]))}
    _stub_get(monkeypatch, etf, reversed_payload)
    assert etf.fetch_sosovalue_rows()[0]["date"] == "2026-11-03"


def test_sosovalue_missing_key_raises(monkeypatch):
    monkeypatch.delenv("SOSOVALUE_API_KEY", raising=False)
    with pytest.raises(etf.MissingApiKey):
        etf.fetch_sosovalue_rows()


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 400101, "message": "invalid", "data": None},
        {"code": 0, "message": "success", "data": []},
        {"code": 0, "data": [{"date": "2026-11-03", "total_net_inflow": None}]},
        {"code": 0, "data": [{"total_net_inflow": 1.0}]},
    ],
)
def test_sosovalue_bad_payload_raises(monkeypatch, payload):
    monkeypatch.setenv("SOSOVALUE_API_KEY", "SOSO")
    _stub_get(monkeypatch, etf, payload)
    with pytest.raises(ValueError):
        etf.fetch_sosovalue_rows()


def test_sosovalue_http_error_raises(monkeypatch):
    monkeypatch.setenv("SOSOVALUE_API_KEY", "SOSO")
    _stub_get(monkeypatch, etf, status=401)
    with pytest.raises(RuntimeError):
        etf.fetch_sosovalue_rows()


def test_tftc_latest_rows(monkeypatch):
    payload = {
        "recentSessions": [
            {"date": "2026-11-02", "netFlowUsd": -398617353.4},
            {"date": "2026-10-30", "netFlowUsd": -643330345.885},
        ]
    }
    _stub_get(monkeypatch, etf, payload)
    rows = etf.fetch_tftc_latest_rows()
    assert rows[0] == {"date": "2026-11-02", "value": -398617353.4, "source": "tftc"}


def test_tftc_history_skips_null_days_and_sorts_newest_first(monkeypatch):
    payload = {
        "days": [
            {"date": "2026-10-30", "netFlowUsd": 1.0},
            {"date": "2026-10-31", "netFlowUsd": None},
            {"date": "2026-11-02", "netFlowUsd": 2.0},
        ]
    }
    _stub_get(monkeypatch, etf, payload)
    rows = etf.fetch_tftc_history()
    assert [r["date"] for r in rows] == ["2026-11-02", "2026-10-30"]


def test_tftc_empty_raises(monkeypatch):
    _stub_get(monkeypatch, etf, {"days": []})
    with pytest.raises(ValueError):
        etf.fetch_tftc_history()


# ── CoinMetrics ───────────────────────────────────────────────────────────────


def test_exchange_netflow_history_newest_first_and_skips_partial(monkeypatch):
    payload = {
        "data": [
            {"time": "2026-11-01T00:00:00.000Z", "FlowInExNtv": "10", "FlowOutExNtv": "4"},
            {"time": "2026-11-02T00:00:00.000Z", "FlowInExNtv": "3", "FlowOutExNtv": "5"},
            {"time": "2026-11-03T00:00:00.000Z", "FlowInExNtv": "7"},
        ]
    }
    calls = _stub_get(monkeypatch, coinmetrics, payload)
    rows = coinmetrics.fetch_exchange_netflow_history()
    assert rows == [
        {"date": "2026-11-02", "value": -2.0},
        {"date": "2026-11-01", "value": 6.0},
    ]
    assert calls[0]["params"]["page_size"] >= 100


def test_exchange_netflow_empty_raises(monkeypatch):
    _stub_get(monkeypatch, coinmetrics, {"data": []})
    with pytest.raises(ValueError):
        coinmetrics.fetch_exchange_netflow_history()


def test_exchange_supply(monkeypatch):
    payload = {"data": [{"time": "2026-11-03T00:00:00.000Z", "SplyExNtv": "2660347.5"}]}
    _stub_get(monkeypatch, coinmetrics, payload)
    assert coinmetrics.fetch_exchange_supply()["value"] == 2660347.5


# ── FOMC ──────────────────────────────────────────────────────────────────────

CALENDAR = """
<a href="/monetarypolicy/files/fomcminutes20260916.pdf">PDF</a>
<a href="/monetarypolicy/fomcminutes20260916.htm">HTML</a>
<a href="/monetarypolicy/fomcminutes20260729.htm">HTML</a>
<a href="/newsevents/pressreleases/monetary20261028a.htm">Statement</a>
<a href="/newsevents/pressreleases/monetary20261028a1.htm">Implementation Note</a>
<a href="/newsevents/pressreleases/monetary20260916a.htm">Statement</a>
"""


def test_fetch_documents_returns_statement_and_minutes_separately(monkeypatch):
    _stub_get(monkeypatch, fomc, text=CALENDAR)
    docs = fomc.fetch_fomc_documents()
    assert docs["statement"]["date"] == "2026-10-28"
    assert docs["statement"]["url"].endswith("monetary20261028a.htm")
    assert docs["minutes"]["date"] == "2026-09-16"
    assert docs["minutes"]["url"].endswith("fomcminutes20260916.htm")


def test_minutes_with_same_date_as_statement_are_not_hidden(monkeypatch):
    """不具合の再現：議事録の URL は会合日付なので、同じ日付の声明に隠れていた。"""
    html = (
        '<a href="/monetarypolicy/fomcminutes20261028.htm">m</a>'
        '<a href="/newsevents/pressreleases/monetary20261028a.htm">s</a>'
    )
    _stub_get(monkeypatch, fomc, text=html)
    docs = fomc.fetch_fomc_documents()
    assert docs["minutes"]["date"] == docs["statement"]["date"] == "2026-10-28"


def test_fetch_documents_raises_when_page_has_no_links(monkeypatch):
    _stub_get(monkeypatch, fomc, text="<html></html>")
    with pytest.raises(ValueError):
        fomc.fetch_fomc_documents()


def test_fetch_documents_http_error_raises(monkeypatch):
    _stub_get(monkeypatch, fomc, status=503)
    with pytest.raises(requests.HTTPError):
        fomc.fetch_fomc_documents()


def test_fetch_content_is_not_truncated(monkeypatch):
    long_text = "word " * 20000
    _stub_get(monkeypatch, fomc, text=f"<html><div id='article'>{long_text}</div></html>")
    assert len(fomc.fetch_content("https://x")) > 90000


def test_fetch_content_decodes_from_bytes_not_latin1(monkeypatch):
    """Fed のページは文字コードをヘッダーで示さず、resp.text だと文字化けする。"""
    html = (
        '<html><head><meta charset="utf-8"></head>'
        "<body><div id='article'>September 15–16, 2026</div></body></html>"
    )

    class Latin1Resp(_Resp):
        def __init__(self):
            super().__init__(text=html)
            self.text = self.content.decode("latin-1")

    monkeypatch.setattr(fomc.requests, "get", lambda url, **kwargs: Latin1Resp())
    assert fomc.fetch_content("https://x") == "September 15–16, 2026"


def test_fetch_content_failure_raises_so_that_the_run_is_retried(monkeypatch):
    _stub_get(monkeypatch, fomc, status=500)
    with pytest.raises(requests.HTTPError):
        fomc.fetch_content("https://x")


def test_fetch_documents_ignores_links_outside_the_fed(monkeypatch):
    html = (
        '<a href="https://attacker.example/x/pressreleases/monetary20261102a.htm">bad</a>'
        '<a href="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm">'
        "s</a>"
    )
    _stub_get(monkeypatch, fomc, text=html)
    assert fomc.fetch_fomc_documents()["statement"]["date"] == "2026-10-28"


def test_fetch_documents_ignores_impossible_and_future_dates(monkeypatch):
    html = (
        '<a href="/newsevents/pressreleases/monetary99991231a.htm">future</a>'
        '<a href="/newsevents/pressreleases/monetary20261345a.htm">impossible</a>'
        '<a href="/newsevents/pressreleases/monetary20261028a.htm">ok</a>'
    )
    _stub_get(monkeypatch, fomc, text=html)
    assert fomc.fetch_fomc_documents()["statement"]["date"] == "2026-10-28"


def _future_day() -> str:
    return (date.today() + timedelta(days=5)).isoformat()


@pytest.mark.parametrize("bad_date", ["9999-12-31", "2026-13-45", "20261103", "", None])
def test_tftc_rejects_bad_dates(monkeypatch, bad_date):
    _stub_get(monkeypatch, etf, {"recentSessions": [{"date": bad_date, "netFlowUsd": 1.0}]})
    with pytest.raises(ValueError):
        etf.fetch_tftc_latest_rows()


def test_tftc_rejects_future_date(monkeypatch):
    _stub_get(monkeypatch, etf, {"recentSessions": [{"date": _future_day(), "netFlowUsd": 1.0}]})
    with pytest.raises(ValueError):
        etf.fetch_tftc_latest_rows()


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), "abc"])
def test_tftc_rejects_non_finite_values(monkeypatch, bad_value):
    payload = {"recentSessions": [{"date": "2026-11-02", "netFlowUsd": bad_value}]}
    _stub_get(monkeypatch, etf, payload)
    with pytest.raises(ValueError):
        etf.fetch_tftc_latest_rows()


def test_coinmetrics_rejects_non_finite_values(monkeypatch):
    row = {"time": "2026-11-02T00:00:00.000Z", "FlowInExNtv": "NaN", "FlowOutExNtv": "1"}
    _stub_get(monkeypatch, coinmetrics, {"data": [row]})
    with pytest.raises(ValueError):
        coinmetrics.fetch_exchange_netflow_history()


def test_coinmetrics_error_text_stays_out_of_the_exception(monkeypatch):
    _stub_get(monkeypatch, coinmetrics, status=500, text="::warning::forged")
    with pytest.raises(RuntimeError) as exc:
        coinmetrics.fetch_exchange_supply()
    assert "forged" not in str(exc.value)


def test_fred_trims_to_limit_after_dropping_missing_rows(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "SECRET")
    payload = {
        "observations": [
            {"date": "2026-10-28", "value": "6"},
            {"date": "2026-10-21", "value": "."},
            {"date": "2026-10-14", "value": "5"},
            {"date": "2026-10-07", "value": "4"},
        ]
    }
    _stub_get(monkeypatch, fred, payload)
    rows = fred.fetch_observations("walcl", limit=2)
    assert [r["date"] for r in rows] == ["2026-10-28", "2026-10-14"]

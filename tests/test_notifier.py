import pytest
import requests

import notifier
from rules import BEARISH


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.text = "body"


def test_send_raises_on_http_error_without_url(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord/api/webhooks/1/TOKEN")
    monkeypatch.setattr(notifier.requests, "post", lambda *a, **k: _Resp(500))
    with pytest.raises(notifier.NotificationError) as exc:
        notifier.send("x")
    assert "TOKEN" not in str(exc.value)


def test_send_raises_on_connection_error_without_url(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord/api/webhooks/1/TOKEN")

    def fail(*args, **kwargs):
        raise requests.ConnectionError("https://discord/api/webhooks/1/TOKEN failed")

    monkeypatch.setattr(notifier.requests, "post", fail)
    with pytest.raises(notifier.NotificationError) as exc:
        notifier.send("x")
    assert "TOKEN" not in str(exc.value)


def test_send_raises_when_webhook_is_unset(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    with pytest.raises(notifier.NotificationError):
        notifier.send("x")


def test_send_succeeds_on_204(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord/x")
    calls = []
    monkeypatch.setattr(notifier.requests, "post", lambda *a, **k: calls.append(k) or _Resp(204))
    notifier.send("hello")
    assert calls[0]["json"] == {"content": "hello", "allowed_mentions": {"parse": []}}


def test_split_message_respects_limit_and_lines():
    text = "\n".join(f"line{i:04d}" for i in range(600))
    chunks = notifier.split_message(text, limit=1900)
    assert len(chunks) > 1
    assert all(len(c) <= 1900 for c in chunks)
    assert "\n".join(chunks) == text


def test_split_message_cuts_a_single_long_line():
    chunks = notifier.split_message("x" * 5000, limit=1900)
    assert "".join(chunks) == "x" * 5000
    assert all(len(c) <= 1900 for c in chunks)


def test_format_alert_contains_title_and_verdict():
    text = notifier.format_alert("CPI（9月分）", BEARISH, ["a", "b"])
    assert text.splitlines()[0] == "**【BTC Monitor】CPI（9月分）🔴 弱気**"
    assert "a\nb" in text


@pytest.mark.parametrize(
    "value,expected",
    [
        (137_300_000, "+$137.3M"),
        (-115_563_333.62, "-$115.6M"),
        (1_234_000_000, "+$1.23B"),
        (0, "+$0.0M"),
    ],
)
def test_format_usd_flow(value, expected):
    assert notifier.format_usd_flow(value) == expected


def test_format_btc_flow():
    assert notifier.format_btc_flow(3837.4) == "+3,837 BTC"
    assert notifier.format_btc_flow(-12.6) == "-13 BTC"


def test_format_periods():
    assert notifier.format_month("2026-09-01") == "9月分"
    assert notifier.format_week("2026-10-24") == "10/24週"
    assert notifier.format_day("2026-11-03") == "11/3"


def test_flow_band_text():
    band = {"low": -527e6, "high": 627e6, "samples": 90}
    assert notifier.flow_band_text(False, band) == "普段の範囲内"
    assert notifier.flow_band_text(True, band) == "⚠️ 普段の範囲を超えた"
    assert notifier.flow_band_text(None, None) == "普段の範囲は算出できず"

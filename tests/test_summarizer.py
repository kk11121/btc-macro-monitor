import json
from types import SimpleNamespace

import summarizer


def _response(text="", stop_reason="end_turn"):
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block], stop_reason=stop_reason)


class _Client:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _use(monkeypatch, result):
    client = _Client(result)
    monkeypatch.setattr(summarizer.anthropic, "Anthropic", lambda **k: client)
    return client


def test_summarize_minutes_success(monkeypatch):
    body = json.dumps({"judgment": "弱気", "summary": "要約"}, ensure_ascii=False)
    client = _use(monkeypatch, _response(body))
    result = summarizer.summarize_minutes("議事録の本文", "2026-09-16")
    assert result == {"judgment": "弱気", "emoji": "🔴", "summary": "要約"}
    assert client.calls[0]["model"] == "claude-sonnet-5-5"
    assert "議事録の本文" in client.calls[0]["messages"][0]["content"]


def test_summarize_minutes_truncates_overlong_summary(monkeypatch):
    body = json.dumps({"judgment": "中立", "summary": "あ" * 5000}, ensure_ascii=False)
    _use(monkeypatch, _response(body))
    result = summarizer.summarize_minutes("本文", "2026-09-16")
    assert len(result["summary"]) == summarizer.config.SUMMARY_MAX_CHARS


def test_summarize_minutes_api_failure_is_unknown(monkeypatch):
    _use(monkeypatch, RuntimeError("down"))
    result = summarizer.summarize_minutes("本文", "2026-09-16")
    assert result["judgment"] == "判定不可"
    assert result["emoji"] == "⚪"


def test_summarize_minutes_client_setup_failure_is_unknown(monkeypatch):
    def fail(**kwargs):
        raise TypeError("no credentials")

    monkeypatch.setattr(summarizer.anthropic, "Anthropic", fail)
    assert summarizer.summarize_minutes("本文", "2026-09-16")["judgment"] == "判定不可"


def test_summarize_minutes_refusal_is_unknown(monkeypatch):
    _use(monkeypatch, _response("", stop_reason="refusal"))
    assert summarizer.summarize_minutes("本文", "2026-09-16")["judgment"] == "判定不可"


def test_summarize_minutes_bad_json_is_unknown(monkeypatch):
    _use(monkeypatch, _response("not json"))
    assert summarizer.summarize_minutes("本文", "2026-09-16")["judgment"] == "判定不可"


def test_summarize_minutes_unexpected_label_is_unknown(monkeypatch):
    _use(monkeypatch, _response(json.dumps({"judgment": "様子見", "summary": "x"})))
    assert summarizer.summarize_minutes("本文", "2026-09-16")["judgment"] == "判定不可"


def test_summarize_minutes_without_content_never_calls_api(monkeypatch):
    client = _use(monkeypatch, _response("{}"))
    assert summarizer.summarize_minutes(None, "2026-09-16")["judgment"] == "判定不可"
    assert client.calls == []

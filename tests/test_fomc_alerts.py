import fomc_alerts

# ── FOMC ──────────────────────────────────────────────────────────────────────

STATEMENT = {"url": "https://x/monetary20261209a.htm", "date": "2026-12-09"}
MINUTES = {"url": "https://x/fomcminutes20261028.htm", "date": "2026-10-28"}


def _fomc(monkeypatch, statement=STATEMENT, minutes=MINUTES, content="本文"):
    monkeypatch.setattr(
        fomc_alerts.fomc,
        "fetch_fomc_documents",
        lambda: {"statement": statement, "minutes": minutes},
    )
    monkeypatch.setattr(fomc_alerts.fomc, "fetch_content", lambda url: content)


def _state_fomc(statement_date="2026-10-28", minutes_date="2026-09-16"):
    return {
        "fomc": {
            "statement": {"url": "https://x/old", "date": statement_date},
            "minutes": {"url": "https://x/old", "date": minutes_date},
        }
    }


def test_statement_is_judged_from_the_text(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(minutes_date="2026-10-28"))
    _fomc(
        monkeypatch,
        content="The Committee decided to lower the target range for the federal funds rate "
        "by 1/4 percentage point to 3-1/2 to 3-3/4 percent.",
    )
    fomc_alerts.check_documents(rt)
    assert len(sender.messages) == 1
    text = sender.messages[0]
    assert "FOMC声明" in text
    assert "🟢 強気" in text
    assert "3.50〜3.75%" in text
    assert "会合前の織り込みの控えなし" in text
    assert rt.store.get("fomc", "statement", "date") == "2026-12-09"


def test_statement_unreadable_is_unknown_but_still_sent(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(minutes_date="2026-10-28"))
    _fomc(monkeypatch, content="something else")
    fomc_alerts.check_documents(rt)
    assert "⚪ 判定不可" in sender.messages[0]
    assert rt.store.get("fomc", "statement", "date") == "2026-12-09"


def test_minutes_are_sent_separately_from_statement(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(statement_date="2026-12-09"))
    _fomc(monkeypatch)
    monkeypatch.setattr(
        fomc_alerts.summarizer,
        "summarize_minutes",
        lambda content, date: {"judgment": "弱気", "emoji": "🔴", "summary": "要約"},
    )
    fomc_alerts.check_documents(rt)
    assert len(sender.messages) == 1
    assert "FOMC議事録（10/28会合）" in sender.messages[0]
    assert "要約" in sender.messages[0]
    assert rt.store.get("fomc", "minutes", "date") == "2026-10-28"


def test_minutes_summary_failure_shows_unknown(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(statement_date="2026-12-09"))
    _fomc(monkeypatch, content=None)
    fomc_alerts.check_documents(rt)
    assert "⚪ 判定不可" in sender.messages[0]


def _fetch_content_fails(monkeypatch):
    def fail(url):
        raise RuntimeError("503")

    monkeypatch.setattr(fomc_alerts.fomc, "fetch_content", fail)


def test_statement_page_failure_does_not_advance_and_retries(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(minutes_date="2026-10-28"))
    _fomc(monkeypatch)
    _fetch_content_fails(monkeypatch)
    fomc_alerts.check_documents(rt)
    assert rt.store.get("fomc", "statement", "date") == "2026-10-28"
    assert "fomc_statement" in rt.store.get("errors")
    assert not any("判定不可" in m for m in sender.messages)


def test_minutes_page_failure_does_not_advance_and_retries(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc(statement_date="2026-12-09"))
    _fomc(monkeypatch)
    _fetch_content_fails(monkeypatch)
    fomc_alerts.check_documents(rt)
    assert rt.store.get("fomc", "minutes", "date") == "2026-09-16"
    assert "fomc_minutes" in rt.store.get("errors")


def test_minutes_first_run_is_recorded_silently(make_runtime, sender, monkeypatch):
    state = _state_fomc(statement_date="2026-12-09")
    state["fomc"]["minutes"] = {"url": None, "date": None}
    rt = make_runtime(state)
    _fomc(monkeypatch)

    def must_not_run(content, date):
        raise AssertionError("初回の議事録は要約しない")

    monkeypatch.setattr(fomc_alerts.summarizer, "summarize_minutes", must_not_run)
    fomc_alerts.check_documents(rt)
    assert sender.messages == []
    assert rt.store.get("fomc", "minutes", "date") == "2026-10-28"


def test_statement_send_failure_does_not_block_minutes(make_runtime, sender, monkeypatch):
    rt = make_runtime(_state_fomc())
    _fomc(monkeypatch)
    monkeypatch.setattr(
        fomc_alerts.summarizer,
        "summarize_minutes",
        lambda content, date: {"judgment": "中立", "emoji": "🟡", "summary": "要約"},
    )
    sender.fail_on = ["FOMC声明"]
    fomc_alerts.check_documents(rt)
    assert rt.store.get("fomc", "statement", "date") == "2026-10-28"  # 次回やり直す
    assert rt.store.get("fomc", "minutes", "date") == "2026-10-28"
    assert rt.notification_failed is True



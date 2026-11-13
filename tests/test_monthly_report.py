"""月次レポートの集計と送信。"""

from datetime import datetime, timedelta, timezone

import alert_log
import monthly_report


def _row(ts, indicator, verdict, price=100.0, p24=None, p72=None, kind="immediate"):
    return {"id": ts, "ts": ts, "indicator": indicator, "kind": kind, "verdict": verdict,
            "btc_price": price, "btc_price_1h": None, "btc_price_24h": p24,
            "btc_price_72h": p72}


def test_previous_month_and_jst_bounds():
    assert monthly_report.previous_month("2026-12-01") == "2026-11"
    assert monthly_report.previous_month("2027-01-01") == "2026-12"
    start, end = monthly_report.month_bounds("2026-11")
    assert start == datetime(2026, 10, 31, 15, tzinfo=timezone.utc)  # 日本時間 11/1 0:00
    assert end == datetime(2026, 11, 30, 15, tzinfo=timezone.utc)


def test_rows_in_month_uses_japan_time():
    rows = [_row("2026-10-31T16:00:00+00:00", "cpi", "強気"),  # 日本時間 11/1 1:00
            _row("2026-10-31T14:00:00+00:00", "cpi", "強気")]  # 日本時間 10/31
    assert len(monthly_report.rows_in_month(rows, "2026-11")) == 1


def test_hit_rate_excludes_neutral_and_missing_prices():
    rows = [
        _row("a", "cpi", "強気", p24=110.0, p72=90.0),   # 24h 当たり、72h 外れ
        _row("b", "cpi", "弱気", p24=95.0, p72=99.0),    # 両方当たり
        _row("c", "cpi", "中立", p24=150.0, p72=150.0),  # 除く
        _row("d", "cpi", "強気"),                         # 価格なしは除く
        _row("e", "spot_etf", "弱気", p24=101.0, kind="daily_flow"),
        _row("f", "morning_summary", None, kind="morning"),  # 集計しない種類
    ]
    stats = {s["indicator"]: s for s in monthly_report.summarize(rows, {24: 0.02, 72: 0.04})}
    assert set(stats) == {"cpi", "spot_etf"}
    cpi = stats["cpi"]
    assert cpi["count"] == 4
    assert cpi["verdicts"] == {"強気": 2, "弱気": 1, "中立": 1}
    assert cpi["hits_24"] == [True, True]
    assert cpi["hits_72"] == [False, True]
    # 値動き幅の平均（10%, 5%, 50%）/ 全体平均 2%
    assert round(cpi["ratio_24"], 2) == round((0.10 + 0.05 + 0.50) / 3 / 0.02, 2)
    assert stats["spot_etf"]["hits_24"] == [False]


def test_review_candidates_need_enough_samples():
    many_misses = [{"indicator": "vix", "hits_24": [False, False, True]},
                   {"indicator": "cpi", "hits_24": [False, False]}]
    assert [e["indicator"] for e in monthly_report.review_candidates(many_misses)] == ["vix"]


def test_baseline_moves_over_the_month():
    start, _ = monthly_report.month_bounds("2026-11")
    closes = {start + timedelta(hours=h): 100.0 + (h % 2) for h in range(24 * 40)}
    baseline = monthly_report.baseline_moves(closes, "2026-11")
    assert baseline[24] == 0.0  # 24時間ごとに同じ値に戻る
    assert baseline[72] == 0.0


def test_run_writes_report_sends_summary_and_marks_month(make_runtime, sender, tmp_path,
                                                         monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "kk11121/btc-macro-monitor")
    rt = make_runtime({})
    rt.alert_log_path = tmp_path / "alerts.jsonl"
    rt.dry_run = False
    alert_log.write(rt.alert_log_path, [
        _row("2026-11-10T13:31:00+00:00", "cpi", "弱気", p24=95.0, p72=97.0),
    ])
    reports = tmp_path / "reports"
    monthly_report.run(rt, "2026-12-01", fetch_closes=lambda s, e: {}, reports_dir=reports)
    text = (reports / "2026-11.md").read_text(encoding="utf-8")
    assert "# 月次レポート 2026年11月" in text
    assert "| CPI | 1 | 0 | 1 | 0 | 100%（1件） | 100%（1件） |" in text
    message = next(m for m in sender.messages if "月次レポート" in m)
    assert "CPI 1件" in message
    assert "https://github.com/kk11121/btc-macro-monitor/blob/main/reports/2026-11.md" in message
    assert rt.store.get("monthly", "last_month") == "2026-11"
    monthly_report.run(rt, "2026-12-01", fetch_closes=lambda s, e: {}, reports_dir=reports)
    assert sum("月次レポート" in m for m in sender.messages) == 1  # 同じ月は2回送らない


def test_run_force_resends(make_runtime, sender, tmp_path):
    rt = make_runtime({"monthly": {"last_month": "2026-11"}})
    rt.alert_log_path = tmp_path / "alerts.jsonl"
    monthly_report.run(rt, "2026-12-01", force=True, fetch_closes=lambda s, e: {},
                       reports_dir=tmp_path)
    assert any("月次レポート 2026年11月" in m for m in sender.messages)
    assert "集計対象の通知なし" in sender.messages[0]

"""ワークフローの設定（concurrency は1グループ、mode ごとの時間制限、記録のコミット）。"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "scheduler.yml"


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_single_concurrency_group(workflow):
    assert workflow["concurrency"] == {"group": "btc-monitor-state", "cancel-in-progress": False}


def test_timeout_is_120_for_hourly_and_20_otherwise(workflow):
    timeout = workflow["jobs"]["monitor"]["timeout-minutes"]
    assert "inputs.mode != 'hourly'" in timeout and "&& 20" in timeout
    assert "'47 22 * * *' && 20 || 120" in timeout


def test_modes_and_no_extra_schedules(workflow):
    on = workflow[True]  # PyYAML は on を True として読む
    assert on["workflow_dispatch"]["inputs"]["mode"]["options"] == ["hourly", "morning",
                                                                   "monthly"]
    assert [entry["cron"] for entry in on["schedule"]] == ["47 22 * * *", "23 * * * *"]


def test_bls_key_and_logs_reports_are_committed(workflow):
    steps = workflow["jobs"]["monitor"]["steps"]
    run_step = next(s for s in steps if s.get("name") == "Run monitor")
    assert run_step["env"]["BLS_API_KEY"] == "${{ secrets.BLS_API_KEY }}"
    commit = next(s for s in steps if s.get("name") == "Commit updated state")["run"]
    assert "git add logs" in commit and "git add reports" in commit

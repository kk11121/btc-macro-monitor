import json

import pytest

from state_store import (
    SCHEMA_VERSION,
    StateCorruptError,
    StateStore,
    load_etf_history,
    merge_etf_rows,
    migrate,
    save_etf_history,
)

LEGACY = {
    "cpi": {"date": "2026-09-01", "value": 336.298},
    "nfp": {"date": "2026-09-01", "value": 159044},
    "fomc": {"url": "https://example/monetary20261028a.htm", "date": "2026-10-28"},
    "market": {
        "dxy": {"value": 101.65},
        "us10y": {"value": 4.95},
        "nasdaq": {"value": 25164.3},
        "vix": {"value": 20.22},
    },
    "sec": {"date": "2026-11-03", "seen_ids": ["a", "b"]},
    "stablecoin": {
        "usdt": {"value": 181.0},
        "usdc": {"value": 71.0},
        "btc_exchange": {"value": 2660347.5},
    },
    "liquidity": {
        "m2sl": {"date": "2026-08-01", "value": 23342.8},
        "walcl": {"date": "2026-07-22", "value": 6747378},
    },
    "glassnode": {
        "fear_greed": {"value": 34, "zone": "fear", "date": "2026-11-04"},
        "etf_flow": {"date": "2026-11-03", "value": 5999.44},
        "funding_rate": {"value": 0.0001},
    },
    "unrate": {"date": "2026-09-01", "value": 4.2},
    "icsa": {"date": "2026-10-24", "value": 194000},
}


def test_migrate_keeps_macro_entries_untouched():
    state = migrate(LEGACY)
    for key in ("cpi", "nfp", "unrate", "icsa", "liquidity"):
        assert state[key] == LEGACY[key]


def test_migrate_splits_fomc_and_leaves_minutes_for_silent_seed():
    state = migrate(LEGACY)
    assert state["fomc"]["statement"] == LEGACY["fomc"]
    assert state["fomc"]["minutes"] == {"url": None, "date": None}


def test_migrate_renames_exchange_netflow_and_keeps_value():
    state = migrate(LEGACY)
    assert state["flows"]["exchange_netflow"]["date"] == "2026-11-03"
    assert state["flows"]["exchange_netflow"]["value"] == 5999.44
    assert state["flows"]["spot_etf"]["date"] is None


def test_migrate_moves_baselines_into_summary():
    values = migrate(LEGACY)["summary"]["values"]
    assert values["dxy"] == 101.65
    assert values["us10y"] == 4.95
    assert values["nasdaq"] == 25164.3
    assert values["vix"] == 20.22
    assert values["usdt"] == 181.0
    assert values["usdc"] == 71.0
    assert values["btc_exchange"] == 2660347.5
    assert values["fear_greed"] == {"value": 34, "zone": "fear", "date": "2026-11-04"}
    assert values["funding_rate"] == 0.0001


def test_migrate_keeps_vix_alert_baseline():
    assert migrate(LEGACY)["market"] == {"vix": {"value": 20.22}}


def test_migrate_removes_only_legacy_sections():
    state = migrate(LEGACY)
    for key in ("sec", "stablecoin", "glassnode"):
        assert key not in state
    assert state["schema_version"] == SCHEMA_VERSION
    assert state["errors"] == {}
    assert state["core_pce"] == {"date": None, "value": None}


def test_migrate_does_not_mutate_input():
    snapshot = json.dumps(LEGACY, sort_keys=True)
    migrate(LEGACY)
    assert json.dumps(LEGACY, sort_keys=True) == snapshot


def test_migrate_is_idempotent():
    once = migrate(LEGACY)
    assert migrate(once) == once


def test_migrate_absorbs_old_job_writing_back_over_new_format():
    """切り替え中の旧ジョブは、新形式の state に旧キーを足して書き戻す。"""
    state = migrate(LEGACY)
    state["sec"] = {"date": "2026-11-04", "seen_ids": []}
    state["glassnode"] = {"etf_flow": {"date": "2026-11-04", "value": 111.0}}
    state["fomc"] = {**state["fomc"], "url": "https://example/new.htm", "date": "2026-12-09"}
    again = migrate(state)
    assert "sec" not in again
    assert "glassnode" not in again
    assert again["flows"]["exchange_netflow"] == {"date": "2026-11-04", "value": 111.0}
    assert set(again["fomc"]) == {"statement", "minutes"}
    assert again["fomc"]["statement"]["date"] == "2026-12-09"


def test_migrate_new_format_does_not_copy_vix_alert_into_summary():
    """毎時の VIX 通知の基準と、朝のまとめの基準は別物。"""
    state = migrate(LEGACY)
    state["summary"]["values"] = {}
    state["market"]["vix"]["value"] = 30.0
    assert "vix" not in migrate(state)["summary"]["values"]


def test_migrate_older_legacy_flow_does_not_overwrite_newer():
    state = migrate(LEGACY)
    state["flows"]["exchange_netflow"] = {"date": "2026-11-05", "value": 1.0}
    state["glassnode"] = {"etf_flow": {"date": "2026-11-03", "value": 2.0}}
    assert migrate(state)["flows"]["exchange_netflow"]["date"] == "2026-11-05"


def test_load_saves_migrated_state(state_path):
    state_path.write_text(json.dumps(LEGACY), encoding="utf-8")
    store = StateStore(state_path)
    store.load()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == SCHEMA_VERSION
    assert "sec" not in saved


def test_dry_run_never_writes(state_path):
    text = json.dumps(LEGACY)
    state_path.write_text(text, encoding="utf-8")
    store = StateStore(state_path, dry_run=True)
    store.load()
    store.set(("cpi",), {"date": "x", "value": 1})
    assert store.get("cpi", "date") == "x"
    assert state_path.read_text(encoding="utf-8") == text


def test_load_missing_file_gives_defaults(state_path):
    store = StateStore(state_path)
    store.load()
    assert store.get("schema_version") == SCHEMA_VERSION
    assert store.get("flows", "spot_etf", "date") is None


@pytest.mark.parametrize(
    "content",
    ["{broken", "", "[1, 2]", "null", '{"market": null}', '{"flows": 1}', '{"fomc": []}'],
)
def test_corrupt_state_raises_and_keeps_file(state_path, content):
    state_path.write_text(content, encoding="utf-8")
    with pytest.raises(StateCorruptError):
        StateStore(state_path).load()
    assert state_path.read_text(encoding="utf-8") == content


def test_set_writes_nested_value_and_keeps_siblings(state_path):
    store = StateStore(state_path)
    store.load()
    before = store.state
    store.set(("flows", "spot_etf"), {"date": "2026-11-03", "value": 1.0})
    assert store.get("flows", "spot_etf", "value") == 1.0
    assert store.get("flows", "exchange_netflow", "date") is None
    assert before["flows"]["spot_etf"]["date"] is None  # 以前の参照は変わらない
    assert json.loads(state_path.read_text(encoding="utf-8"))["flows"]["spot_etf"]["value"] == 1.0


def test_non_finite_value_is_refused_and_file_stays_valid(state_path):
    store = StateStore(state_path)
    store.load()
    store.set(("cpi",), {"date": "d", "value": 1.0})
    with pytest.raises(ValueError):
        store.set(("cpi",), {"date": "d", "value": float("nan")})
    assert json.loads(state_path.read_text(encoding="utf-8"))["cpi"]["value"] == 1.0


def test_get_returns_default_for_missing_path(state_path):
    store = StateStore(state_path)
    store.load()
    assert store.get("nope", "deep", default=5) == 5


def test_merge_etf_rows_overrides_same_date_and_sorts():
    old = [
        {"date": "2026-11-01", "value": 1.0, "source": "tftc"},
        {"date": "2026-11-02", "value": 2.0, "source": "tftc"},
    ]
    new = [
        {"date": "2026-11-03", "value": 3.0, "source": "sosovalue"},
        {"date": "2026-11-02", "value": 2.5, "source": "sosovalue"},
    ]
    merged = merge_etf_rows(old, new, keep=10)
    assert [r["date"] for r in merged] == ["2026-11-01", "2026-11-02", "2026-11-03"]
    assert merged[1]["value"] == 2.5


def test_merge_etf_rows_trims_to_keep_newest():
    rows = [{"date": f"2026-10-{d:02d}", "value": 1.0, "source": "x"} for d in range(1, 11)]
    merged = merge_etf_rows([], rows, keep=3)
    assert [r["date"] for r in merged] == ["2026-10-08", "2026-10-09", "2026-10-10"]


def test_etf_history_roundtrip_creates_directory(history_path):
    data = {"backfilled": True, "rows": [{"date": "2026-11-03", "value": 1.0, "source": "x"}]}
    save_etf_history(history_path, data)
    assert load_etf_history(history_path) == data


def test_etf_history_missing_file_gives_empty(history_path):
    assert load_etf_history(history_path) == {"backfilled": False, "rows": []}

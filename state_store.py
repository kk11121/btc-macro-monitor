"""state.json と ETF 履歴の読み書き、および旧形式からの移行。"""

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

_NULL_DATED = {"date": None, "value": None}
_DEFAULT_STATE: dict = {
    "schema_version": SCHEMA_VERSION,
    "cpi": _NULL_DATED,
    "nfp": _NULL_DATED,
    "unrate": _NULL_DATED,
    "icsa": _NULL_DATED,
    "core_pce": _NULL_DATED,
    "fomc": {
        "statement": {"url": None, "date": None},
        "minutes": {"url": None, "date": None},
    },
    "market": {"vix": {"value": None}},
    "liquidity": {"m2sl": _NULL_DATED, "walcl": _NULL_DATED},
    "flows": {
        "spot_etf": {"date": None, "value": None, "source": None},
        "exchange_netflow": _NULL_DATED,
    },
    "summary": {"last_date": None, "values": {}},
    "errors": {},
}


class StateCorruptError(Exception):
    """state.json を読めない。上書きせずに止めるために使う。"""


# ── 移行 ───────────────────────────────────────────────────────────────────


def _value_of(section: dict, key: str) -> Any:
    entry = section.get(key)
    return entry.get("value") if isinstance(entry, dict) else None


def _legacy_baselines(state: dict) -> dict:
    """旧形式の「前回通知した値」を、朝のまとめの基準として取り出す。"""
    market = state.get("market", {})
    stablecoin = state.get("stablecoin", {})
    glassnode = state.get("glassnode", {})
    candidates = {
        "dxy": _value_of(market, "dxy"),
        "us10y": _value_of(market, "us10y"),
        "nasdaq": _value_of(market, "nasdaq"),
        "vix": _value_of(market, "vix"),
        "usdt": _value_of(stablecoin, "usdt"),
        "usdc": _value_of(stablecoin, "usdc"),
        "btc_exchange": _value_of(stablecoin, "btc_exchange"),
        "fear_greed": glassnode.get("fear_greed"),
        "funding_rate": _value_of(glassnode, "funding_rate"),
    }
    return {key: value for key, value in candidates.items() if value is not None}


def _migrate_fomc(fomc: dict) -> dict:
    """声明と議事録を分ける。旧ジョブが書き戻した url/date は声明として取り込む。"""
    statement = fomc.get("statement", copy.deepcopy(_DEFAULT_STATE["fomc"]["statement"]))
    if "url" in fomc:
        statement = {"url": fomc.get("url"), "date": fomc.get("date")}
    minutes = fomc.get("minutes", copy.deepcopy(_DEFAULT_STATE["fomc"]["minutes"]))
    return {"statement": statement, "minutes": minutes}


def _migrate_flows(state: dict) -> dict:
    """取引所純流入の etf_flow を exchange_netflow に改名する。新しい日付を残す。"""
    flows = {**copy.deepcopy(_DEFAULT_STATE["flows"]), **state.get("flows", {})}
    legacy = state.get("glassnode", {}).get("etf_flow")
    if not legacy or legacy.get("date") is None:
        return flows
    current_date = flows["exchange_netflow"].get("date")
    if current_date is None or legacy["date"] > current_date:
        flows["exchange_netflow"] = {"date": legacy["date"], "value": legacy.get("value")}
    return flows


def migrate(raw: dict) -> dict:
    """
    旧形式の state を現在の形式にして返す。入力は変更しない。

    schema_version ではなく中身を見て判定するので、切り替え中に旧ジョブが
    旧キーを書き戻しても、次の読み込みで取り込める。何度実行しても結果は同じ。
    """
    state = copy.deepcopy(raw)
    # 旧形式のときだけ写す。新形式の market.vix は毎時の通知の基準で、朝の基準とは別物
    is_legacy = state.get("schema_version") != SCHEMA_VERSION
    baselines = _legacy_baselines(state) if is_legacy else {}
    summary = state.get("summary", {})
    values = {**baselines, **summary.get("values", {})}
    vix_value = _value_of(state.get("market", {}), "vix")

    migrated = {key: value for key, value in state.items()
                if key not in ("sec", "stablecoin", "glassnode")}
    migrated["fomc"] = _migrate_fomc(state.get("fomc", {}))
    migrated["flows"] = _migrate_flows(state)
    migrated["market"] = {"vix": {"value": vix_value}}
    migrated["summary"] = {"last_date": summary.get("last_date"), "values": values}
    migrated["schema_version"] = SCHEMA_VERSION
    for key, default in _DEFAULT_STATE.items():
        migrated.setdefault(key, copy.deepcopy(default))
    return migrated


# ── state.json ─────────────────────────────────────────────────────────────


def _write_json(path: Path, data: dict) -> None:
    """途中で落ちても壊れないよう、一時ファイルに書いてから置き換える。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    # allow_nan=False：NaN は JSON として不正で、書くと次回の読み込みで壊れる
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _with_value(state: dict, path: tuple[str, ...], value: Any) -> dict:
    """path の位置だけ差し替えた新しい dict を返す。"""
    head, *rest = path
    updated = value if not rest else _with_value(state.get(head) or {}, tuple(rest), value)
    return {**state, head: updated}


class StateStore:
    def __init__(self, path: Path, dry_run: bool = False) -> None:
        self.path = Path(path)
        self.dry_run = dry_run
        self._state: dict = copy.deepcopy(_DEFAULT_STATE)

    @property
    def state(self) -> dict:
        return self._state

    def load(self) -> None:
        """読み込んで移行する。壊れていたら StateCorruptError を出し、ファイルは触らない。"""
        if not self.path.exists():
            self._state = copy.deepcopy(_DEFAULT_STATE)
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StateCorruptError(f"{self.path.name} を JSON として読めません") from exc
        if not isinstance(raw, dict):
            raise StateCorruptError(f"{self.path.name} の中身がオブジェクトではありません")
        try:
            migrated = migrate(raw)
        except (AttributeError, TypeError, KeyError) as exc:
            raise StateCorruptError(f"{self.path.name} の形が想定と違います") from exc
        self._state = migrated
        if self._state != raw and not self.dry_run:
            logger.info("state.json を schema_version %s に移行しました", SCHEMA_VERSION)
            _write_json(self.path, self._state)

    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self._state
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, path: tuple[str, ...], value: Any) -> None:
        updated = _with_value(self._state, path, value)
        if not self.dry_run:
            _write_json(self.path, updated)
        self._state = updated

    def delete(self, path: tuple[str, ...]) -> None:
        *parents, leaf = path
        section = self.get(*parents, default={})
        self.set(tuple(parents), {k: v for k, v in section.items() if k != leaf})


# ── ETF の履歴 ─────────────────────────────────────────────────────────────


def load_etf_history(path: Path) -> dict:
    if not path.exists():
        return {"backfilled": False, "rows": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_etf_history(path: Path, history: dict) -> None:
    _write_json(path, history)


def merge_etf_rows(old_rows: list[dict], new_rows: list[dict], keep: int) -> list[dict]:
    """同じ日は新しい方を優先して重ね、古い順に並べて keep 件に絞る。"""
    by_date = {row["date"]: row for row in [*old_rows, *new_rows]}
    return [by_date[date] for date in sorted(by_date)][-keep:]

"""
FOMC の利下げ確率（SoSoValue の /macro/fomc-probabilities）と、その計算。

会合ごとの確率の配列は rate_ranges より短いことがある（2026年11月13日の実測）。
先頭から対応させ、足りない分と null は 0 として扱う。
"""

from datetime import datetime, timezone

import config
from fetchers import sosovalue
from fetchers.validation import finite_float

PERCENT = 100.0


def parse_range(text: str) -> tuple[float, float]:
    """'375-400' → (3.75, 4.00)。単位は bp の整数で返ってくる。"""
    low, high = str(text).split("-")
    return int(low) / PERCENT, int(high) / PERCENT


def _probability(raw: object) -> float:
    if raw in (None, ""):
        return 0.0
    return finite_float(raw)


def normalize(raw: dict) -> dict:
    """
    Returns:
        {"as_of": ISO（UTC）, "meetings": [{"date", "probs": [(下限, 上限, 確率%)]}]}
    """
    ranges = [parse_range(text) for text in raw["rate_ranges"]]
    meetings = []
    for meeting in raw["meetings"]:
        values = list(meeting.get("probabilities") or [])
        probs = [
            (low, high, _probability(values[i]) if i < len(values) else 0.0)
            for i, (low, high) in enumerate(ranges)
        ]
        meetings.append({"date": str(meeting["meeting_date"]), "probs": probs})
    as_of = datetime.fromtimestamp(int(raw["as_of"]) / 1000, timezone.utc)
    return {
        "as_of": as_of.isoformat(timespec="seconds"),
        "meetings": sorted(meetings, key=lambda m: m["date"]),
    }


def meeting_stats(probs: list[tuple], target: dict) -> dict:
    """利下げ確率・利上げ確率（%）と期待金利（%）。確率の合計が100でなくても割合で扱う。"""
    total = sum(p for _, _, p in probs)
    if total <= 0:
        raise ValueError("FOMC probabilities sum to zero")
    cut = sum(p for _, high, p in probs if high <= target["lower"])
    hike = sum(p for low, _, p in probs if low >= target["upper"])
    expected = sum((low + high) / 2 * p for low, high, p in probs) / total
    return {
        "cut_prob": round(cut / total * PERCENT, 1),
        "hike_prob": round(hike / total * PERCENT, 1),
        "expected_rate": round(expected, 4),
    }


def summarize(normalized: dict, target: dict, today_et: str) -> dict:
    """
    次回会合（今日以降で最も近い会合）の確率と期待金利、3会合先までの織り込み幅。

    target は今の誘導目標 {"lower", "upper"}（%）。
    """
    upcoming = [m for m in normalized["meetings"] if m["date"] >= today_et]
    if not upcoming:
        raise ValueError("no upcoming FOMC meeting in SoSoValue data")
    stats = meeting_stats(upcoming[0]["probs"], target)
    horizon = upcoming[min(config.FOMC_OUTLOOK_HORIZON, len(upcoming)) - 1]
    horizon_rate = meeting_stats(horizon["probs"], target)["expected_rate"]
    target_mid = (target["lower"] + target["upper"]) / 2
    return {
        "meeting": upcoming[0]["date"],
        **stats,
        "horizon_meeting": horizon["date"],
        "priced_bp": round((horizon_rate - target_mid) * PERCENT, 1),
        "as_of": normalized["as_of"],
    }


def fetch() -> dict:
    return normalize(sosovalue.fetch_fomc_probabilities())

"""
判定の向きと閾値を一元管理するファイル。

「どちらを強気とするか」「どれくらい動いたら通知するか」をここだけで調整できます。
他のコードを触らず、この値だけ変えれば挙動が変わります。
"""

# ── 判定の向き ─────────────────────────────────────────────────────────────
RISE_BULLISH = 1   # 上がったら強気
RISE_BEARISH = -1  # 上がったら弱気

# 値の上下で判定する指標（設計書「判定ルール」）。
# 予想値がある指標（core_cpi など）は、Phase B までは前回値との比較に使う。
DIRECTION: dict[str, int] = {
    "dxy": RISE_BEARISH,
    "us10y": RISE_BEARISH,
    "nasdaq": RISE_BULLISH,
    "vix": RISE_BEARISH,
    "usdt": RISE_BULLISH,
    "usdc": RISE_BULLISH,
    "m2sl": RISE_BULLISH,
    "walcl": RISE_BULLISH,
    "btc_exchange": RISE_BEARISH,
    "core_cpi": RISE_BEARISH,
    "core_pce": RISE_BEARISH,
    "nfp": RISE_BEARISH,
    "unrate": RISE_BULLISH,
    "icsa": RISE_BULLISH,
}

# 「同じ」とみなす幅。ここにない指標は完全一致のときだけ中立。
# CPI・コアPCE・失業率は小数1桁に丸めてから比べるので幅は持たない。
FLAT_BAND: dict[str, float] = {
    "nfp": 25.0,     # 千人（±2.5万人）
    "icsa": 5000.0,  # 件
}

# 流れの量を見る指標。前日との差ではなく、その日の符号で判定する。
FLOW_DIRECTION: dict[str, int] = {
    "spot_etf": RISE_BULLISH,          # ETF への流入は現物の買い
    "exchange_netflow": RISE_BEARISH,  # 取引所への流入は売り圧力
}

# FOMC声明の決定と判定（"bullish" / "bearish" / "neutral"）
FOMC_DECISION_VERDICT: dict[str, str] = {
    "lower": "bullish",
    "raise": "bearish",
    "maintain": "neutral",
}

# ── VIX ───────────────────────────────────────────────────────────────────
VIX_ALERT_RISE_PCT = 10.0      # 前回通知値からの上昇率がこれ以上で、
VIX_ALERT_LEVEL = 20.0         # かつこの水準を超えたら即時通知
VIX_BASELINE_DROP_PCT = 10.0   # これ以上下がったら通知せず基準だけ下げる

# ── 日次フロー ─────────────────────────────────────────────────────────────
FLOW_LOOKBACK_DAYS = 90    # 普段の振れ幅を計算する日数（ETF は取引日）
FLOW_SIGMA = 2.0           # 平均 ± この倍率 × 標準偏差 を「普段の範囲」とする
FLOW_MIN_SAMPLES = 20      # これ未満の履歴では範囲を計算しない
ETF_HISTORY_KEEP_DAYS = 120  # history/etf_flows.json に残す取引日数

# ── 朝のまとめの注記 ───────────────────────────────────────────────────────
FUNDING_NOTE_THRESHOLD = 0.0003  # ±0.03% を超えたら注記
FEAR_GREED_NOTE_ZONES = {"extreme_fear", "extreme_greed"}

# ── 発表時刻 ───────────────────────────────────────────────────────────────
RELEASE_TIME_ET = (8, 30)  # CPI・雇用統計・コアPCE・新規失業保険申請（米東部）

# ── Claude（FOMC議事録の要約のみ）─────────────────────────────────────────
SUMMARY_MODEL = "claude-sonnet-5-5"
SUMMARY_MAX_TOKENS = 16000
SUMMARY_MAX_CHARS = 400  # 通知に載せる要約の上限

"""
判定の向きと閾値を一元管理するファイル。

「どちらを強気とするか」「どれくらい動いたら通知するか」をここだけで調整できます。
他のコードを触らず、この値だけ変えれば挙動が変わります。
"""

# ── 判定の向き ─────────────────────────────────────────────────────────────
RISE_BULLISH = 1   # 上がったら強気
RISE_BEARISH = -1  # 上がったら弱気

# 値の上下で判定する指標（設計書「判定ルール」）。
# 予想値がある指標（core_cpi など）は、予想が取れなかった回だけ前回値との比較に使う。
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
    "fomc_expected": RISE_BEARISH,  # 次回会合の期待金利
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

# ── 発表日程と時刻（米東部）────────────────────────────────────────────────
RELEASE_TIME_ET = (8, 30)  # CPI・雇用統計・コアPCE・新規失業保険申請
FOMC_TIME_ET = (14, 0)     # FOMC声明・議事録
FOMC_MINUTES_DELAY_DAYS = 21  # 議事録は会合の3週間後に公表される

# FRED の release_id（2026年11月13日に series/release で確認）
RELEASES: dict[str, dict] = {
    "cpi": {"release_id": 10, "label": "CPI", "time_et": RELEASE_TIME_ET},
    "employment": {"release_id": 50, "label": "雇用統計", "time_et": RELEASE_TIME_ET},
    "icsa": {"release_id": 180, "label": "新規失業保険申請", "time_et": RELEASE_TIME_ET},
    "core_pce": {"release_id": 54, "label": "コアPCE", "time_et": RELEASE_TIME_ET},
}
CALENDAR_REFRESH_HOURS = 20   # 発表日程の控えがこれより古ければ取り直す
CALENDAR_ROWS = 20            # FRED から取る日程の件数（新しい順。先の予定が先頭に来る）

# ── 予想値 ─────────────────────────────────────────────────────────────────
# 単位："percent"（%）、"thousands"（千人）、"count"（件）
FORECAST_METRICS: dict[str, dict] = {
    "core_cpi_mom": {"release": "cpi", "unit": "percent", "direction": RISE_BEARISH,
                     "sosovalue": "Core CPI (MoM)", "forexfactory": "Core CPI m/m"},
    "core_cpi_yoy": {"release": "cpi", "unit": "percent", "direction": RISE_BEARISH,
                     "sosovalue": "Core CPI (YoY)", "forexfactory": "Core CPI y/y"},
    "cpi_mom": {"release": "cpi", "unit": "percent", "direction": RISE_BEARISH,
                "sosovalue": "CPI (MoM)", "forexfactory": "CPI m/m"},
    "cpi_yoy": {"release": "cpi", "unit": "percent", "direction": RISE_BEARISH,
                "sosovalue": "CPI (YoY)", "forexfactory": "CPI y/y"},
    "nfp": {"release": "employment", "unit": "thousands", "direction": RISE_BEARISH,
            "sosovalue": "Nonfarm Payrolls", "forexfactory": "Non-Farm Employment Change"},
    "unrate": {"release": "employment", "unit": "percent", "direction": RISE_BULLISH,
               "sosovalue": None, "forexfactory": "Unemployment Rate"},
    "icsa": {"release": "icsa", "unit": "count", "direction": RISE_BULLISH,
             "sosovalue": "Initial Jobless Claims", "forexfactory": "Unemployment Claims"},
    "core_pce_mom": {"release": "core_pce", "unit": "percent", "direction": RISE_BEARISH,
                     "sosovalue": "Core PCE Price Index (MoM)",
                     "forexfactory": "Core PCE Price Index m/m"},
}
# 予想と「同じ」とみなす幅（実績の単位）。% の指標は小数1桁に丸めて比べるので 0
FORECAST_MATCH_BAND: dict[str, float] = {
    "nfp": 25.0,     # 千人（±2.5万人）
    "icsa": 5000.0,  # 件
}
SOSOVALUE_DATE_TOLERANCE_DAYS = 1  # SoSoValue の日付は発表日と1日ずれることがある

# ── FOMC の金利見通し ───────────────────────────────────────────────────────
FOMC_OUTLOOK_ALERT_PT = 15.0  # 利下げ確率か利上げ確率が前回通知からこれ以上動いたら通知
FOMC_OUTLOOK_HORIZON = 3      # 何会合先までの織り込み幅を出すか
FOMC_SURPRISE_BP = 10.0       # 決定と会合前の期待金利の差がこれを超えたら強気・弱気
FOMC_EXPECTED_DIRECTION = RISE_BEARISH  # 期待金利が上がったら弱気

# ── 発表待ち受け（毎時の監視の中で動く）────────────────────────────────────
WATCH_LOOKAHEAD_MINUTES = 60   # 起動からこの分数以内の発表を待つ
WATCH_POLL_SECONDS = 30        # 発表時刻からの取得間隔
WATCH_MAX_MINUTES = 45         # 発表時刻からこの分数で打ち切る（遅れて動いた回もここまで拾う）
WATCH_PREFETCH_MINUTES = 3     # 発表のこの分数前に予想値を取り直す
HOURLY_RUN_MINUTE = 23         # 毎時の監視の起動（分）。朝のまとめとの重なりのテストに使う
HOURLY_OVERHEAD_MINUTES = 10   # 待ち受け以外の確認にかかる時間の見込み
MORNING_RUN_JST = (7, 47)      # 朝のまとめの起動（日本時間）
MORNING_MAX_MINUTES = 20       # 朝のまとめの最長の実行時間（ワークフローの時間制限）

# ── SoSoValue の上限 ───────────────────────────────────────────────────────
SOSOVALUE_WARN_RATIO = 0.2  # 今月の残りが上限のこの割合を切ったら朝のまとめで警告

# ── BLS（CPI・雇用統計の実績）──────────────────────────────────────────────
BLS_SERIES: dict[str, str] = {
    "cpi": "CUSR0000SA0",           # CPI 総合・季節調整あり
    "cpi_nsa": "CUUR0000SA0",       # CPI 総合・季節調整なし
    "core_cpi": "CUSR0000SA0L1E",   # コアCPI・季節調整あり
    "core_cpi_nsa": "CUUR0000SA0L1E",  # コアCPI・季節調整なし
    "nfp": "CES0000000001",         # 非農業部門雇用者数（千人）
    "unrate": "LNS14000000",        # 失業率
}

# ── 検証（通知の記録と月次レポート）───────────────────────────────────────
PRICE_HORIZONS_HOURS = (1, 24, 72)  # 通知後の BTC 価格を書き足す時間
PRICE_FILL_MAX_AGE_DAYS = 120       # これより古い行の空欄は埋めにいかない
MONTHLY_MIN_SAMPLES = 3             # 見直し候補に挙げる最低件数
MONTHLY_REVIEW_HIT_RATE = 0.5       # 当たり率がこれ未満なら見直し候補

# ── Claude（FOMC議事録の要約のみ）─────────────────────────────────────────
SUMMARY_MODEL = "claude-sonnet-5-5"
SUMMARY_MAX_TOKENS = 16000
SUMMARY_MAX_CHARS = 400  # 通知に載せる要約の上限

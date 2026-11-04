# BTC Macro Monitor

ビットコイン短期トレードの**意思決定補助**を目的としたマクロ・オンチェーン指標監視システム。

GitHub Actions で定期実行し、結果を Discord に送る。強気・弱気の判定は `config.py` に書いた固定ルールで行い、Claude を使うのは FOMC議事録の要約だけ（年8回）。

改修の全体像は [docs/redesign.md](docs/redesign.md) を参照。現在は Phase A まで実装済みで、予想値がある指標も前回値との比較で判定している（通知に「予想値なし」と表示する）。

## 通知の種類

| 種類 | タイミング | 中身 |
|------|-----------|------|
| 即時通知 | 発表・異変を検知した直後 | CPI、雇用統計、失業率、新規失業保険申請、コアPCE、FOMC声明・議事録、VIX急騰 |
| 日次フロー | 新しい日のデータを見つけた直後 | 現物BTC ETFの資金流出入、取引所BTC純流入。普段の範囲を超えたら強調する |
| 朝のまとめ | 毎朝 7:47 ごろ（日本時間） | 各指標の前回まとめ比、ETF・純流入の最新値、取得の稼働状況 |
| 取得エラー | 失敗の1回目と復旧時だけ | 続いている間は朝のまとめの稼働状況に載る |

## 指標の振り分けと判定

向きと閾値はすべて `config.py` で変えられる。

| 指標 | ソース | 通知 | 判定 |
|------|--------|------|------|
| CPI | FRED（CPILFESL ほか） | 即時 | コア前月比が先月の前月比より上なら弱気 |
| 雇用統計（NFP） | FRED（PAYEMS） | 即時 | 雇用者数の増減が先月より多ければ弱気（±2.5万人は中立） |
| 失業率 | FRED（UNRATE） | 即時 | 前回より上なら強気 |
| 新規失業保険申請 | FRED（ICSA） | 即時 | 前回より多ければ強気（±5,000件は中立） |
| コアPCE | FRED（PCEPILFE） | 即時 | 前月比が先月の前月比より上なら弱気 |
| FOMC声明 | federalreserve.gov | 即時 | 利下げは強気、利上げは弱気、据え置きは中立。本文から機械的に読み取る |
| FOMC議事録 | federalreserve.gov | 即時 | Claude が要約して判定。失敗時は「⚪ 判定不可」 |
| VIX | Yahoo Finance | 前回通知値から+10%以上かつ20超で即時、毎朝まとめ | 上昇は弱気 |
| 現物BTC ETF | SoSoValue（代替 TFTC） | 日次フロー、まとめ | 流入は強気、流出は弱気 |
| 取引所BTC純流入 | CoinMetrics | 日次フロー、まとめ | 流入は弱気、流出は強気 |
| DXY・米10年債 | Yahoo Finance | まとめ | 上昇は弱気 |
| NASDAQ | Yahoo Finance | まとめ | 上昇は強気 |
| 恐怖&強欲指数 | Alternative.me | まとめ | 判定なし。極度の恐怖・強欲のとき注記 |
| Funding Rate | OKX | まとめ | 判定なし。±0.03%超のとき注記 |
| USDT・USDC供給量 | CoinGecko | まとめ | 増加は強気 |
| 取引所BTC残高 | CoinMetrics | まとめ | 増加は弱気 |
| M2・FRBバランスシート | FRED | 新しい発表があった日のまとめ | 増加は強気。前回の発表と比べる |

- 普段の範囲：過去90日（ETFは90取引日）の平均 ± 2標準偏差
- VIX：10%以上下がったら通知せず基準だけ下げる。高いままの基準で次の急騰を見逃さないため
- 前年比は季節調整なし、前月比は季節調整ありの系列で計算し、小数1桁に丸めて比べる

## 指標の読み方（なぜBTCに効くのか）

実際は複数の要因が絡むため、以下は「基本の型」で例外もあります。

### 金利・インフレ系（CPI・NFP・失業率・新規失業保険申請・FOMC・米10年債）

**基本の型**: インフレが高い／景気が強い → FRB（米中央銀行）が金利を高く保つ → 預金や国債の魅力が増す → 株やBTCなどのリスク資産から資金が抜ける → **BTCに下押し**。逆に、インフレ鈍化や雇用悪化は利下げ期待につながり **BTCに追い風**。

| 指標 | 動いた向き | BTCへの向き（ざっくり） |
|------|-----------|----------------------|
| CPI・コアPCE（物価）上振れ | インフレ高い | 🔴 弱気寄り（利下げが遠のく） |
| 失業率↑ / NFP下振れ | 景気弱い | 🟢 強気寄り（利下げ期待）※後退懸念が強すぎると逆に売られることも |
| 新規失業保険申請↑ | 労働市場が弱い | 🟢 強気寄り（利下げ期待） |
| 米国10年債利回り↑ | 無リスク金利上昇 | 🔴 弱気寄り（資金が債券へ） |
| FOMC（タカ派＝引き締め寄り） | 金利を高く維持 | 🔴 弱気寄り |

### ドル・株・恐怖指数（DXY・NASDAQ・VIX）

| 指標 | 動いた向き | BTCへの向き |
|------|-----------|------------|
| DXY（ドル指数）↑ | ドル高 | 🔴 弱気寄り（ドル建て資産に逆風） |
| NASDAQ↑ | ハイテク株高 | 🟢 強気寄り（BTCはリスクオンで連動しやすい） |
| VIX（恐怖指数）急騰 | 市場が恐怖 | 🔴 弱気寄り（リスク回避で資金逃避） |

### お金の量（M2・FRBバランスシート・USDT/USDC供給量）

**基本の型**: 世の中に出回るお金が増える → 余ったお金がリスク資産に向かう → **BTCに追い風**。

| 指標 | 動いた向き | BTCへの向き |
|------|-----------|------------|
| M2マネーサプライ↑ / FRBバランスシート拡大 | 金融緩和 | 🟢 強気寄り |
| USDT・USDC供給量↑ | 暗号資産市場の待機資金が増加 | 🟢 強気寄り（減少は🔴弱気寄り） |

### オンチェーン・需給（現物ETF・取引所BTC・Funding Rate・恐怖&強欲指数）

| 指標 | 動いた向き | BTCへの向き |
|------|-----------|------------|
| 現物BTC ETFへの流入 | 現物の買い | 🟢 強気寄り（流出は🔴弱気寄り） |
| 取引所BTC残高↑ / 純流入 | 売る準備のBTCが取引所に集まる | 🔴 弱気寄り（純流出は🟢強気寄り＝長期保有へ） |
| Funding Rate 大きくプラス | ロング（買い持ち）が過熱 | ⚠️ 急落（ロング・スクイーズ）に注意。マイナスへ反転は底打ちの兆候になり得る |
| 恐怖&強欲指数 | 市場心理 | **逆張り指標**: 「極度の恐怖」は買い場の候補、「極度の強欲」は過熱・調整警戒 |

## 限界と免責

- 本システムは**意思決定の補助**であって、売買シグナルではありません。最終判断は必ずご自身で行ってください。
- **指標にはラグがあります**。FRED の更新は公式発表より遅く、Phase A の即時通知は毎時の監視で検知するため、発表から1時間ほど遅れることがあります。
- **判定は固定ルール**で、値の向きしか見ません。改定値や内訳、声明文の中身は判定に入りません。
- **FOMC議事録の判定は Claude（AI）による推定**で、外れることがあります。
- データは**無料API**に依存しており、提供側の仕様変更や一時障害で取得が止まる場合があります（その際はエラー通知が届きます）。
- 暗号資産は価格変動が非常に大きく、本システムは損失を防ぐものではありません。**投資は自己責任**で行ってください。

## セットアップ

### 1. GitHub Secretsを登録

リポジトリの `Settings > Secrets and variables > Actions` から以下を登録：

| Secret名 | 取得元 | 備考 |
|----------|--------|------|
| `FRED_API_KEY` | https://fred.stlouisfed.org/docs/api/api_key.html | 無料 |
| `SOSOVALUE_API_KEY` | https://sosovalue.com/developer/dashboard | 無料。未設定なら ETF は TFTC で代替 |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/ | 従量課金。FOMC議事録の要約のみ（年8回） |
| `DISCORD_WEBHOOK_URL` | Discord サーバー設定 → 連携サービス → ウェブフック | 無料 |
| `COINGECKO_API_KEY` | https://www.coingecko.com/en/api → Demo API | 任意・レート制限緩和用 |

### 2. GitHub Actionsを有効化

リポジトリの `Actions` タブでワークフローを有効にする。

## ジョブ

時刻はUTC。GitHub の定時実行は毎時0分付近が混むので、実行の分をずらしている。

| ジョブ | mode | 実行 | 中身 |
|--------|------|------|------|
| 朝のまとめ | `morning` | 毎日 22:47（日本時間 7:47） | 全指標の取得、まとめの通知 |
| 毎時の監視 | `hourly` | 毎時 23分 | VIX急騰、FOMC（臨時の発表を含む）、日次フローの新着、経済指標の新着 |

手動実行：

```
Actions → BTC Macro Monitor → Run workflow → mode を選択
```

## ローカル実行

```bash
pip install -r requirements-dev.txt

# .envファイルを作成して各APIキーを記入
cp .env.example .env

python main.py --mode hourly --dry-run    # 送信も保存もしない。通知の本文を画面に出す
python main.py --mode morning --dry-run

python main.py --mode hourly              # 実際に送信し、state.json を更新する
python main.py --mode morning

pytest tests
```

`--dry-run` を付けない実行はローカルの `state.json` を書き換えます。本番の状態は GitHub Actions がコミットしているので、確認には `--dry-run` を使ってください。

## 状態の保存

| ファイル | 中身 |
|----------|------|
| `state.json` | 通知済みの日付、朝のまとめの基準値、失敗中の指標 |
| `history/etf_flows.json` | ETF の日次の値（直近120取引日）。普段の範囲の計算に使う |

- 旧形式の `state.json` は、読み込み時に自動で移行する
- `state.json` が壊れていたら上書きせずに止まり、送れれば Discord に知らせる。git の履歴から復元する
- ETF の履歴は、初回だけ TFTC から過去分を取り込み、以降は毎日の値を自分で貯める

## ファイル構成

```
btc-macro-monitor/
├── .github/workflows/scheduler.yml  # GitHub Actions（自動実行）
├── fetchers/
│   ├── fred.py                      # FRED API（CPI・NFP・失業率・新規申請・コアPCE・M2・WALCL）
│   ├── fomc.py                      # FOMC声明・議事録のスクレイピング
│   ├── market.py                    # Yahoo Finance（DXY・US10Y・NASDAQ・VIX）
│   ├── etf.py                       # 現物BTC ETF（SoSoValue、代替 TFTC）
│   ├── stablecoin.py                # CoinGecko（USDT・USDC）
│   ├── glassnode.py                 # Alternative.me（Fear&Greed）+ OKX（Funding Rate）
│   ├── coinmetrics.py               # CoinMetrics Community（取引所BTC残高・純流入）
│   └── validation.py                # 外部データの検証（日付・数値）
├── history/etf_flows.json           # ETF の日次の履歴（自動で作成）
├── tests/                           # pytest
├── config.py                        # 判定の向きと閾値の一元管理
├── rules.py                         # 判定と計算（純粋関数）
├── jobs_hourly.py                   # 毎時の監視
├── jobs_morning.py                  # 朝のまとめ
├── runtime.py                       # 指標ごとの切り離し、失敗の1回目・復旧時の通知
├── state_store.py                   # state.json と ETF 履歴の読み書き、移行
├── summarizer.py                    # FOMC議事録の要約（Claude API）
├── notifier.py                      # Discord Webhook通知と本文の組み立て
├── state.json                       # 状態（Gitで永続化）
├── main.py                          # エントリーポイント
└── requirements.txt
```

## 通知フォーマット

**即時通知**
```
【BTC Monitor】CPI（9月分）🟡 中立
コア前月比 +0.3%（先月 +0.3%） ← 判定に使用
コア前年比 +2.6%（先月 +2.4%）
総合前月比 +0.6%（先月 +0.4%）
総合前年比 +3.7%（先月 +3.4%）
予想値なし（前回値と比較）
発表 10/14 21:30（日本時間）
🔗 https://fred.stlouisfed.org/series/CPILFESL
```

**日次フロー**
```
【BTC Monitor】現物BTC ETF（11/3分）🔴 弱気
-$115.6M 流出 ／ 普段の範囲内
普段の範囲（過去90取引日）-$527.0M 〜 +$627.7M
出所：SoSoValue
```

**朝のまとめ**
```
【BTC Monitor】朝のまとめ 11月4日(水)
■ マクロ・市場（前回まとめ比）
 DXY 101.50 ↓0.2% 🟢
 米10年債 4.95% →0bp 🟡
 NASDAQ 25,372 ↑0.8% 🟢
 VIX 20.8 ↑3.0% 🔴
■ 暗号資産
 現物BTC ETF -$115.6M（11/3分）流出 🔴 普段の範囲内
 取引所BTC純流入 +5,999 BTC（11/3分）流入 🔴 普段の範囲内
 恐怖&強欲 34 恐怖（前回 34）
 Funding Rate（OKX）+0.010%
 USDT 178.4B ↓1.5% 🔴 ／ USDC 70.6B ↓0.7% 🔴
 取引所BTC残高 2.667M ↑0.3% 🔴
■ 流動性（前回の発表比）
 M2 23,414十億ドル ↑0.5% 🟢（9月分）
■ 稼働状況：すべて正常
```

## データの出典

- 現物BTC ETF の過去分（初回の取り込み）と、SoSoValue が使えないときの代替：[The Commoner Bitcoin ETF Tracker by TFTC](https://www.tftc.io/bitcoin-etf-flows)（[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)）。その日の値を TFTC から取った通知には出典を載せる

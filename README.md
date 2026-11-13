# BTC Macro Monitor

ビットコイン短期トレードの**意思決定補助**を目的としたマクロ・オンチェーン指標監視システム。

GitHub Actions で定期実行し、結果を Discord に送る。強気・弱気の判定は `config.py` に書いた固定ルールで行い、Claude を使うのは FOMC議事録の要約だけ（年8回）。

改修の全体像は [docs/redesign.md](docs/redesign.md) を参照。Phase B+C まで実装済み（報告は [docs/phase-bc-report.md](docs/phase-bc-report.md)）。

## 通知の種類

| 種類 | タイミング | 中身 |
|------|-----------|------|
| 即時通知 | 発表・異変を検知した直後 | CPI、雇用統計（NFP・失業率）、新規失業保険申請、コアPCE（予想比で判定）、FOMC声明・議事録、FOMC金利見通しの急変、VIX急騰 |
| 日次フロー | 新しい日のデータを見つけた直後 | 現物BTC ETFの資金流出入、取引所BTC純流入。普段の範囲を超えたら強調する |
| 朝のまとめ | 毎朝 7:47 ごろ（日本時間） | 今日・明日の予定と予想値、金利見通し、各指標の前回まとめ比、ETF・純流入の最新値、取得の稼働状況と SoSoValue の残り回数 |
| 月次レポート | 毎月1日の朝のまとめのあと | 前月の指標ごとの件数、当たり率、通知後の値動きの大きさ |
| 取得エラー | 失敗の1回目と復旧時だけ | 続いている間は朝のまとめの稼働状況に載る |

## 指標の振り分けと判定

向きと閾値はすべて `config.py` で変えられる。

| 指標 | ソース | 通知 | 判定 |
|------|--------|------|------|
| CPI | 実績は BLS（代替 FRED）、予想は SoSoValue と ForexFactory | 即時 | コア前月比が予想より上なら弱気。総合・前年比も予想と並べる |
| 雇用統計 | 実績は BLS（代替 FRED）、予想は同上（失業率は ForexFactory のみ） | 即時 | NFP が予想より上なら弱気（±2.5万人は一致）。失業率を同じ通知に並べる |
| 新規失業保険申請 | 実績は SoSoValue（代替 FRED）、予想は同上 | 即時 | 予想より多ければ強気（±5,000件は一致） |
| コアPCE | 実績は SoSoValue（代替 FRED）、予想は同上 | 即時 | 前月比が予想より上なら弱気 |
| FOMC声明 | federalreserve.gov と SoSoValue の利下げ確率 | 即時 | 決定後の誘導目標の中央と、会合前の期待金利の差が +10bp 超なら弱気、−10bp 未満なら強気 |
| FOMC金利見通し | SoSoValue（`/macro/fomc-probabilities`）、誘導目標は FRED | 利下げ確率か利上げ確率が前回通知から15ポイント以上動いたら即時、毎朝まとめ | 次回会合の期待金利が上がれば弱気 |
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
- 前年比の基準月（12か月前）が FRED で欠測のとき（2025年10月など）は「—（基準月のデータなし）」と出す。2026年10月分は今月の値、11月分は「先月」の値がこの表示になる

### 予想値との比べ方

- 予想値は SoSoValue（`/macro/events/{event}/history`）と ForexFactory の週次カレンダーの両方から取り、2つを範囲として扱う。実績が範囲（それぞれの「同じとみなす幅」を含む）の中なら一致、上なら上振れ、下なら下振れ
- 片方しか取れなければその1つと比べ、両方なければ前回値と比べて「予想値なし」と出す
- SoSoValue の値は文字列（CPI 系は `"0.3%"`、NFP と申請件数は千単位の数字）、ForexFactory は `"65K"` の形なので、単位をそろえてから比べる
- SoSoValue の次回分は実績が空の行。履歴が1件もなければ指標名の誤りとしてエラー、次回の行がまだないだけならエラーにしない
- 指標名は `config.py` の `FORECAST_METRICS` に固定で持つ（SoSoValue の予定一覧は使わない）
- 予想値は朝のまとめと発表の直前に取り直す。ForexFactory は非公式なので、取るのは1日2〜3回まで
- CPI はコア前月比で判定し、コア前年比が逆の結果なら「前年比は逆方向」、雇用統計は失業率が逆なら「失業率は逆方向」と添える

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
- **指標にはラグがあります**。発表待ち受けは発表時刻から30秒間隔で取りに行きますが、FRED は公式発表より遅く（CPI は約40分遅れた実績があります）、BLS のキーがないときや待ち受けで取れなかったときは、次の回の予備確認（最大で発表から約1時間後）になります。
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
| `SOSOVALUE_API_KEY` | https://sosovalue.com/developer/dashboard | 無料。予想値・利下げ確率・ETF。未設定なら ETF は TFTC で代替 |
| `BLS_API_KEY` | https://data.bls.gov/registrationEngine/ | 無料。CPI・雇用統計の実績を早く取る。未設定や無効なら FRED で代替 |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/ | 従量課金。FOMC議事録の要約のみ（年8回） |
| `DISCORD_WEBHOOK_URL` | Discord サーバー設定 → 連携サービス → ウェブフック | 無料 |
| `COINGECKO_API_KEY` | https://www.coingecko.com/en/api → Demo API | 任意・レート制限緩和用 |

### 2. GitHub Actionsを有効化

リポジトリの `Actions` タブでワークフローを有効にする。

### 3. 外部タイマーを設定

GitHub の定時実行は間引かれて数時間遅れることがあるので、外部タイマーから起動する。手順は「[外部タイマーからの起動](#外部タイマーからの起動)」。

## ジョブ

起動は外部タイマー（cron-job.org）からの `workflow_dispatch` が主で、GitHub の定時実行（`schedule`）は予備。予備の時刻はUTCで、毎時0分付近が混むので実行の分をずらしている。

| ジョブ | mode | 予備の定時実行 | 中身 |
|--------|------|------|------|
| 朝のまとめ | `morning` | 毎日 22:47（日本時間 7:47） | 予定と予想値の更新、金利見通し、全指標の取得、まとめの通知。1日は月次レポートも |
| 毎時の監視 | `hourly` | 毎時 23分 | VIX急騰、FOMC（臨時の発表を含む）、金利見通しの変化、日次フローの新着、経済指標の予備確認、**発表待ち受け**、通知後の BTC 価格の書き足し |
| 月次レポート | `monthly` | なし（手動のみ） | 朝のまとめで送れなかったときの送り直し |

- 朝のまとめは、その日（日本時間）にすでに送っていれば何もせず終わる。外部タイマーと予備の定時実行の両方が動いても二重には届かない。送信に失敗した日は記録しないので、次の起動で送り直す
- 毎時の監視は、通知済みの日付を `state.json` で見ているので、何度動いても同じ通知は届かない
- ワークフローの時間制限は、毎時の監視が120分（発表待ち受けを含むため）、ほかは20分

手動実行：

```
Actions → BTC Macro Monitor → Run workflow → mode を選択
```

朝のまとめ・月次レポートを送り直すときは `force` にチェックを入れる。

### 発表待ち受け（毎時の監視の中で動く）

別のジョブや cron-job.org の予定は作らない。毎時の監視が起動したとき、その後60分以内に発表があれば、発表時刻まで待ち、発表時刻から30秒間隔で取りに行く（最長45分）。

1. いつもの確認（VIX、FOMC、日次フロー、金利見通し、経済指標の予備確認）を先に済ませる
2. 発表の3分前に予想値を取り直す（FOMC声明の日は、会合直前の期待金利も控える）
3. 発表時刻から30秒間隔で、主な取得先（CPI・雇用統計は BLS、申請件数・コアPCE は SoSoValue）を見て、新しい値がなければ FRED も見る
4. 取れた指標から通知し、発表時刻から取れるまでの秒数を `logs/release_latency.jsonl` に書く
5. 45分たっても取れなければエラー通知（1回目と復旧時のみ）にし、次の回の予備確認（FRED）が拾う。二重には通知しない

毎時の監視は日本時間の毎時23分に動く。米東部と日本の時差は13時間か14時間でちょうど1時間単位なので、米東部でも毎時23分になり、どの発表にも直前の回がある。

| 発表（米東部） | 待ち受けを始める回（米東部） | 冬時間の日本時間 | 夏時間の日本時間 |
|---|---|---|---|
| 8:30（CPI・雇用統計・新規失業保険申請・コアPCE） | 8:23 | 22:23 | 21:23 |
| 10:00（今は対象指標なし） | 9:23 | 23:23 | 22:23 |
| 14:00（FOMC声明・議事録） | 13:23 | 翌 3:23 | 翌 2:23 |

- 待ち受けで長く動くのは日本時間でおよそ 20:23〜翌4:55 の間だけで、朝のまとめ（7:47）とは重ならない（テストで確かめている）。`concurrency` は1グループのまま
- 予備の定時実行が遅れて動いた回も、発表から45分以内でまだ取れていなければ取りに行く
- 毎時の23分の回が飛ばされると、その発表は次の回の予備確認になる

### SoSoValue の呼び出し回数（上限 月1万回）

| 用途 | 回数 | 月 |
|------|------|------|
| ETF（毎時の監視） | 1日24回 | 約720 |
| 利下げ確率（毎時の監視） | 1日24回 | 約720 |
| 朝のまとめ（予定の予想値と利下げ確率） | 1日1〜8回 | 約240 |
| 待ち受けの直前の予想値 | 発表のある回に1〜4回 | 約40 |
| 待ち受けの実績（申請件数・コアPCE） | 発表1回あたり最大90回 | 最大約480 |
| 手動実行・再試行の余裕 | ― | 約300 |
| 合計 | | **約2,500（上限の25%）** |

残り回数は応答ヘッダー（`x-ratelimit-remaining-month`）から `state.json` の `sosovalue` に記録し、朝のまとめの稼働状況に出す。上限の2割を切ったら警告する。

## 外部タイマーからの起動

GitHub の定時実行は混雑すると間引かれ、数時間遅れたり飛ばされたりする（実績では1日に約5回しか動かなかった）。そのため、cron-job.org から GitHub の API を呼んでワークフローを起動する。

### トークン

fine-grained personal access token を作る（GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens）。

| 項目 | 設定 |
|------|------|
| Repository access | Only select repositories → `kk11121/btc-macro-monitor` だけ |
| Repository permissions | **Actions: Read and write**（Metadata: Read-only は自動で付く）。ほかは不要 |
| Expiration | 期限を付け、切れる前に作り直す。切れると外部からの起動が止まり、予備の定時実行だけになる |

- classic のトークンでも動くが、`repo` スコープが要り、自分の全リポジトリに効くのですすめない
- トークンは cron-job.org にだけ保存する。このリポジトリや GitHub Secrets には入れない（ワークフロー側では使わない）

### リクエスト

| 項目 | 値 |
|------|------|
| アドレス | `https://api.github.com/repos/kk11121/btc-macro-monitor/actions/workflows/scheduler.yml/dispatches` |
| メソッド | `POST` |
| ヘッダー | `Accept: application/vnd.github+json`<br>`Authorization: Bearer <トークン>`<br>`X-GitHub-Api-Version: 2022-11-28`<br>`Content-Type: application/json` |
| 成功の応答 | 2xx（本文なしの 204、または実行の ID 入りの 200） |

GitHub の API は `User-Agent` ヘッダーも必須だが、cron-job.org が自動で付ける。

送る内容（ジョブごとに cron-job.org の予定を分ける）：

| 予定 | 時刻（タイムゾーン Asia/Tokyo） | 送る内容 |
|------|------|------|
| 朝のまとめ | 毎日 7:47 | `{"ref": "main", "inputs": {"mode": "morning"}}` |
| 毎時の監視 | 毎時 23分 | `{"ref": "main", "inputs": {"mode": "hourly"}}` |

- `ref` は `main` 固定。`inputs` を省くと `hourly` で動く
- 送り直しは手動のときだけ：`{"ref": "main", "inputs": {"mode": "morning", "force": "true"}}`。定期の予定には `force` を入れない（毎回送ってしまう）
- 失敗の通知を受けられるよう、cron-job.org の失敗時の通知を有効にしておく

手元から試すとき：

```bash
curl -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_DISPATCH_TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/kk11121/btc-macro-monitor/actions/workflows/scheduler.yml/dispatches \
  -d '{"ref": "main", "inputs": {"mode": "hourly"}}'
```

### 同時に起動されたとき

- ワークフローは `concurrency` で1つずつ順に流れる。各実行は始まった時点の `main` を取り直すので、前の実行が保存した `state.json` を読んでから動く
- GitHub の仕様で、順番待ちにできるのは1件だけ。待ちの間にさらに起動されると、古い待ちは取り消される。朝のまとめが取り消されても、その日に送っていなければ後から動く予備の定時実行が送る

## ローカル実行

```bash
pip install -r requirements-dev.txt

# .envファイルを作成して各APIキーを記入
cp .env.example .env

python main.py --mode hourly --dry-run    # 送信も保存もしない。通知の本文を画面に出す
python main.py --mode morning --dry-run

python main.py --mode hourly              # 実際に送信し、state.json を更新する
python main.py --mode morning
python main.py --mode morning --force     # その日に送信済みでも送り直す
python main.py --mode monthly --force     # 前月の月次レポートを送り直す

pytest tests

# 過去の通知の復元（state.json の git 履歴から。結果は reports/ に書く）
python scripts/reconstruct_history.py --since 2026-05-24
```

`--dry-run` を付けない実行はローカルの `state.json` を書き換えます。本番の状態は GitHub Actions がコミットしているので、確認には `--dry-run` を使ってください。

## 状態の保存

| ファイル | 中身 |
|----------|------|
| `state.json` | 通知済みの日付と発表日、朝のまとめを送った日と基準値、失敗中の指標、発表日程と予想値の控え、金利見通しの基準、SoSoValue の残り回数 |
| `history/etf_flows.json` | ETF の日次の値（直近120取引日）。普段の範囲の計算に使う |
| `logs/alerts.jsonl` | 全通知の記録（1通知1行）。通知時点と1・24・72時間後の BTC 価格（OKX の1時間足）を毎時の監視で書き足す。取れなかった欄は空のまま次の実行で再試行する |
| `logs/release_latency.jsonl` | 発表時刻から実績が取れるまでの秒数（指標・取得先ごと） |
| `reports/YYYY-MM.md` | 月次レポート |
| `reports/history-2026-05-24.md`、`reports/history-events.csv` | 過去の通知の復元と、その後の BTC の値動きの集計 |

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
│   ├── sosovalue.py                 # SoSoValue の共通の呼び出しと残り回数
│   ├── forexfactory.py              # ForexFactory 週次カレンダー（予想値）
│   ├── bls.py                       # BLS 公式API v2（CPI・雇用統計の実績）
│   ├── fomc_outlook.py              # 利下げ確率と期待金利の計算
│   ├── okx.py                       # OKX の1時間足（検証用の価格）
│   └── validation.py                # 外部データの検証（日付・数値）
├── history/etf_flows.json           # ETF の日次の履歴（自動で作成）
├── logs/                            # 通知の記録と発表までの時間（自動で作成）
├── reports/                         # 月次レポートと過去分の復元
├── scripts/reconstruct_history.py   # 過去の通知の復元と集計
├── tests/                           # pytest
├── config.py                        # 判定の向きと閾値、指標名、発表時刻の一元管理
├── rules.py                         # 判定と計算（純粋関数）
├── forecasts.py                     # 予想値の取得と単位の変換
├── macro.py                         # 経済指標の実績・判定・通知文（待ち受けと予備確認で共通）
├── fomc_alerts.py                   # FOMC声明・議事録・金利見通し
├── release_calendar.py              # 発表日程（FRED と SoSoValue の会合日）
├── release_watch.py                 # 発表待ち受け（毎時の監視の中で動く）
├── alert_log.py                     # 通知の記録と BTC 価格の書き足し
├── monthly_report.py                # 月次レポート
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

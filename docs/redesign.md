# BTC Macro Monitor 改修設計書

Oct 30, 2026 · @草川優三

## 方針

通知を「即時通知・朝のまとめ・月次レポート」の3種類に分け、強気・弱気の判定はClaude APIを使わない固定ルールに置き換える。Claudeを使うのはFOMC議事録の要約だけ（年8回）にする。背景として、5か月（2026年5月24日〜10月29日）の通知約2,000件のうち72%がSECで、相場を動かすFOMC・CPI・雇用関連は2%だった。

変えること

- 判定：Claudeの推測をやめ、予想値がある指標は「予想より上か下か同じか」、ない指標は「前回通知した値から上がったか下がったか」で決める
- 予想値：SoSoValue公式APIとForexFactory（非公式の経済指標カレンダー）の両方から取り、2つの予想を範囲として比べる。片方しか取れなければその1つで、両方取れなければ前回値との比較で判定する
- FOMC：予想値の代わりに市場の織り込み（会合ごとの利下げ確率）と比べて判定する。利下げ確率は毎日追い、大きく動いたら通知する
- 通知：即時通知は相場を動かすものだけにし、残りは毎朝1回のまとめに集める
- 監視対象：SECの監視をやめ、コアPCEと現物BTC ETFの資金流出入を加える
- 検証：すべての通知を記録し、その後のBTCの値動きと突き合わせる

残すこと

- GitHub ActionsとDiscordによる無料運用、state.jsonによる状態管理
- 指標ごとに失敗を切り離す作りと、config.pyでの閾値の一元管理
- 取引所BTC純流入は毎日届ける（朝のまとめに必ず載せる）

## 通知の設計

相場を動かす即時通知は1日1件未満、日次フロー（ETFと取引所純流入）は各1日1件、朝のまとめは毎日1件、月次レポートは月1件を目安にする（現状は1日約13件）。即時通知が鳴ったら必ず見る、という状態を作るのが狙い。

| 種類 | タイミング（日本時間） | 中身 |
| --- | --- | --- |
| 即時通知 | 発表・異変の直後 | 経済指標の結果と予想比、FOMC、FOMCの利下げ確率の急変、VIX急騰 |
| 日次フロー | 新しい日のデータが出た直後 | 現物BTC ETFの資金流出入、取引所BTC純流入。普段の振れ幅を超えたら強調する |
| 朝のまとめ | 毎朝8時ごろ | 当日・翌日の重要指標の予定と予想値、各指標の前回まとめ比、取得の稼働状況 |
| 月次レポート | 毎月1日 | 指標ごとの判定の当たり率と、通知後のBTCの値動き |

指標の振り分け

| 指標 | 即時通知 | 朝のまとめ |
| --- | --- | --- |
| CPI（総合・コア） | 発表直後、予想比で判定 | 予定と予想値 |
| 雇用統計（NFP・失業率） | 発表直後、予想比で判定 | 予定と予想値 |
| 新規失業保険申請 | 発表直後、予想比で判定 | 予定と予想値 |
| コアPCE（新規追加） | 発表直後、予想比で判定 | 予定と予想値 |
| FOMC声明 | 発表直後、決定を会合前の市場の織り込みと比べて判定 | 予定 |
| FOMC議事録 | 公表直後、Claudeが要約 | 予定 |
| FOMCの金利見通し（新規追加） | 次回会合の利下げ確率が前回通知から15ポイント以上動いたとき | 次回会合の利下げ確率と前日比、3会合先までの利下げ織り込み幅 |
| VIX | 前回通知値から+10%以上、かつ20超 | 毎日 |
| 取引所BTC純流入 | 日次フローとして毎日。普段の振れ幅（過去90日の平均±2標準偏差）を超えたら強調 | 最新値 |
| 現物BTC ETFの資金流出入（新規追加） | 日次フローとして米国の取引日ごと。普段の振れ幅（過去90取引日の平均±2標準偏差）を超えたら強調 | 最新値 |
| DXY・米10年債・NASDAQ | なし | 毎日 |
| 恐怖&強欲指数・Funding Rate | なし | 毎日 |
| USDT・USDC供給量、取引所BTC残高 | なし | 毎日 |
| M2・FRBバランスシート | なし | 更新があった日 |
| 取得エラー | 失敗の1回目と復旧時だけ | 稼働状況に一覧。SoSoValueの今月の残り回数も載せ、2割を切ったら警告する |

朝のまとめの見本（数値は見本）

```
【BTC Monitor】朝のまとめ 〇月〇日(〇)
■ 今日・明日の予定
 〇/〇 22:30 CPI　予想：コア前月比 +0.3%、コア前年比 +3.0%
■ 金利見通し（次回FOMC 〇/〇）
 利下げ確率 62%（前日比 +8pt）🟢
 3会合先までの利下げ織り込み −48bp（前日比 −6bp）
■ マクロ・市場（前回まとめ比）
 DXY 102.21 ↓0.3% 🟢
 米10年債 4.99% ↑4bp 🔴
 NASDAQ 26,155 ↑0.8% 🟢
 VIX 16.9 ↓5% 🟢
■ 暗号資産
 現物BTC ETF +$137.3M（〇/〇分）流入 🟢 普段の範囲内
 取引所BTC純流入 +3,837 BTC（〇/〇分）流入 🔴 普段の範囲内
 恐怖&強欲 51 中立（前回 53）
 Funding Rate（OKX）+0.010%
 USDT 181.0B ↑0.2% 🟢 ／ USDC 71.1B ↑0.1% 🟢
 取引所BTC残高 2.687M ↓0.1% 🟢
■ 稼働状況：すべて正常（SoSoValue 今月の残り 8,950回）
```

即時通知の見本（数値は見本）

```
【BTC Monitor】CPI（〇月分）🔴 弱気
コア前月比 +0.4%（予想 +0.3%）上振れ ← 判定に使用
コア前年比 +3.1%（予想 +3.0%）上振れ
総合前月比 +0.3%（予想 +0.3%）一致
総合前年比 +3.0%（予想 +2.9%）上振れ
発表 〇/〇 22:30 ／ 予想値の出所：SoSoValue・ForexFactory

【BTC Monitor】雇用統計（〇月分）🟡 中立
NFP +6.4万人（予想 6.3万〜6.5万人）一致 ← 判定に使用
失業率 4.2%（予想 4.2%）一致
発表 〇/〇 22:30 ／ 予想値の出所：SoSoValue・ForexFactory

【BTC Monitor】FOMC金利見通し 🟢 強気
次回会合（〇/〇）の利下げ確率 78%（前回通知 60%）+18pt
3会合先までの利下げ織り込み −62bp（前回通知 −45bp）
出所：SoSoValue（〇/〇 〇:〇 時点）

【BTC Monitor】FOMC声明 🔴 弱気
決定：据え置き（誘導目標 3.50〜3.75%）
会合前の織り込み：利下げ確率 70%、期待金利 3.45%
決定との差 +17.5bp → 市場の予想より引き締め的
```

## 判定ルール

予想値がある指標は「実績が予想より上か、下か、同じか」、ない指標は「前回通知した値から上がったか下がったか」で強気・弱気を決める。どちら向きを強気とするかは指標ごとに固定し、config.pyで変えられるようにする。比べる相手は、即時通知なら前回の即時通知、朝のまとめなら前回のまとめの値とする。

予想値がある指標

| 指標 | 判定に使う数字 | 予想より上なら | 「同じ」とみなす幅 | 名前（SoSoValue／ForexFactory） |
| --- | --- | --- | --- | --- |
| CPI | コアCPI前月比 | 弱気 | 小数1桁で一致 | Core CPI (MoM)／Core CPI m/m。前年比は Core CPI (YoY)／Core CPI y/y |
| 雇用統計 | NFP（雇用者数の前月からの増減） | 弱気 | ±2.5万人 | Nonfarm Payrolls／Non-Farm Employment Change |
| 失業率 | 失業率 | 強気 | 小数1桁で一致 | SoSoValueになし／Unemployment Rate |
| 新規失業保険申請 | 申請件数 | 強気 | ±5,000件 | Initial Jobless Claims／Unemployment Claims |
| コアPCE | コアPCE前月比 | 弱気 | 小数1桁で一致 | Core PCE Price Index (MoM)／Core PCE Price Index m/m |
| FOMC声明 | 決定後の誘導目標（範囲の中央）と、会合前の期待金利の差 | 弱気 | ±10bp | 予想値ではなく利下げ確率（/macro/fomc-probabilities）を使う |

- 予想値はSoSoValueとForexFactoryの両方から取り、2つの予想を範囲として扱う。実績が2つの予想の間（それぞれの「同じとみなす幅」を含む）なら一致、高い方より上なら上振れ、低い方より下なら下振れとする。2つが同じ値なら、1つの予想と比べるのと同じ結果になる
- 片方しか取れなかった回はその1つで判定する。両方取れなかった回は前回値との比較で判定し、「予想値なし」と明記する
- 通知には「予想 6.3万〜6.5万人」のように範囲で表示する
- コアCPIは前月比と前年比の両方を、予想値と並べて載せる。判定は前月比で行い、前年比が逆の結果なら「前年比は逆方向」と添える
- 総合CPIの前月比・前年比など、判定に使わない数字も予想値と並べて表示する
- 雇用統計はNFPで判定し、失業率が逆の結果なら「失業率は逆方向」と添える
- ForexFactory側のCPIとコアPCEの名前は、発表週のカレンダーで確かめる

計算で押さえる点

- 実績は予想値と同じ形にそろえる。CPIは指数から前月比・前年比を、NFPは雇用者数から前月からの増減を計算する
- 前年比は季節調整なしの系列（CPIAUCNS・CPILFENS）、前月比は季節調整ありの系列（CPIAUCSL・CPILFESL）から計算する。公式発表がこの組み合わせで、崩すと0.1ずれることがある
- 比較は公式発表と同じく小数1桁に丸めてから行う
- SoSoValueの値は文字列で返る。CPI系・コアPCEは "0.3%" の形（整数は "0%"、負は "-0.4%"）、NFPと新規失業保険申請は千単位の数字だけ（"63" は6.3万人）。ForexFactoryは "65K" の形なので、同じ単位にそろえてから比べる
- SoSoValueの次回分の予想は、実績が空（""）の行とする。予定一覧（/macro/events）は抜けや1日のずれがあるため使わない
- SoSoValueの履歴が1件もなければ、指標名の誤りとしてエラーを通知する。過去の行はあるが次回の行がまだないときは、エラーにせずForexFactoryだけで判定する
- FOMC声明の政策金利は、本文の決まり文句（maintain / lower / raise と誘導目標の範囲）から機械的に読み取る
- 期待金利は、各金利レンジの中央値に確率を掛けて足したもの。利下げ確率は、現在の誘導目標より下のレンジの確率の合計。現在の誘導目標はFRED（DFEDTARU・DFEDTARL）から取る
- 3会合先までの利下げ織り込み幅は、3回先の会合の期待金利と現在の誘導目標（範囲の中央）の差
- FOMC声明の判定に使う「会合前の期待金利」は、会合当日の朝のまとめで取った値を控えておく

予想値がない指標

| 指標 | 上昇したら | 下落したら |
| --- | --- | --- |
| FOMCの期待金利（次回会合） | 弱気 | 強気 |
| DXY | 弱気 | 強気 |
| 米10年債利回り | 弱気 | 強気 |
| NASDAQ | 強気 | 弱気 |
| VIX | 弱気 | 強気 |
| USDT・USDC供給量 | 強気 | 弱気 |
| M2・FRBバランスシート | 強気 | 弱気 |
| 取引所BTC残高 | 弱気 | 強気 |

例外は3つ。

- 取引所BTC純流入は、その日が流入（プラス）なら弱気、流出（マイナス）なら強気とする。流れの量なので、前日との差より向きに意味がある
- 現物BTC ETFの資金流出入は、流入なら強気、流出なら弱気とする。取引所とは逆で、ETFへの流入は現物の買いを意味する
- 恐怖&強欲指数とFunding Rateは数値とゾーンを示すだけで、強気・弱気は付けない。極度の恐怖・極度の強欲、Funding Rate ±0.03%超のときだけ注記する

## データソース

新しく加えるのは、SoSoValue公式API（予想値、FOMCの利下げ確率、現物BTC ETFの資金流出入）、実績を早く取るBLS公式API、予想値のもう1つの取得先になるForexFactoryの週次カレンダーの3つ。SECの取得はやめ、残りは今の取得先をそのまま使う。

| データ | 取得先 | 必要なキー | 備考 |
| --- | --- | --- | --- |
| 予想値（新規） | SoSoValue公式API（GET /macro/events/{event}/history）。ベースURLは https://openapi.sosovalue.com/openapi/v1、キーはヘッダー x-soso-api-key で送る | SOSOVALUE\_API\_KEY（取得済み） | 失業率以外の7指標が取れる（2026年11月4日に確認）。予定一覧（/macro/events）は使わない。上限は実測で1分100回・月1万回で、残り回数は応答のヘッダー（x-ratelimit-remaining-month）で分かる |
| 予想値（新規、2つ目） | ForexFactory 週次カレンダーJSON | 不要 | 非公式で、今週分のみ。失業率の予想はこちらだけ。項目は title・country・date・impact・forecast・previous |
| 発表日程（新規） | FRED（GET /fred/release/dates） | FRED\_API\_KEY（既存） | 公式の発表予定日。時刻は config.py に固定で置く（CPI・雇用統計・コアPCE・新規失業保険申請は米東部8:30、FOMC声明・議事録は14:00）。各発表の release\_id は実装時に確認する |
| FOMCの利下げ確率（新規） | SoSoValue公式API（GET /macro/fomc-probabilities） | SOSOVALUE\_API\_KEY | 今後の会合ごと（11月4日時点で9回分）に、各金利レンジになる確率。会合の日付もここから取る |
| CPI・雇用統計の実績（新規） | BLS公式API v2 | BLS\_API\_KEY（無料登録） | 発表時刻に合わせて取る。取れなければFREDで代替 |
| 現物BTC ETFの資金流出入（新規） | SoSoValue公式API（GET /etfs/summary-history）。失敗時はTFTCで代替 | SOSOVALUE\_API\_KEY | 項目は date・total\_net\_inflow（米ドル、数値で返る）など。TFTCを使った通知には出典表記が必要（CC BY 4.0） |
| 新規失業保険申請、コアPCE、M2、FRBバランスシート、政策金利の誘導目標 | FRED | FRED\_API\_KEY（既存） | 誘導目標の上限・下限（DFEDTARU・DFEDTARL）を追加。残りは現状どおり |
| FOMC声明・議事録 | federalreserve.gov | 不要 | 声明と議事録を別々に記録する |
| DXY・米10年債・NASDAQ・VIX | Yahoo Finance（yfinance） | 不要 | 現状どおり |
| 取引所BTC残高・純流入 | CoinMetrics Community API | 不要 | 現状どおり。UTCの1日が終わってから中央値4.7時間後（日本時間13時40分ごろ）に出る（5か月の実測） |
| Funding Rate、検証用のBTC価格 | OKX公開API | 不要 | 検証用に1時間足を追加で取る |
| 恐怖&強欲指数 | Alternative.me | 不要 | 現状どおり |
| USDT・USDC供給量 | CoinGecko | 任意 | 現状どおり |
| FOMC議事録の要約 | Claude API（claude-sonnet-5-5） | ANTHROPIC\_API\_KEY（既存） | 年8回だけ |

ForexFactoryの取得先は `https://nfs.faireconomy.media/ff_calendar_thisweek.json` で、項目は利用例で確認した（[source](https://www.mql5.com/en/forum/343908/16870225)）。当初の候補だった cdn-nfs.faireconomy.media は名前解決できず、来週分（ff\_calendar\_nextweek.json）は404だった（2026年11月4日の事前確認）。今週分しか載らないため毎朝取り直し、予想値を state.json に控えておく。

SoSoValueのキーは開発者ダッシュボードで申請し、承認を待つ（[source](https://sosovalue.gitbook.io/soso-value-api-doc/setting-up-your-api-key.md)）。日次の合計は `GET /etfs/summary-history`（symbol=BTC、country\_code=US）で取れる（[source](https://sosovalue.gitbook.io/soso-value-api-doc/2.-etf/summary-history.md)）。代替のTFTCは同じSoSoValueのデータを無料で転載するJSON（`https://www.tftc.io/bitcoin-etf-flows/latest.json`）だが、10月30日の確認では10月28日22:00（UTC）から更新が止まっていた（[source](https://www.tftc.io/bitcoin-etf-flows/methodology)）。

SoSoValueの予想値は `GET /macro/events/{event}/history` で指標ごとの実績・予想・前回を取り、実績が空の行を次回分とみなす（[source](https://sosovalue.gitbook.io/soso-value-api-doc/8.-macro/event-history.md)）。指標名は事前確認で確かめた名前を config.py に固定で持つ。名前を間違えてもエラーにならず、空の結果が返る。FOMCの利下げ確率は `GET /macro/fomc-probabilities` で、FedWatchと同じ種類のデータ（[source](https://sosovalue.gitbook.io/soso-value-api-doc/8.-macro/fomc-probabilities.md)）。発表日程は FRED の `GET /fred/release/dates` で取る。標準では先の予定日が除かれるため、include\_release\_dates\_with\_no\_data=true を付けて取り、実装時に先の日付が入るか確かめる（[source](https://fred.stlouisfed.org/docs/api/fred/release_dates.html)）。事前確認の詳細は docs/sosovalue-check.md にある。

## 直す不具合

直す不具合は6件で、うち上の2件は相場判断に直接響く。SECの重複通知など2件は、SEC監視の撤廃でなくなる。いずれもコードと、state.jsonの変更履歴（2026年5月24日〜10月29日）で確かめた。

| 不具合 | 起きていること | 場所 | 直し方 |
| --- | --- | --- | --- |
| FOMC議事録が届かない | 運用中に3回公表されたが通知ゼロ。届いたのは声明5回だけ | fetchers/fomc.py | 議事録のURLは会合日付で、同じ日付の声明と同着になり声明が選ばれる。声明と議事録を別々に記録する |
| FRBバランスシートが6日遅れ | 水曜21:00（UTC）に取りに行くが、公表は木曜16:30（米東部）（[source](https://www.federalreserve.gov/Releases/H41/)） | scheduler.yml | 毎日の朝のまとめで取り、金曜朝に拾う |
| AIの失敗が「中立」として届く | 故障と本当の中立を見分けられない | summarizer.py | Claude撤廃で解消。議事録要約が失敗したら「判定不可」と表示する |
| 取引所純流入が etf\_flow という名前で保存されている | 現物ETFフローを足すと名前が衝突する | fetchers/coinmetrics.py、detector.py | exchange\_netflow に改名し、state.jsonを移行する |
| FREDの対象月を発表日として表示している | 9月分のCPIが「2026-09-01」と表示される | summarizer.py、notifier.py | 「〇月分」と発表時刻を分けて表示する |
| 取得エラーが毎回鳴る | 失敗が続くと毎時同じエラー通知が届く | main.py | 失敗の1回目と復旧時だけ通知し、続いている間は朝のまとめに載せる |

## 検証

すべての通知を1行ずつ記録し、通知時点と1時間後・24時間後・72時間後のBTC価格を自動で書き足す。毎月1日に指標ごとの当たり率をまとめ、効かない指標を減らす判断材料にする。

記録する項目（logs/alerts.jsonl、1通知1行）

- 日時、指標、種類（即時通知か朝のまとめか）、実績値、比べた相手（予想値または前回通知値）、判定
- 通知時点のBTC価格と、1時間後・24時間後・72時間後の価格。OKXの1時間足から、時間が来たものを毎時のジョブで埋める

月次レポート（Discordと reports/YYYY-MM.md）

- 指標ごとの通知件数
- 当たり率：強気のあとにBTCが上がり、弱気のあとに下がった割合（24時間後・72時間後）。中立は除く
- 動きの大きさ：通知後のBTCの値動き幅の平均を、全期間の平均と比べた倍率
- 判定と逆に動くことが多い指標を、config.pyの向きを見直す候補として挙げる

過去5か月分の再構成

- state.jsonの変更履歴から、2026年5月24日以降の約2,000件の通知の日時と指標を復元できる（今回の分析で確認済み）
- 当時の判定はClaude任せで記録がないため、まずは「どの指標の通知のあとにBTCが大きく動いたか」だけを測る

## 実装の進め方

事前確認とPhase Aは済んだ。残りのPhase BとCは1回にまとめ、Phase Aを1週間ほど動かして様子を見てから入れる。通知の記録はさかのぼって取れないため、Cを後回しにせずBと一緒に始める。プロンプトを貼る前に、この設計書をMarkdownで書き出し、リポジトリに docs/redesign.md として置いておく。

1. 事前確認（2026年11月4日完了。結果は docs/sosovalue-check.md）：SoSoValueのマクロ系APIを一通り呼び、使えるか、どの指標がどの名前で載っているか、発表前に予想値が入っているかを確かめた。残る確認は、CPIとコアPCEの予想値が発表の何日前に載るか
2. Phase A 整理と不具合修正（2026年11月4日に配備。コミット 8cb19a9。報告は docs/phase-a-report.md）：Claude撤廃（議事録以外）、SEC監視の撤廃、不具合6件、現物BTC ETFの資金流出入の追加、日次フローの即時送信、即時通知と朝のまとめへの振り分け、予想値がない指標の判定、ジョブ構成の変更。予想値がある指標は、Phase Bまでは前回値比で判定する。確認：11月11日ごろまでの1週間、朝のまとめが毎日届き、即時通知が1日1件未満に収まること
3. 外部タイマーへの切り替え（2026年11月12日に配備。コミット 158cfde。報告は docs/trigger-report.md）：GitHub の定時実行が1日約5回に間引かれ、朝のまとめが3〜5時間遅れたため、cron-job.org から workflow\_dispatch で起動する形にした。朝のまとめの二重送信防止、前年比の基準月が欠測のときの表示も入れた。確認：11月13日の朝のまとめが 7:47 に1通だけ届いた
4. Phase B+C 予想値・金利見通し・発表待ち受けと、通知の記録：発表日程の取得（FRED）、予想値の取得（SoSoValueとForexFactoryの2つ）、予想値がある指標の判定（2つの予想を範囲として比べる。コアPCEを含む）、FOMCの利下げ確率（朝のまとめ・即時通知・声明の判定）、朝のまとめへの予定の追加、発表待ち受けジョブ、BLS公式API、SoSoValueの残り回数の監視。これに通知ログとBTC価格の書き足し、月次レポートの仕組み、過去5か月分の再構成を加える。確認：次のCPIか雇用統計で、予想比つきの通知が発表から数分で届き、通知ログに1件ずつ記録されること
5. 最初の月次レポート：記録を始めた翌月の1日に最初の1通が届く。丸々1か月分の記録で集計したレポートは、その翌月から

新しいジョブ構成（時刻はUTC）

| ジョブ | 実行 | 中身 |
| --- | --- | --- |
| 朝のまとめ | 毎日 22:47（日本時間 7:47） | 予想値と予定の更新、全指標の取得、まとめの通知 |
| 毎時の監視 | 毎時 23分 | VIX急騰、FOMCの利下げ確率の変化、FOMC（臨時の発表を含む）、日次フロー（ETF・取引所純流入）の新着の送信、検証用の価格の書き足し |
| 発表待ち受け | 毎時の監視に組み込む（その1時間内に発表があるとき） | 当日の発表があれば時刻まで待ち、30秒間隔で最長45分取りに行く |
| 月次レポート | 毎月1日 0:17 | 当たり率の集計と通知 |

GitHubの定時実行は毎時0分付近が混み、遅れたり実行されなかったりする（[source](https://docs.github.com/en/enterprise-server@3.15/actions/how-tos/monitoring-and-troubleshooting-workflows/troubleshooting-workflows)）。実績でも1日に約5回しか動かず、数時間遅れたため、2026年11月12日から外部タイマー（cron-job.org）が workflow\_dispatch で起動する形に変えた。表の時刻は予備の定時実行のもの。公開リポジトリなので、待ち受けで待っている時間も無料。

事前確認のプロンプト（設計書なしでそのまま使える）

```
SoSoValue API の事前確認をしてください。既存のコードは変更せず、結果を docs/sosovalue-check.md にまとめるだけです。

準備
- APIキーは .env の SOSOVALUE_API_KEY に入れてある。キーをコード、ログ、まとめのファイルに書かないこと
- ベースURLと認証ヘッダーは公式ドキュメントで確認する：https://sosovalue.gitbook.io/soso-value-api-doc/authentication.md

確認すること
1. GET /macro/events：返ってくる指標名をすべて書き出し、次の指標がどの名前で載っているかを表にする。CPI 前月比・前年比、コアCPI 前月比・前年比、雇用統計（NFP）、失業率、新規失業保険申請、コアPCE 前月比。載っていないものは「なし」と書く
2. GET /macro/events/{event}/history：上の各指標について直近5件を取り、actual・forecast・previous の形式（単位の付き方）を記録する。まだ発表されていない回（actual が空の行）に forecast が入っているかも確かめる
3. 2 の forecast を、ForexFactory 週次カレンダー（https://nfs.faireconomy.media/ff_calendar_thisweek.json）の同じ指標と見比べる（今週分に載っていれば）
4. GET /macro/fomc-probabilities：as_of、rate_ranges、次回会合の日付と確率を記録する。時間をおいて2回呼べるなら、as_of が変わるかも確かめる
5. GET /etfs/summary-history（symbol=BTC、country_code=US）：最新の日付と値
6. 各エンドポイントでエラーや制限（デモプランで使えないなど）が出たら、その内容

まとめ方
- 指標ごとに「SoSoValueで取れる／取れない」を一覧にし、取れない指標は ForexFactory で代わりになるかを書く
- 指標名を間違えるとエラーにならず空の結果が返るので、空だった場合はその旨を明記する
```

Phase A のプロンプト

```
docs/redesign.md（改修設計書）を読んで、Phase A を実装してください。

対象
- Claude APIの撤廃。FOMC議事録の要約だけ残す（モデルは claude-sonnet-5-5、失敗時は「⚪ 判定不可」と表示）
- SEC監視の撤廃（ジョブ、取得処理、state.json の該当部分を削除）
- 「直す不具合」の章の6件すべて
- 現物BTC ETFの資金流出入の追加。SoSoValue 公式API（GET /etfs/summary-history、symbol=BTC、country_code=US、キーは Secrets の SOSOVALUE_API_KEY）を使い、キー未設定や失敗のときは TFTC の latest.json で代替する（TFTC を使った通知には出典を入れる）。最初に実際に取得して項目を確認し、異常値の判定には過去90取引日を使う
- ETF と取引所純流入は朝のまとめを待たず、毎時の監視で新しい日のデータを見つけたらすぐ送る（日次フロー）
- 「通知の設計」の章の振り分け（即時通知と朝のまとめ）
- 「判定ルール」の章のうち、予想値がない指標のルール
- 「実装の進め方」の章のジョブ構成（朝のまとめと毎時の監視）
- 予想値がある指標は、Phase B までは前回値との比較で判定し、「予想値なし」と表示する

進め方
- 実装の前に、変更するファイルと state.json の移行方法を一覧にして確認を取ること
- 判定の向きと閾値は config.py に置くこと
- state.json の既存データは消さずに移行すること
- 各ジョブを手動でも実行できるように残し、README を更新すること
```

Phase B+C のプロンプト

```
docs/redesign.md の Phase B と Phase C をまとめて実装してください。docs/sosovalue-check.md（事前確認の結果）と docs/phase-a-report.md（Phase A の報告）と docs/trigger-report.md（外部タイマー対応の報告）も読むこと。最初に git pull で最新にすること。

Phase B の対象
- 発表日程の取得（FRED の GET /fred/release/dates、include_release_dates_with_no_data=true）。時刻は config.py に固定で置き、各発表の release_id は最初に確認すること
- 予想値の取得（SoSoValue の GET /macro/events/{event}/history と、ForexFactory 週次カレンダー https://nfs.faireconomy.media/ff_calendar_thisweek.json の両方）。SoSoValue の指標名は docs/sosovalue-check.md の名前を config.py に固定で持ち、予定一覧（/macro/events）は使わない
- 「判定ルール」の章の、予想値がある指標のルール（2つの予想を範囲として比べる。コアPCEを含む）
- FOMC の利下げ確率（GET /macro/fomc-probabilities）。朝のまとめへの掲載、前回通知から15ポイント以上動いたときの即時通知、FOMC 声明の判定（会合前の期待金利との差）
- 朝のまとめへの「今日・明日の予定と予想値」と「SoSoValue の今月の残り回数」の追加
- 発表待ち受け。別のジョブは作らず、毎時の監視に組み込む。毎時の監視が起動したとき、その後60分以内に発表があれば、発表時刻まで待ち、発表時刻から30秒間隔で取得する（最長45分）。cron-job.org の予定は増やさない。夏時間と冬時間の両方で、米国東部 8:30・10:00 の発表と FOMC 声明（14:00）を拾えることをテストで確かめること。ワークフローの timeout-minutes を待ち受けに足りる長さにし、待ち受け中に次の毎時の起動が来ても、朝のまとめが取り消されないことを確かめること。発表が出なかったときや取れなかったときは、次の回の毎時の監視で拾い、二重に通知しないこと
- CPI と雇用統計の実績は BLS 公式API v2 から取り、取れなければ FRED で代替する（.env と Secrets の BLS_API_KEY は登録済み。最初にキーが使えるか確かめること）。新規失業保険申請とコアPCEは BLS にないため、待ち受けでは SoSoValue の実績を取りに行き、出なければ FRED で拾う。指標ごとに、発表時刻から実績が取れるまでの時間を記録すること

Phase C の対象
- logs/alerts.jsonl への全通知の記録と、1時間後・24時間後・72時間後の BTC 価格（OKX の1時間足）の書き足し。価格が取れなかった行は空欄のまま残し、次の実行で再試行する
- 毎月1日の月次レポート（Discord と reports/YYYY-MM.md）
- state.json の git 履歴から、2026年5月24日以降の通知の日時と指標を復元するスクリプトと、その期間の BTC 値動きの集計（結果は reports/ に保存）
- logs/ と reports/ もワークフローでコミットされるようにする

注意
- CPI はコアの前月比・前年比と総合の前月比・前年比を載せ、判定はコア前月比で行うこと
- 前年比は季節調整なし、前月比は季節調整ありの系列で計算し、小数1桁に丸めて比べること
- SoSoValue の値は文字列で、CPI 系は "0.3%"（"0%" や負の値もある）、NFP と新規失業保険申請は千単位の数字だけ。ForexFactory は "65K" の形なので、単位をそろえてから比べること
- SoSoValue の次回分は実績が空（""）の行とする。履歴が1件もなければ指標名の誤りとしてエラー、次回の行がまだないだけならエラーにせず ForexFactory だけで判定すること
- SoSoValue の上限は月1万回。応答ヘッダー x-ratelimit-remaining-month を記録し、2割を切ったら朝のまとめで警告すること。月の呼び出し回数の見積もりを README に書くこと
- ForexFactory の取得は1日数回までにすること（非公式のため、取りすぎると遮断されうる）
- 取得失敗はエラー通知すること（1回目と復旧時のみ）
- 過去の発表データで判定のテストを書くこと（2つの予想が一致する場合と割れる場合の両方）
- ローカルの state.json は書き換えず、コミットにも含めないこと

進め方
- 実装の前に、変更するファイルと state.json の移行方法の一覧を docs/phase-bc-plan.md に書き出して止まること
- 実装とテストが終わったら、報告を docs/phase-bc-report.md に書き出し、コミットとプッシュの前で止まること
```

## 決めたことと残るリスク

未決だった点は2026年10月30日・11月3日・11月4日・11月12日・11月13日に決めた。運用で残るリスクは7点ある。

- CPI：コアCPIは前月比・前年比の両方を載せる。判定はコア前月比で行い、前年比が逆なら添える
- コアPCE：即時通知に加える
- SEC：監視をいったんすべて撤廃する
- ETF：現物BTC ETFの資金流出入を加える。取得先はSoSoValue公式APIで、TFTCは代替
- 日次フロー：ETFと取引所純流入は、新しい日のデータが出たらすぐ送る
- FOMCの金利見通し：利下げ確率を朝のまとめと即時通知に載せ、FOMC声明は市場の織り込みと比べて判定する（11月3日）
- 予想値：SoSoValueとForexFactoryの両方から取り、2つの予想を範囲として比べる。失業率はForexFactoryのみ（11月4日）
- 発表日程：SoSoValueの予定一覧は使わず、FREDの公式の発表日程から取る（11月4日）
- SoSoValueの空の結果：履歴が1件もなければエラー、次回の行がまだないだけならForexFactoryだけで判定する（11月4日）
- SoSoValueの上限：実測は月1万回。残り回数を朝のまとめに載せ、2割を切ったら警告する（11月4日）
- 進め方：Phase Aを1週間ほど様子見し、Phase BとCは1回にまとめて入れる。CPIに間に合わせるために急がない（11月4日）
- 起動：cron-job.org から workflow\_dispatch で起動し、GitHub の定時実行は予備にする（11月12日）。発表待ち受けは別のジョブにせず毎時の監視に組み込み、その1時間内に発表があれば待って、発表時刻から30秒間隔で取りに行く。cron-job.org の予定は増やさない。毎時の監視の実行時刻を発表に合わせる案は、待ち受けの方が速いため採らない（11月13日）

リスク

- 予想値はSoSoValueとForexFactoryのどちらも、指標名や形式が変わりうる。両方取れないときはエラー通知を出し、前回値比の判定に切り替えて「予想値なし」と明記する
- SoSoValueへの依存が大きい（予想値・利下げ確率・ETF）。無料のデモプランで、上限（実測で1分100回・月1万回。資料の記載と違った）や条件が変わりうる。止まったときは予想値をForexFactoryだけで判定し、ETFはTFTCで代替し、利下げ確率は朝のまとめに取得失敗と出す。TFTCは更新が1日以上止まることがある
- CPIとコアPCEの予想値が、発表の何日前にSoSoValueに載るかは未確認。次のCPIの数日前から確かめる。それまでは、前日の朝のまとめに予想値が片方しか出ないことがある
- 利下げ確率の更新はほぼ1日1回。CPIなどの直後に織り込みが動いても、通知は次の更新まで遅れることがある
- yfinanceも非公式で、取得が止まることがある。止まった指標は朝のまとめの稼働状況に出る
- ルール判定は予想との差の向きしか見ない。改定値や内訳、FOMCの声明文や会見の中身は判定に入らないため、大きく外れた回は自分で確かめる
- 起動は外部タイマー（cron-job.org）に頼る。cron-job.org が止まるか、トークンの期限（1年）が切れると、GitHub の定時実行だけになり数時間遅れうる。期限の前にトークンを作り直す

## 出典

- [Federal Reserve: H.4.1 Factors Affecting Reserve Balances](https://www.federalreserve.gov/Releases/H41/)
- [GitHub Docs: Troubleshooting workflows](https://docs.github.com/en/enterprise-server@3.15/actions/how-tos/monitoring-and-troubleshooting-workflows/troubleshooting-workflows)
- [MQL5 Forum: Economic Calendar Api Recommendation?](https://www.mql5.com/en/forum/343908/16870225)
- [FRED API: fred/release/dates](https://fred.stlouisfed.org/docs/api/fred/release_dates.html)
- [SoSoValue API: Setting Up Your API Key](https://sosovalue.gitbook.io/soso-value-api-doc/setting-up-your-api-key.md)
- [SoSoValue API: ETF Summary History](https://sosovalue.gitbook.io/soso-value-api-doc/2.-etf/summary-history.md)
- [SoSoValue API: Macroeconomic Events](https://sosovalue.gitbook.io/soso-value-api-doc/8.-macro/events.md)
- [SoSoValue API: Macro Event History](https://sosovalue.gitbook.io/soso-value-api-doc/8.-macro/event-history.md)
- [SoSoValue API: FOMC Target Rate Probabilities](https://sosovalue.gitbook.io/soso-value-api-doc/8.-macro/fomc-probabilities.md)
- [SoSoValue API: Rate Limit](https://sosovalue.gitbook.io/soso-value-api-doc/rate-limit.md)
- [TFTC: Bitcoin ETF Flows Methodology](https://www.tftc.io/bitcoin-etf-flows/methodology)

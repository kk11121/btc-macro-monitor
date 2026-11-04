# SoSoValue API 事前確認

確認日時：2026年11月4日 05:03〜05:13（UTC）ごろ

ベースURL：`https://openapi.sosovalue.com/openapi/v1`
認証ヘッダー：`x-soso-api-key`

## 結論

- 対象8指標のうち7指標は SoSoValue で実績・予想・前回が取れる。失業率だけは取れず、ForexFactory で代わりになる
- **CPI 系4指標とコアPCE は、発表前の予想値を今回確認できていない**。この時点で発表前の行がなかった（CPI 系）か、発表が近くなかった（コアPCE）ため
- `GET /macro/events` の一覧は対象指標の発見には使えない。NFP や新規失業保険申請が、発表日の直前でも載っていなかった
- NFP の予想は SoSoValue が 63、ForexFactory が 65K で一致しなかった

## 指標ごとの一覧

| 指標 | SoSoValueで | SoSoValue の指標名 | `/macro/events` の一覧に | 発表前の forecast | ForexFactory の名前（今週分） |
|---|---|---|---|---|---|
| CPI 前月比 | 取れる | `CPI (MoM)` | あり | 未確認（行がまだない） | 今週はなし |
| CPI 前年比 | 取れる | `CPI (YoY)` | あり | 未確認（行がまだない） | 今週はなし |
| コアCPI 前月比 | 取れる | `Core CPI (MoM)` | あり | 未確認（行がまだない） | 今週はなし |
| コアCPI 前年比 | 取れる | `Core CPI (YoY)` | **なし** | 未確認（行がまだない） | 今週はなし |
| 雇用統計（NFP） | 取れる | `Nonfarm Payrolls` | **なし** | あり（63） | Non-Farm Employment Change |
| 失業率 | **取れない** | なし | なし | － | Unemployment Rate |
| 新規失業保険申請 | 取れる | `Initial Jobless Claims` | **なし** | あり（197） | Unemployment Claims |
| コアPCE 前月比 | 取れる | `Core PCE Price Index (MoM)` | **なし** | 未確認（直近の発表は10月29日） | 今週はなし |

**失業率**：次の11通りはすべて空の結果（HTTP 200、0件）だった。公式ドキュメント全文にも失業率の指標名はない。

`Unemployment Rate`、`Unemployment Rate (MoM)`、`Unemployment rate`、`unemployment rate`、`UNEMPLOYMENT RATE`、`US Unemployment Rate`、`U.S. Unemployment Rate`、`Unemployment`、`Jobless Rate`、`Unemployment Rate (Monthly)`、`Unemployment Rate (Oct)`

ただし一覧に載らない指標がある以上、別の名前で存在する可能性は残る。正しい名前を確かめる手段がないので「なし」として扱う。

**ForexFactory で代わりになるか**：

- 失業率：なる。今週分に予想 4.2%、前回 4.2%（11月6日 08:30 ET）
- CPI 系とコアPCE：今週分に載っていないので未確認

## 1. GET /macro/events

HTTP 200。返ってきた指標名は次の8つがすべてで、5日分しかなかった。

| date | events |
|---|---|
| 2026-11-03 | S&P Global Services PMI、ISM Non-Manufacturing PMI |
| 2026-11-09 | CPI (MoM)、Core CPI (MoM)、CPI (YoY) |
| 2026-11-11 | Existing Home Sales |
| 2026-11-12 | PPI (MoM) |
| 2026-11-16 | Retail Sales (MoM) |

分かったこと：

- **載っていない指標がある**。`Nonfarm Payrolls`（11月6日）と `Initial Jobless Claims`（11月5日）は履歴に発表前の行があるのに、一覧にはない
- **日付が履歴と1日ずれる例がある**。Services PMI と ISM は一覧で11月3日、履歴で11月4日（ForexFactory も11月4日）。CPI の11月9日も同じようにずれている可能性があるが未確認
- `start_date`・`end_date`、`date`、`limit` を付けても結果は変わらなかった

## 2. GET /macro/events/{event}/history

指標名は大文字と小文字を区別しない（`nonfarm payrolls` でも取れた）。`limit` は効いた。

空の結果だった名前（すべて HTTP 200、0件）：

- `Non-Farm Payrolls`、`Non Farm Payrolls`、`NFP`
- `Jobless Claims`、`Initial Claims`、`Unemployment Claims`
- `Core PCE (MoM)`、`Core PCE (YoY)`、`Core PCE Price Index`、`PCE (MoM)`、`PCE Price Index (MoM)`
- `CPI`
- 失業率の11通り（上記）

### CPI (MoM)

| date | actual | forecast | previous |
|---|---|---|---|
| 2026-10-14 | 0.6% | 0.6% | 0.4% |
| 2026-09-11 | 0.4% | 0.4% | 0.1% |
| 2026-08-12 | 0.1% | 0.1% | -0.4% |
| 2026-07-14 | -0.4% | -0.1% | 0.5% |
| 2026-06-10 | 0.5% | 0.5% | 0.6% |

### CPI (YoY)

| date | actual | forecast | previous |
|---|---|---|---|
| 2026-10-14 | 3.7% | 3.6% | 3.4% |
| 2026-09-11 | 3.4% | 3.4% | 3.4% |
| 2026-08-12 | 3.4% | 3.4% | 3.5% |
| 2026-07-14 | 3.5% | 3.8% | 4.2% |
| 2026-06-10 | 4.2% | 4.2% | 3.8% |

### Core CPI (MoM)

| date | actual | forecast | previous |
|---|---|---|---|
| 2026-10-14 | 0.3% | 0.2% | 0.3% |
| 2026-09-11 | 0.3% | 0.2% | 0.2% |
| 2026-08-12 | 0.2% | 0.2% | 0% |
| 2026-07-14 | 0% | 0.2% | 0.2% |
| 2026-06-10 | 0.2% | 0.3% | 0.4% |

### Core CPI (YoY)

| date | actual | forecast | previous |
|---|---|---|---|
| 2026-10-14 | 2.6% | 2.5% | 2.4% |
| 2026-09-11 | 2.4% | 2.4% | 2.5% |
| 2026-08-12 | 2.5% | 2.5% | 2.6% |
| 2026-07-14 | 2.6% | 2.8% | 2.9% |
| 2026-06-10 | 2.9% | 2.9% | 2.8% |

### Nonfarm Payrolls

| date | actual | forecast | previous |
|---|---|---|---|
| 2026-11-06 | （空） | 63 | 29 |
| 2026-10-02 | 29 | 89 | 162 |
| 2026-09-04 | 162 | 55 | -23 |
| 2026-08-07 | -23 | 85 | 57 |
| 2026-07-02 | 57 | 114 | 172 |

### Initial Jobless Claims

| date | actual | forecast | previous |
|---|---|---|---|
| 2026-11-05 | （空） | 197 | 194 |
| 2026-10-29 | 194 | 198 | 194 |
| 2026-10-22 | 194 | 200 | 199 |
| 2026-10-15 | 199 | 199 | 197 |
| 2026-10-08 | 197 | 200 | 197 |

### Core PCE Price Index (MoM)

| date | actual | forecast | previous |
|---|---|---|---|
| 2026-10-29 | 0.3% | 0.3% | 0.2% |
| 2026-09-30 | 0.2% | 0.3% | 0.2% |
| 2026-08-26 | 0.2% | 0.2% | 0.1% |
| 2026-07-30 | 0.1% | 0.2% | 0.3% |
| 2026-06-25 | 0.3% | 0.3% | 0.2% |

### 値の形式

| 指標 | 形式 | 例 |
|---|---|---|
| CPI 系、コアPCE | 文字列、`%` 付き | `"0.6%"`、`"-0.4%"`、`"0%"`（整数のときは小数点なし） |
| NFP | 文字列、単位なし（千人） | `"63"`、`"-23"` |
| 新規失業保険申請 | 文字列、単位なし（千件） | `"197"` |

### 発表前の行

actual が空の行は `""` で返り、確認できた行にはすべて forecast と previous が入っていた。

| 指標 | 発表日 | 確認時点での残り | 発表前の行 |
|---|---|---|---|
| S&P Global Services PMI | 11月4日 | 当日 | あり（予想 57.9） |
| ISM Non-Manufacturing PMI | 11月4日 | 当日 | あり（予想 54.8） |
| Initial Jobless Claims | 11月5日 | 1日前 | あり（予想 197） |
| Nonfarm Payrolls | 11月6日 | 2日前 | あり（予想 63） |
| CPI 系 | 一覧では11月9日 | 5日前 | **なし** |

`CPI (MoM)` を `start_date=2026-11-01`、`end_date=2026-12-31` で取っても空だった。行が何日前に現れるかは未確認で、発表の数日前に現れるのか、CPI には現れないのかはまだ分からない。

## 3. ForexFactory との比較

SoSoValue と同じ時間帯（11月4日 05:05 UTC ごろ）に取得した今週分で比べた。

| 指標 | 発表 | SoSoValue 予想 | ForexFactory 予想 | SoSoValue 前回 | ForexFactory 前回 | 一致 |
|---|---|---|---|---|---|---|
| NFP | 11月6日 | 63 | 65K | 29 | 29K | **予想が不一致** |
| 新規失業保険申請 | 11月5日 | 197 | 197K | 194 | 194K | 一致 |
| ISM 非製造業（対象外、参考） | 11月4日 | 54.8 | 54.8 | 54.9 | 54.9 | 一致 |
| S&P サービス業PMI（対象外、参考） | 11月4日 | 57.9 | 57.9 | 58.8 | 57.9 | **前回が不一致** |

- NFP の差の理由は不明。どちらの予想を使うかで判定が変わりうる
- S&P サービス業PMI の前回の違いは、ForexFactory が速報値を前回にしている可能性があるが未確認
- CPI 系、コアPCE、失業率は比べられなかった。CPI 系とコアPCE は今週の ForexFactory になく、失業率は SoSoValue にない

### ForexFactory の取得先

**`docs/redesign.md` の URL は使えない。** `cdn-nfs.faireconomy.media` は名前解決できず（Non-existent domain）、`nfs.faireconomy.media` なら HTTP 200 で取れた。来週分の `ff_calendar_nextweek.json` は 404 だった。

## 4. GET /macro/fomc-probabilities

HTTP 200。

- as_of：`1793768694563`（2026-11-04 05:04:54 UTC）
- rate_ranges：`350-375`、`375-400`、`400-425`、`425-450`、`450-475`、`475-500`、`500-525`、`525-550`（8個）
- meetings：9回分（2026-12-09 〜 2027-12-08）
- 次回会合：**2026-12-09**

| rate_range | 確率 |
|---|---|
| 350-375 | 0.0% |
| 375-400 | 20.9% |
| 400-425 | 79.1% |
| 425-450〜500-525 | 0.0% |
| 525-550 | null |

**as_of の変化**：約8分あけて2回呼び、as_of も確率も変わった。

| | 呼んだ時刻（UTC） | as_of（UTC） | 次回会合 375-400 | 次回会合 400-425 |
|---|---|---|---|---|
| 1回目 | 05:04:54 | 05:04:54（`1793768694563`） | 20.9% | 79.1% |
| 2回目 | 05:13:09 | 05:09:54（`1793768994681`） | 26.8% | 73.2% |

- as_of の差はほぼ5分（300秒）で、ドキュメントの「キャッシュ5分」と合う。as_of はキャッシュを作った時刻で、確率が更新された時刻とは限らない
- 確率は9回すべての会合で変わった。ドキュメントの「更新はおよそ1日1回」より頻繁に動く可能性がある
- 比べたのは1組だけなので、どのくらいの頻度で変わるかは未確認

## 5. GET /etfs/summary-history（symbol=BTC、country_code=US）

HTTP 200。最新は **2026-11-03**。

| date | total_net_inflow（米ドル） | total_value_traded | total_net_assets | cum_net_inflow |
|---|---|---|---|---|
| 2026-11-03 | -115,563,333.62 | 1,460,343,755.80 | 99,157,322,791.53 | 56,466,092,983.59 |
| 2026-11-02 | -398,617,353.40 | 1,991,815,926.50 | 98,124,458,163.03 | 56,581,656,317.21 |
| 2026-10-30 | -643,330,345.89 | 6,350,689,995.00 | 98,139,977,651.38 | 56,980,273,670.61 |

値は文字列ではなく JSON の数値で返る。

## 6. エラーと制限

- **再設定後のキーでは、デモプランで使えないエンドポイントはなかった**。対象の4つはすべて HTTP 200 で、エラーも出なかった
- 最初に入っていたキーは全エンドポイントで 401 `400101` になり、入れ直して解消した
- 指標名の誤りはエラーにならず、HTTP 200、`code: 0`、`data: []` が返る

### ドキュメントと実際の違い

| 項目 | ドキュメント | 実際 |
|---|---|---|
| 上限（分） | 20回 | 100回（`x-ratelimit-limit-minute`） |
| 上限（月） | 10万回 | **1万回**（`x-ratelimit-limit-month`） |
| 制限のヘッダー名 | `X-RateLimit-Limit` など | `x-ratelimit-limit-minute`、`-month`、`x-ratelimit-remaining-minute`、`-month`、`x-ratelimit-reset-minute`、`-month` |
| レスポンスの形 | 例では `data` の中身のみ | `{code, message, data, details}` で包まれ、中身は `data` |
| FOMC の会合数 | 約11回 | 9回 |
| FOMC の更新頻度 | およそ1日1回 | 約8分の間に全会合の確率が変化（1組のみの観測） |

`docs/redesign.md` は月10万回を前提にしているので、月1万回で足りるかを Phase B で見積もる必要がある。

## Phase B に向けて

- 指標名は `/macro/events` から探さず、上の表の名前を固定で持つ
- 空の結果は「名前の誤り」と「まだ行がない」を区別できない。CPI 系などは発表の数日前まで空の可能性があるので、空を即エラーとして扱う `docs/redesign.md` の方針は見直しが必要
- 発表日は `/macro/events` の日付に頼らない
- NFP は SoSoValue が千人単位の数字のみ、ForexFactory が `65K` なので、同じ単位に直して比べる
- `"0%"` のように小数点がない値、負の値、actual の `""` を扱う
- FOMC の確率は短時間で数ポイント動くことがあるので、`docs/redesign.md` の「更新はほぼ1日1回」を前提にした通知の条件は見直す。as_of はデータの鮮度の判定に使わない
- `docs/redesign.md` の ForexFactory の URL を `nfs.faireconomy.media` に直す（今回は変更していない）

## 残っている確認

1. CPI の発表前の行が現れるかと、その forecast。次回の CPI の数日前から当日に、`CPI (MoM)` などの履歴を見る
2. コアPCE の次回の発表前に、同じことを確かめる
3. CPI の発表週とコアPCE の発表週に、ForexFactory の名前と予想値を確かめる
4. NFP の予想のどちらを使うか決める

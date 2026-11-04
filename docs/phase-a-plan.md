# Phase A 実装前の確認一覧

作成日：2026年11月4日

設計書：`docs/redesign.md`
状態：**確認待ち。実装は未着手で、既存のコードと state.json は変更していない**

## 変更するファイル

| ファイル | 変更内容 |
|---|---|
| `config.py` | 判定の向き、VIX（+10%かつ20超）、「同じ」の幅、ETF・純流入の90日と±2σ、Funding ±0.03% を置く。使わなくなる閾値は消す |
| `state_store.py`（新規） | `detector.py` から読み書きと移行処理を分ける |
| `rules.py`（新規） | 判定の純粋関数（前回値比、流入の向き、±2σ、VIX） |
| `jobs_morning.py`（新規） | 朝のまとめ |
| `jobs_hourly.py`（新規） | 毎時の監視（VIX、FOMC、日次フロー、経済指標） |
| `main.py` | `--mode morning` / `--mode hourly` だけの入口に縮小する。取得確認用に `--dry-run`（送信も保存もしない）を追加 |
| `detector.py` | 削除（役割は上の2ファイルへ移す） |
| `summarizer.py` | 議事録の要約だけ残す。`claude-sonnet-5-5` を使い、失敗時は「⚪ 判定不可」 |
| `notifier.py` | 即時通知、日次フロー、朝のまとめ、エラー／復旧の形式に作り直す。Discord への送信失敗は例外にする（今は失敗しても状態が進み、通知が消える） |
| `fetchers/fomc.py` | 声明と議事録を別々に取る |
| `fetchers/fred.py` | `CPILFESL`・`CPILFENS`・`CPIAUCNS`・`PCEPILFE` を追加し、複数件を取れるようにする |
| `fetchers/coinmetrics.py` | キーを `exchange_netflow` に改名し、100日分を取る |
| `fetchers/etf.py`（新規） | SoSoValue を主、TFTC を代替として取得 |
| `fetchers/sec.py` | 削除 |
| `.github/workflows/scheduler.yml` | 朝のまとめ（22:47 UTC）と毎時の監視（毎時23分）の2ジョブにする。手動実行を残し、同時実行も防ぐ |
| `.env.example`、`README.md` | `SOSOVALUE_API_KEY` の追加と内容の更新 |
| `tests/`、`requirements-dev.txt`（新規） | pytest のテスト |

## state.json の移行

読み込み時に `schema_version: 2` へ自動で移行し、何度実行しても同じ結果になるようにする。切り替え中に旧ジョブが旧形式で書き戻しても、次の読み込みで移行される。

| 現在 | 移行後 |
|---|---|
| `cpi`・`nfp`・`unrate`・`icsa` | そのまま |
| `fomc` | `fomc.statement` に移す |
| （なし） | `fomc.minutes`。初回は記録だけして、過去の議事録は通知しない |
| （なし） | `core_pce`。初回は記録だけ |
| `glassnode.etf_flow` | `flows.exchange_netflow`（値はそのまま） |
| （なし） | `flows.spot_etf` |
| `market.vix` | そのまま（VIX 通知の基準）。朝のまとめの基準にも写す |
| `market` の残り、`stablecoin`、`glassnode.fear_greed`、`glassnode.funding_rate` | `summary.values` に移す |
| `liquidity` | そのまま |
| `sec` | 削除（指示どおり。git の履歴には残る） |
| （なし） | `errors`（失敗の継続状況）、`summary.last_date` |

state.json が壊れていたときに初期値で上書きする今の動作はやめて、エラーで止める。

### 配備の直後に起きること

- ETF：最新分が1回届く
- 朝のまとめ：初回の前回比は「前回通知した時点の値」と比べる
- FRB バランスシート：初回は7月22日分との比較になる。±0.5%未満だと保存されない今の仕様で、そこから更新が止まっているため

## 確認して分かったこと

- ETF の履歴：SoSoValue は `limit=300` でも22行（約1か月）しか返さず、90取引日分は取れない
- TFTC の全履歴：`data.json` に721日分あり、直近14日のうち SoSoValue にもある13日で値が完全に一致した
- 取引所純流入：CoinMetrics から100日分取れる

## 判断をお願いしたいこと

| # | 判断すること | 推奨 |
|---|---|---|
| 1 | ETF の異常値判定の基準 | TFTC の全履歴に SoSoValue の直近分を重ねて90取引日にする。この場合 ETF の通知すべてに TFTC の出典が入る。TFTC が取れない日は22取引日で計算し、その旨を表示する |
| 2 | 毎時の監視に経済指標の確認を残すか | Phase B まで残す。設計書のジョブ表にはないが、発表待ち受けは Phase B なので、外すと CPI などの即時通知が止まる |
| 3 | 「前回値比」の中身 | 下の表のとおり。CPI の指数は毎月上がるので、そのまま比べると常に弱気になる |
| 4 | Phase A の FOMC 声明の判定 | 本文から機械的に読み取り、利下げは強気、利上げは弱気、据え置きは中立、読み取れなければ「⚪ 判定不可」とする。「予想値なし」も表示する。市場の織り込みとの比較は Phase B で行う |
| 5 | VIX の基準値が下がる方向の扱い | 10%以上下がったら、通知せず基準だけ下げる。「前回通知値」のままだと、急騰の後に基準が高いまま残り、次の急騰を見逃す |
| 6 | この PC への Python の導入 | `winget` で Python 3.11 を入れる。今は未インストールでテストを実行できない。難しければ GitHub Actions でテストを動かす方法に切り替える |

判断3の比べ方：

| 指標 | 比べ方 |
|---|---|
| CPI | コア前月比を先月の前月比と比べる（前年比と総合も表示する） |
| NFP | 増減数を先月の増減数と比べる |
| コアPCE | 前月比を先月の前月比と比べる。Phase A で追加する |

## お願いしたい作業と、確認できない範囲

- GitHub の Secrets：`SOSOVALUE_API_KEY` の登録が必要。Secrets の操作はこちらではできない。未登録の間は TFTC で動く
- 議事録の要約：`.env` に `ANTHROPIC_API_KEY` がないので、ローカルでは実際に呼べずモックでのテストになる。実際に動くかは、本番で次の議事録が公表されたときに確かめる
- Discord：本番の Webhook にテスト送信はしない。確認は `--dry-run` で行う
- コミットとプッシュ：指示があるまで行わない

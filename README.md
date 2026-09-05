# 海事ニュース 見出し一覧

日本海事新聞、海事プレスONLINE、Splash247、TDSの公開見出し・公開抜粋を集め、ドライバルク向けの優先順位を付けた静的HTMLを生成します。記事本文や認証が必要なページは取得しません。

## 構成

| 配置 | 役割 |
| --- | --- |
| `src/maritime_headlines/sources/` | 媒体別の取得・解析。共通HTTP処理は `common.py` |
| `src/maritime_headlines/models.py` | URL正規化、記事識別子、日時、取得結果 |
| `src/maritime_headlines/storage.py` | 履歴移行、記事更新、保持期間、原子的ファイル保存 |
| `src/maritime_headlines/scoring.py` | 関心度と鮮度を分離した採点 |
| `src/maritime_headlines/config/scoring.json` | カテゴリ点数、同義語グループ、注目閾値、保持日数 |
| `src/maritime_headlines/render.py` | 表示対象を選びHTMLを生成 |
| `src/maritime_headlines/templates/`・`assets/` | HTML、CSS、JavaScript。出力HTMLには埋め込むため単体で開けます |
| `src/maritime_headlines/pipeline.py`・`cli.py` | 処理順序とコマンドライン |
| `tests/` | 日付、更新判定、採点、取得障害、媒体別解析の回帰テスト |
| `.github/workflows/ci.yml` | コード変更時の検証 |
| `.github/workflows/scrape.yml` | 定期取得・GitHub Pagesへの公開 |
| `scripts/persist_state.py` | 公開に成功した状態だけを `news-state` ブランチへ保存 |
| `data/legacy_seen_articles.json` | 初回移行用の旧履歴スナップショット。自動更新しません |

コードは `main`、運用中の記事状態は `news-state`、公開ファイルはPages用artifactに分離しています。`main` にニュース更新のコミットは追加しません。

## ローカル実行

Python 3.12以上を使用します。

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShellの場合: .venv\Scripts\Activate.ps1
python -m pip install -e .
maritime-headlines
```

リポジトリのルートで実行すると、旧履歴を初回だけ移行し、`data/state.json`、`data/run-status.json`、`dist/index.html`を生成します。従来の `python maritime_headlines.py` も実行できます。生成先は従来のルート直下から `dist/` に変わっています。

保存済みデータだけを確認する場合:

```bash
maritime-headlines --render-only --output .preview
```

このモードは通信・履歴更新を行わず、画面にプレビューと明記します。初回移行後は `--state` を指定して実際の運用状態を参照してください。

任意の履歴・設定を使用する場合:

```bash
maritime-headlines --state /path/to/state.json --output dist --config /path/to/scoring.json
```

## 記事と時刻の扱い

- 通常の記事は媒体と正規化URLで識別します。既知媒体のHTTP/HTTPS表記差、追跡用クエリ、フラグメントを整理します。
- TDSの固定URLは、日付・号数と記事種別で号ごとに分けます。それらが取得できない場合は見出し内容を識別に使います。この場合、軽微な見出し修正も別記事になることがあります。
- `published_date` は確認できた公開日です。不明なら `null` とし、取得日の代入はしません。TDSの日次市場見出し・更新表の月日だけの表記は、取得時点に最も近い年を補います。週次記事の本文中に登場する出来事の日付は公開日として使いません。
- `first_seen_at` は初めて検出した日時、`content_updated_at` は見出し・カテゴリの変化を確認した日時、`last_seen_at` は直近の取得で存在を確認した日時です。すべてタイムゾーン付きで保存します。
- 同じ記事の再取得だけでは、新着・更新扱いにしません。同じ号の見出し修正では初検出日時を維持します。
- 旧海事プレス・TDSデータの公開日は誤った補完を含むため、そのまま信用しません。TDSでは見出しから復元できる日付だけを使用します。移行前に上書きされて失われた内容は復元できません。
- 通常は直近7日分を表示します。公開日が不明な場合は内容更新日時を使います。取得元に残り続ける古い記事の識別情報は保持し、期限切れ後に再び新着になるのを防ぎます。取得元から消えた記事は最終確認から7日で履歴から削除します。

## 採点

「注目」の対象は**関心度だけ**で判定します。鮮度ボーナスは並び順にのみ加算します。画面の点数は関心度です。

同義語グループ内は一致した最大点を1回だけ加算します。たとえば「ケープサイズ Capesize」は30点の加算です。ASCII語には単語境界を使い、`demolition` の途中を `MOL` と誤認しません。

## 自動取得と公開

- 平日JST 07:00〜20:45、15分間隔の予定です。GitHub Actionsの混雑で遅延する場合があります。
- 手動実行はActionsの「海事ニュース取得・公開」から行えます。本番反映は `main` の実行に限定しています。
- 取得・公開は同時実行しません。新規実行で進行中の処理を中断しない設定です。
- **初回の切り替え時は、Settings → Pages → Build and deployment → Sourceを「GitHub Actions」にします。** 既存の公開URLは変わりません。
- 初回は `data/legacy_seen_articles.json` から読み込み、成功した公開の後に `news-state` ブランチを作成します。次回以降はそのブランチの `state.json` を読み込みます。
- コード更新 → 回帰テスト → 取得 → Pages公開 → `news-state`保存の順です。`GITHUB_TOKEN` のpushに依存したPages起動は使いません。
- `news-state`へのpushは通常のpushです。競合時に強制上書きしません。

## 取得に失敗した場合

一部媒体の失敗では、他媒体の取得を続行し、失敗媒体は期限内の保存記事と前回の成功日時を表示します。0件取得は取得異常として扱います。これは空のニュース一覧と解析処理の故障を区別できないための保守的な扱いです。

**全媒体が失敗した場合は非ゼロで終了し、前回の記事状態・公開ページを更新しません。** 個別の例外や代替取得経路の失敗はActionsログと `fetch-status-...` artifactに残します。画面には媒体別の最終成功日時があり、平日の取得時間帯に90分を超えて更新がない場合は注意表示が出ます。

確認する順番:

1. Actionsの「海事ニュース取得・公開」で失敗した工程を開く。
2. `Fetch headlines` が失敗した場合は `fetch-status-...` の媒体別エラー・件数・取得経路を確認する。
3. 特定媒体が0件なら、公開ページのHTML変更を確認し、該当する `sources/` の解析とテストを更新する。
4. `Deploy` で失敗した場合はPagesのSource設定・権限・Environment設定を確認する。
5. 履歴JSONが不正な場合は自動で空にせず停止する。`news-state` の直前の正常な `state.json` を復元して再実行する。

## 検証

```bash
python -m unittest discover -s tests -v
node --check src/maritime_headlines/assets/app.js
maritime-headlines --render-only --output .preview
```

通常のテストは通信を行いません。BeautifulSoupがない環境ではHTML解析テストをスキップしますが、CIでは `REQUIRE_PARSER_TESTS=1` を指定し、スキップを許しません。HTTP先の可用性や本番HTMLの変更は、定期取得のログで確認します。

変更を戻す場合はコード変更のコミットをrevertしてください。旧構成へ戻す際には、旧形式の `seen_articles.json` とPagesの公開方式も合わせて戻す必要があります。新形式の運用履歴は `news-state` に残ります。

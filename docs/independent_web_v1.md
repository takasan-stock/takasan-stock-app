# Takasan Stock Hunter 独立Web版 v1

## 目的
既存の `takasan-stock-app` をスキャンエンジンとして残し、画面を独立Webサイト化する。

## 構成
- `web/`: React + Vite フロント
- `web_api/`: FastAPI
- `early_rules.py`: Streamlit / Web API 共通のEARLY判定
- Google Sheets: 現行スキャン結果とウォッチリストを継続利用
- GitHub Actions: 現行スキャンを継続利用

## 初期5画面
1. TODAY COMMAND CENTER
2. EARLY HUNTER
3. WATCHLIST
4. RANKING
5. CHART

## 重要原則
判定ロジックの正はPython側。Reactは判定を再計算せず、APIが返した結果を表示する。

EARLY TOP20、理想本命、ウォッチ昇格状態は `early_rules.py` を共通利用するため、
Streamlit版と独立Web版の判定ズレを防ぐ。

## Render Blueprint
リポジトリ直下の `render.yaml` を使用する。

### API環境変数
- `SPREADSHEET_ID`
- `GCP_SERVICE_ACCOUNT_JSON`
  - GoogleサービスアカウントJSON全体をJSON文字列として設定
- `CORS_ORIGINS`
  - 独立Webの公開URLを設定

### Web環境変数
- `VITE_API_BASE`
  - APIサービスの公開URL
  - Viteのためビルド時に埋め込まれる。変更時はWebを再ビルドする

## v1 API
- GET /api/health
- GET /api/summary
- GET /api/early
  - 共通TOP20監査ロジックを通過した上位20
- GET /api/watchlist
- GET /api/watch-board
  - 共通ウォッチ昇格ロジック
- POST /api/watchlist
- PUT /api/watchlist/{ticker}/memo
- DELETE /api/watchlist/{ticker}
- GET /api/chart/{ticker}

## CI
`.github/workflows/web_v1_ci.yml`

PR時に以下を確認:
- Python構文
- 共通EARLYルールの簡易回帰テスト
- React/Vite production build

## 次段階
- 実戦検証ページ
- データ鮮度 / Operational Health
- Entry Hunter連携
- 認証
- モバイル最適化
- 独立リポジトリへの切り出し

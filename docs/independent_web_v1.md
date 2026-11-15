# Takasan Stock Hunter 独立Web版 v1

## 目的
既存の `takasan-stock-app` をスキャンエンジンとして残し、画面を独立Webサイト化する。

## 構成
- `web/`: React + Vite フロント
- `web_api/`: FastAPI
- Google Sheets: 現行スキャン結果とウォッチリストを継続利用
- GitHub Actions: 現行スキャンを継続利用

## 初期5画面
1. TODAY COMMAND CENTER
2. EARLY HUNTER
3. WATCHLIST
4. RANKING
5. CHART

## 重要原則
判定ロジックの正はPythonスキャン側。Webフロントは結果を読み、表示・操作するだけにする。

## Render環境変数
API:
- `SPREADSHEET_ID`
- `GCP_SERVICE_ACCOUNT_JSON` (サービスアカウントJSON全体を1行JSONで設定)
- `CORS_ORIGINS` (公開Web URL)

Web:
- `VITE_API_BASE` (APIサービスURL)

## v1 API
- GET /api/health
- GET /api/summary
- GET /api/early
- GET /api/watchlist
- POST /api/watchlist
- PUT /api/watchlist/{ticker}/memo
- DELETE /api/watchlist/{ticker}
- GET /api/chart/{ticker}

## 次段階
- EARLY TOP20のTOP20監査条件をAPI側へ共通化
- 実戦検証ページ
- データ鮮度/Operational Health
- Entry Hunter連携
- 認証
- モバイル最適化

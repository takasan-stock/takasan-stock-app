# Takasan Stock Hunter — ChatGPT Sites Migration

## Goal

Create a new ChatGPT Site that reproduces the current Takasan Stock Hunter frontend while keeping the existing Render API unchanged.

## Architecture

```
ChatGPT Sites
  -> https://takasan-stock-hunter-api-ue7i.onrender.com
     -> Google Sheets
     -> J-Quants
     -> Yahoo Finance fallback
```

## Source

GitHub repository: `takasan-stock/takasan-stock-app`
Frontend source: `web/`
API source: `web_api/`

The ChatGPT Site should reuse the current frontend behavior and styling, but must not deploy or modify the API.

## API base

```
https://takasan-stock-hunter-api-ue7i.onrender.com
```

Required endpoints:

- GET /api/health
- GET /api/summary
- GET /api/early
- GET /api/watch-board
- GET /api/stock-context/{ticker}
- GET /api/chart/{ticker}?period=2y
- POST /api/watchlist
- PUT /api/watchlist/{ticker}/memo
- DELETE /api/watchlist/{ticker}

## Pages to reproduce

1. TODAY
2. EARLY HUNTER
3. WATCHLIST
4. RANKING
5. CHART

## Critical behavior

### TODAY

- Keep TODAY ACTION QUEUE.
- CHART button must open the selected ticker.
- WATCHLIST state must remain synced with the API.

### CHART

- Default timeframe: daily.
- Timeframe choices: daily / weekly only.
- Daily moving averages: MA5 / MA21 / MA200.
- Weekly moving averages: MA5 / MA40.
- Keep candlesticks, volume, price axis, time axis, crosshair, zoom and scroll.
- Keep daily/weekly decision summary.
- Keep ENTRY CHECK.
- Distinguish:
  - pass = green
  - fail = red
  - unavailable = gray N/A
- Do not treat missing values as zero or as failed conditions.

### ENTRY CHECK

Daily:
- Close >= MA21
- MA21 >= MA200

Weekly:
- Close >= MA5
- MA5 >= MA40

Existing EARLY fields:
- 初動ランク
- 長期ステージ
- RR
- 損切り距離
- 反転
- 定着
- 形状

Do not replace the existing Entry Hunter logic. This panel is supplemental only.

## Visual design

Keep the current light theme:
- white / pale blue / mint
- soft borders
- readable dark navy text
- WATCHLIST gold filled star only when registered
- ideal candidates use pale green emphasis

## Migration constraints

Do not:
- change Google Sheets
- change Render API
- change J-Quants settings
- change Secrets
- change database schema
- change EARLY extraction rules
- change WATCHLIST persistence
- publish automatically

Build the Site privately first and review it before deployment.

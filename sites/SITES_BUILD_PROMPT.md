# Prompt for ChatGPT Sites

Use @Sites to create a new website named **Takasan Stock Hunter Sites v1**.

Use the GitHub repository `takasan-stock/takasan-stock-app` as the source reference. Reproduce the current frontend under `web/` as faithfully as possible.

Use the branch `sites-v1-migration` for migration notes and constraints. Use the current `main` branch implementation under `web/` as the UI behavior reference.

Do not modify the existing Render API or any existing production service.

Use this API as the backend:

```
https://takasan-stock-hunter-api-ue7i.onrender.com
```

Required pages:

- TODAY
- EARLY HUNTER
- WATCHLIST
- RANKING
- CHART

Preserve the current light visual design and current workflows.

CHART requirements:

- Default = 日足
- Timeframes = 日足 / 週足 only
- 日足 MA = 5 / 21 / 200
- 週足 MA = 5 / 40
- Candlesticks
- Volume
- Price axis
- Time axis
- Crosshair
- Zoom and scroll
- Daily/weekly decision summary
- ENTRY CHECK
- WATCHLIST previous / next navigation

ENTRY CHECK must distinguish:

- green = condition passed
- red = data exists but condition failed
- gray = source data unavailable / N/A

Never convert missing values to zero.

TODAY requirements:

- Preserve TODAY ACTION QUEUE
- CHART button must open the exact selected ticker
- WATCHLIST registration state must remain synchronized with the API

WATCHLIST requirements:

- gold filled star = registered
- outline star = not registered
- preserve memo saving
- preserve delete confirmation

Do not change:

- Google Sheets
- J-Quants
- Render API
- API Secrets
- existing database/storage
- EARLY screening logic
- Entry Hunter core logic
- WATCHLIST persistence

Create a private preview only.
Do not publish or deploy until I explicitly approve it.

After the preview builds, report:
1. which pages were reproduced
2. which API endpoints are connected
3. any feature that could not be reproduced exactly
4. whether WATCHLIST writes work
5. whether daily/weekly CHART switching works
6. whether ENTRY CHECK distinguishes pass/fail/N/A correctly


## Browser / preview safety instruction

Do not inspect, infer, copy, or navigate using the user's current browser URL.

Do not use browser history, address-bar inspection, or an inferred local/current URL to locate the Site.

Work only from:
- the ChatGPT Sites project context created in this task,
- the connected GitHub repository,
- and the explicit Render API base written above.

If opening the private preview requires a browser action that is blocked by a safety confirmation:
1. do not retry by guessing the URL,
2. do not navigate to an inferred URL,
3. leave the Site saved as a private draft,
4. report the exact blocked action and the exact UI step the user should click manually.

A browser safety block must not prevent completing code generation, API wiring, or saving the private Site draft.


## Version 3 retry / cold-start hardening

For the next Sites revision, read and apply:

```
sites/SITES_V3_RETRY_SPEC.md
```

This Version 3 change is Sites-only. Do not modify Render API or upstream data systems.


## Version 4 embedded preview fallback

If the embedded ChatGPT Sites editor/preview reports API connection failure while Render receives no upstream request, read and apply:

```
sites/SITES_V4_PREVIEW_FALLBACK_SPEC.md
```

This keeps Version 3 retry behavior and adds a GET-only direct Render fallback. Do not change Render API.


## 429 diagnostics

If Sites returns HTTP 429 before reaching Render, read and apply:

```
sites/SITES_429_DIAGNOSTICS_SPEC.md
```

Do not change retry policy for 429 until diagnostics identify the source.


## Version 6 Cloudflare 429 circuit breaker

If Version 5 diagnostics show Cloudflare 429 before Render application logs, read and apply:

```
sites/SITES_V6_429_COOLDOWN_SPEC.md
```

Do not add aggressive retries for 429. Use single-flight requests and a 60-second circuit breaker.


## Version 7 persistent last-known-good cache

If Cloudflare/Render edge 429 persists even with Version 6 circuit breaker, read and apply:

```
sites/SITES_V7_PERSISTENT_CACHE_SPEC.md
```

Keep Version 6 network behavior unchanged. Version 7 is a display-resilience layer only.


## Version 8 GitHub bootstrap fallback

If first-run local cache is empty and Cloudflare/Render 429 prevents live data, read and apply:

```
sites/SITES_V8_GITHUB_BOOTSTRAP_SPEC.md
```

The GitHub bootstrap is public market data only. Never publish WATCHLIST or memo data.


## Version 9 GitHub chart bootstrap

If Live chart API is unavailable and no local chart cache exists, read and apply:

```
sites/SITES_V9_CHART_BOOTSTRAP_SPEC.md
```

GitHub chart snapshots contain public J-Quants OHLCV only. Never include WATCHLIST or personal data.


## Version 10 snapshot-primary read/write split

After Version 9 proves GitHub Snapshot can serve TODAY/EARLY/RANKING/CHART during Render 429, read and apply:

```
sites/SITES_V10_READ_WRITE_SPLIT_SPEC.md
```

Version 10 makes Snapshot the normal public-market read path and reserves Render primarily for WATCHLIST/private writes.


## Version 10.1 stop automatic Render public reads

If Version 10 still generates Render requests for summary/early/chart during normal Snapshot use, read and apply:

```
sites/SITES_V10_1_STOP_AUTO_RENDER_READS_SPEC.md
```

Normal TODAY/EARLY/RANKING/CHART navigation must produce zero Render public-read requests.


## Version 10.2 WATCHLIST sync fix

If WATCHLIST同期 produces no Render health/watch request and leaves the UI at 前回同期, read and apply:

```
sites/SITES_V10_2_WATCHLIST_SYNC_FIX_SPEC.md
```

The explicit WATCHLIST sync must never silently no-op. Keep Version 10.1 public Render reads at zero.


## Version 13 no health preflight

If explicit WATCHLIST sync reaches an edge 429 on the health preflight before watch-board is attempted, read and apply:

```
sites/SITES_V13_NO_HEALTH_PREFLIGHT_SPEC.md
```

Normal user operations should call the target endpoint directly and use /api/health for diagnostics only.


## Version 14 Site Database WATCHLIST

After Version 13 confirms Render edge 429 still blocks the single WATCHLIST request, move private WATCHLIST state to the Site's built-in database:

```
sites/SITES_V14_SITES_DB_WATCHLIST_SPEC.md
```

Keep Snapshot as public-market source. Keep the Site private. Do not delete legacy Render/Sheets WATCHLIST data during migration.

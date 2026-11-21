# Takasan Stock Hunter Sites v1 — Version 10 Snapshot Primary / Render Write Path

## Goal

Make GitHub Snapshot the normal primary read path for public market data.

Use Render API mainly for private WATCHLIST state and write actions.

This reduces Render/Cloudflare request volume and avoids making the whole Site depend on Render availability.

## Architecture

### Public read path — primary

Use GitHub Snapshot by default for:

- summary
- EARLY
- RANKING
- TODAY public-market candidate data
- chart OHLCV
- chart MA calculations
- ENTRY CHECK chart-derived MA conditions

Snapshot sources:

```
https://raw.githubusercontent.com/takasan-stock/takasan-stock-app/sites-data/sites-bootstrap/meta.json
https://raw.githubusercontent.com/takasan-stock/takasan-stock-app/sites-data/sites-bootstrap/summary.json
https://raw.githubusercontent.com/takasan-stock/takasan-stock-app/sites-data/sites-bootstrap/early.json
https://raw.githubusercontent.com/takasan-stock/takasan-stock-app/sites-data/sites-bootstrap/charts/<Ticker>.json
```

### Private read/write path — Render

Use Render only for:

- WATCHLIST read
- WATCHLIST promotion board
- WATCHLIST add
- WATCHLIST delete
- WATCHLIST memo update
- any private/personal state that must not be published to GitHub

Do not publish WATCHLIST or memos to GitHub.

## 1. Startup sequence

On normal startup:

1. Restore Version 7 local cache, if available.
2. Fetch GitHub meta + summary + early once.
3. Render TODAY / EARLY / RANKING immediately from Snapshot.
4. Use local WATCHLIST cache if available.
5. Do NOT call Render /api/summary or /api/early during normal startup.
6. Do NOT call Render chart endpoints during normal startup.
7. Do NOT start Render health polling on normal startup.

This is the main change from Version 6-9.

## 2. Render health is on-demand only

A Render health sequence may start only when needed for private/live operations:

- user opens WATCHLIST and requests live sync
- user adds a ticker to WATCHLIST
- user deletes a ticker
- user saves a memo
- user explicitly presses a dedicated "WATCHLIST同期" or "Live同期" action

Do not start Render health merely because:
- page mounted
- route changed
- React re-rendered
- Snapshot loaded
- chart opened

Keep Version 6 single-flight and 429 circuit breaker for these on-demand Render sequences.

## 3. WATCHLIST local cache

If local WATCHLIST cache exists:

- show it immediately
- show a badge:
  `WATCHLIST: 前回同期`
- show last synced time
- use it for "登録済み" state and TODAY action decoration

Do not claim it is live.

If local cache does not exist:

- WATCHLIST status is unknown
- show `—`, not 0
- show `WATCHLISTは未同期です`
- do not guess registration state

## 4. WATCHLIST sync

Provide an explicit lightweight action:

```
WATCHLIST同期
```

When pressed:

1. if circuit breaker open, do not send network traffic; show remaining cooldown
2. otherwise run one single-flight health sequence
3. after health 200, fetch /api/watch-board and /api/watchlist if required by current UI
4. validate payloads
5. update local WATCHLIST cache only after successful 200
6. update registration badges
7. store last synced time

No background polling.

## 5. Writes

For add / delete / memo update:

- relay only
- no direct fallback
- no automatic retry
- no offline queue
- no optimistic "saved" state before server confirmation

Before write:
- if breaker open, block the action with a clear message
- if Render is unavailable, do not attempt repeated requests

After successful write:
- perform one explicit WATCHLIST refresh
- persist confirmed server state into local cache

## 6. TODAY behavior

TODAY public candidate ranking comes from GitHub Snapshot.

WATCHLIST decoration uses:
1. current live WATCHLIST state if synced this session
2. otherwise local WATCHLIST cache
3. otherwise unknown

Do not suppress TODAY because WATCHLIST is unavailable.

When WATCHLIST state is unknown:
- do not show "登録済み"
- do not treat unknown as unregistered
- show neutral button/state such as `WATCHLIST未同期`

ENTRY確認 KPI is private/WATCHLIST-dependent:
- show live count if live state available
- else local cached count with `前回同期` label
- else `—`

## 7. CHART behavior

Default chart source order:

1. GitHub J-Quants Snapshot
2. Version 7 local chart cache if it is newer
3. Live Render chart only when user explicitly requests `Live更新`

This is intentionally different from Version 9.

Opening CHART must not wake Render automatically.

Show:

```
DATA: J-Quants Snapshot
取得日時: ...
```

Provide optional:
```
Live更新
```
button.

If Live update succeeds:
- replace current chart with live J-Quants/Render payload
- persist it in local chart cache
- show live source

If Live update fails:
- keep Snapshot chart visible
- never blank the chart

## 8. ENTRY CHECK

Use Snapshot chart rows for MA-based checks by default.

Use EARLY Snapshot rows for:
- initial rank
- long stage
- RR
- stop distance
- reversal
- settle
- shape

Use WATCHLIST/private data only for private status labels, not for the public technical checks.

Do not require Render availability to calculate the public 9-point ENTRY CHECK when all required EARLY + chart Snapshot fields are present.

## 9. Selected header fallback

For a selected ticker:

Priority:
1. summary row
2. EARLY row

If summary exists:
- show current summary fields as today

If summary is absent but EARLY exists:
- do not show meaningless dashes only
- show:
  - 初動 <初動優先スコア>
  - Rank <初動ランク>
  - RR <RR>

Example:

```
初動 92.8 / Rank S / RR 7.16
```

Keep "総合 —" only if no summary score exists, but supplement it with the EARLY metrics.

## 10. Freshness model

Snapshot freshness is based on meta.generated_at.

Display:

- <= 24h: `Snapshot 最新`
- > 24h: `Snapshotが古い可能性があります`

WATCHLIST freshness is separate and based on last live sync.

Do not mix market-data freshness with WATCHLIST freshness.

## 11. 429 behavior

Version 6 breaker remains for Render operations only.

Because Snapshot is primary:
- Render 429 must not block TODAY/EARLY/RANKING/CHART
- Render 429 must not blank current public-market data
- only WATCHLIST/live actions are degraded

Show a compact private-data status:

```
WATCHLIST Live接続制限中
```

Do not show a full-page connection-error banner when Snapshot data is healthy.

## 12. UI status

Recommended top status chips:

```
MARKET DATA: J-Quants Snapshot
Snapshot: 2026-11-21 16:06
WATCHLIST: 前回同期 / 未同期 / Live
```

Keep the page usable even if WATCHLIST is offline.

## 13. Version 7/8/9 cache/bootstrap

Keep:
- persistent local cache
- GitHub summary/EARLY bootstrap
- GitHub chart Snapshot
- local chart cache

But treat GitHub Snapshot as the normal primary read path, not merely an error fallback.

## 14. Verification

Verify all of these:

1. Fresh browser startup shows TODAY without any Render request.
2. EARLY loads without Render.
3. RANKING loads without Render.
4. Opening 5947 chart loads GitHub J-Quants Snapshot without Render.
5. Daily/weekly MA works without Render.
6. ENTRY CHECK can reach the expected technical status without Render.
7. Render logs show no summary/early/chart request from normal page startup.
8. WATCHLIST unknown is shown as `—`, not 0.
9. WATCHLIST local cache can decorate TODAY without live sync.
10. WATCHLIST sync is explicit/on-demand only.
11. Add/delete/memo remain server-confirmed only.
12. Render 429 does not blank public pages.
13. Selected header for EARLY-only ticker supplements with 初動/Rank/RR.
14. Live chart update is optional and does not replace Snapshot unless successful.
15. No personal data is added to public GitHub Snapshot.

## 15. Save / deploy

Save as Version 10.

Deploy with current private / 本人限定 access.

Do not change:
- Render configuration
- Google Sheets
- J-Quants secrets
- Version 9

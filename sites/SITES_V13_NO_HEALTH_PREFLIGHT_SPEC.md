# Takasan Stock Hunter Sites v1 — Version 13 No-Health-Preflight Private Actions

## Problem

Version 12 correctly sends one user-initiated WATCHLIST sync sequence, but the first request:

```
GET /api/health
```

receives HTTP 429 at the edge before reaching the Render application.

Because health fails first, `/api/watch-board` is never attempted.

Render application logs show no matching health/watch-board request, indicating the 429 occurs before the FastAPI app.

## Goal

Remove unnecessary health preflight requests from user-initiated private/live operations.

Use the actual target endpoint itself as the availability check.

This reduces request count and avoids spending one edge request on health before the useful request.

## Scope

Sites-side only.

Do not change:
- Render API
- Google Sheets
- GitHub Snapshot
- J-Quants
- WATCHLIST storage contract
- public Snapshot read path
- Version 10.1 zero-public-read rule

## 1. WATCHLIST sync — direct target request

When user presses:

```
WATCHLIST同期
```

Do NOT call `/api/health` first.

Instead call exactly:

```
GET /api/watch-board
```

once.

Expected normal request count:

```
health = 0
watch-board = 1
public read = 0
```

If the UI can derive all membership/status/memo data from watch-board, do not call `/api/watchlist`.

Only call `/api/watchlist` if there is a proven field unavailable from watch-board.

## 2. watch-board success

If watch-board returns HTTP 200 with valid payload:

- update WATCHLIST local cache
- update registration badges
- update memo fields
- update `last_synced_at`
- clear private breaker
- set status `Live同期済み`

No health call is needed.

## 3. watch-board transient failure

For:
- network error
- 502
- 503
- 504

Use the existing GET relay/direct fallback behavior if available.

Do not add a health call.

The target request itself is the readiness probe.

## 4. watch-board 429

If watch-board returns 429:

- stop immediately
- keep previous WATCHLIST cache visible
- open private 60-second breaker
- show `WATCHLIST Live接続制限中 · あとXX秒`
- do not retry automatically
- do not call health
- do not call watch-board again until user action after breaker expiry

## 5. Writes — no health preflight

For:
- WATCHLIST add
- WATCHLIST delete
- memo update

Do NOT call `/api/health` before the write.

Send exactly the intended write request once.

If write succeeds:
- perform one `GET /api/watch-board` confirmed refresh
- update local cache

If write returns 429:
- do not retry
- do not claim success
- keep confirmed cached state
- open private breaker

## 6. Live chart update — no health preflight

When user presses `Live更新`:

Do NOT call health first.

Send exactly:

```
GET /api/chart/{ticker}?period=2y
```

once.

If success:
- replace Snapshot chart
- cache live chart
- label source live

If 429/failure:
- keep Snapshot chart
- show compact error
- no health request

## 7. Health endpoint role

`/api/health` becomes diagnostics-only.

It may be used:
- by Render infrastructure health checks
- by explicit developer diagnostics

It must not be part of normal user-facing:
- WATCHLIST sync
- writes
- Live chart update
- Snapshot startup

## 8. Request minimization

Target counts:

### Normal startup/navigation
- health 0
- summary 0
- early 0
- chart live 0
- watch-board 0

### WATCHLIST同期
- health 0
- watch-board 1

### memo save success
- health 0
- PUT memo 1
- watch-board refresh 1

### WATCHLIST add success
- health 0
- POST watchlist 1
- watch-board refresh 1

### delete success
- health 0
- DELETE watchlist 1
- watch-board refresh 1

### Live更新
- health 0
- chart 1

## 9. Breaker behavior

Keep Version 10.2 private breaker.

Important:
- breaker is opened by the target endpoint's 429
- expired breaker clears before the next user action
- no automatic background retry
- no silent no-op

## 10. UI states

WATCHLIST states remain:

- 未同期
- 前回同期
- 同期中…
- Live同期済み
- Live接続制限中 · あとXX秒
- 同期失敗

For Live chart:
- Snapshot表示中
- Live更新中…
- Live更新済み
- Live更新失敗 — Snapshotを表示中

## 11. Diagnostics

Record:
- operation type
- target endpoint
- transport
- status
- started_at
- completed_at
- breaker_opened_at if 429

Do not log secrets.

## 12. Acceptance tests

### A WATCHLIST sync
Click once.

Expected:
- health 0
- watch-board 1
- summary/early/chart/stock-context 0

### B WATCHLIST 429
Expected:
- health 0
- watch-board 1 -> 429
- breaker opens
- cache stays visible

### C memo save
Expected:
- PUT 1
- if success, watch-board 1
- health 0

### D Live chart
Expected:
- chart 1
- health 0
- Snapshot remains on failure

### E rapid clicks
10 sync clicks:
- one watch-board request only

## 13. Save / deploy

Save as the next Site version.

Deploy private / 本人限定.

Do not change Render configuration.
Do not modify Version 12.

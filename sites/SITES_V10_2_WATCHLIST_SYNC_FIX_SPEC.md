# Takasan Stock Hunter Sites v1 — Version 10.2 WATCHLIST Sync Fix

## Observed problem

After Version 10.1:
- Snapshot public pages work correctly.
- User presses `WATCHLIST同期`.
- UI stays at `WATCHLIST: 前回同期`.
- last sync time does not change.
- Render logs show zero matching `/api/health` and zero matching `/api/watch-board`.

Therefore the sync flow is stopping inside Sites before any Render request is sent.

## Goal

Make the explicit WATCHLIST sync button always do one of two visible things:

1. send exactly one allowed private sync sequence, or
2. visibly explain why it is blocked.

Never silently no-op.

## Scope

Sites-side only.

Do not change:
- Render API
- Google Sheets
- GitHub Snapshot
- J-Quants
- public read path
- WATCHLIST storage contract
- Version 10.1 zero-public-read behavior

## 1. Dedicated sync action

Create/ensure one dedicated function:

```
syncWatchlist({ userInitiated: true })
```

The `WATCHLIST同期` button must call this function directly.

Do not route the button through:
- Snapshot refresh
- generic refresh-all
- public startup controller
- chart live controller

## 2. Private circuit breaker namespace

WATCHLIST/private sync breaker must be separate from old public/startup breaker state.

Use a dedicated state/key such as:

```
watchlist_private_breaker
```

Do not allow historical public-data 429 state from Versions 5-9 to block a new WATCHLIST sync.

## 3. Expired breaker cleanup

Before a user-initiated sync:

1. read private breaker state
2. if `expires_at <= now`, clear it immediately
3. proceed with sync

Never keep an expired breaker active because of stale in-memory or localStorage state.

## 4. Active breaker behavior

If the private breaker is genuinely still active:

- do not send a request
- show exact visible state:
  `WATCHLIST同期待機中 · あとXX秒`
- disable the button only for the remaining cooldown
- automatically re-enable when cooldown expires

Do not simply leave the label as `前回同期`.

## 5. Sync sequence

When allowed:

1. set UI state to `同期中…`
2. run one single-flight `GET /api/health`
3. if health 200:
   - request `GET /api/watch-board`
   - request `GET /api/watchlist` only if the UI needs raw membership data
4. validate payloads
5. update local WATCHLIST cache
6. update registration badges
7. update `last_synced_at`
8. clear any private breaker
9. set UI state to `Live同期済み`

Expected minimum sequence:

```
health: 1
watch-board: 1
```

No summary/early/chart/stock-context requests.

## 6. GET transport policy

For these explicit private GETs:

- primary: same-origin relay
- if network error / 502 / 503 / 504: direct Render GET fallback may be used as already supported
- if 429: stop immediately and open the private 60-second breaker
- do not aggressively retry 429

## 7. 429 private sync state

On 429:

- keep existing local WATCHLIST cache visible
- set status:
  `WATCHLIST Live接続制限中`
- show remaining cooldown
- do not change last successful sync timestamp
- do not clear registration badges sourced from cache

After 60 seconds:
- re-enable WATCHLIST同期
- do not auto-sync in the background
- user may press it again

This is important: Version 10.2 should not wake Render automatically.

## 8. Other failures

For terminal non-429 failure:
- keep cached WATCHLIST
- show:
  `WATCHLIST同期に失敗しました`
- provide the button again
- log sanitized status/transport diagnostics

## 9. UI states

WATCHLIST status must be one of:

- `未同期`
- `前回同期`
- `同期中…`
- `Live同期済み`
- `Live接続制限中 · あとXX秒`
- `同期失敗`

Button state must match the status.

No silent state.

## 10. Last sync timestamp

Only update `最終同期` after:
- health 200, and
- required WATCHLIST payload(s) 200 + valid.

Do not update on:
- button click
- health failure
- 429
- partial failure

## 11. Request diagnostics

For a user-initiated sync, record:

```
watchlist_sync_click_id
watchlist_sync_started_at
watchlist_sync_blocked_reason
watchlist_health_status
watchlist_board_status
watchlist_sync_completed_at
```

No secrets.

This should make it obvious whether the click reached transport.

## 12. Preserve Version 10.1 public-read zero rule

During WATCHLIST sync:
- /api/summary = 0
- /api/early = 0
- /api/chart/* = 0
- /api/stock-context/* = 0

Normal startup remains Snapshot-only.

## 13. Verification

### A. Expired breaker
Seed an expired private breaker.
Click WATCHLIST同期.

Expected:
- stale breaker cleared
- health 1 request
- watch-board 1 request
- sync completes

### B. Active breaker
Seed 30 seconds remaining.
Click WATCHLIST同期.

Expected:
- 0 network requests
- visible countdown
- button disabled
- re-enabled after expiry

### C. Normal success
Click once.

Expected:
- health 1
- watch-board 1
- optional watchlist 1 only if necessary
- last sync time changes
- status becomes Live同期済み

### D. 429
Expected:
- cache preserved
- 60-second private breaker
- visible countdown
- no public Render reads

### E. Repeated clicks
Rapidly click 10 times.

Expected:
- single-flight collapses to one sync sequence
- no duplicate requests

## 14. Save / deploy

Save as Version 10.2 or next integer saved version.

Deploy private / 本人限定.

Do not modify Version 11.
Do not change Render configuration.

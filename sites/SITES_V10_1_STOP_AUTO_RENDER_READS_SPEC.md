# Takasan Stock Hunter Sites v1 — Version 10.1 Stop Automatic Render Reads

## Goal

Finish the Version 10 read/write split by removing remaining automatic Render reads for public market data.

Observed Render logs after Version 10 still showed automatic requests:

- GET /api/summary
- GET /api/early
- GET /api/chart/5947.T?period=2y

These requests must no longer occur during normal page startup/navigation/chart opening.

Render should be used only for WATCHLIST/private state and explicit live actions.

## Scope

Sites-side only.

Do not change:
- Render API
- Google Sheets
- J-Quants credentials
- Snapshot generation
- WATCHLIST persistence contract
- EARLY logic
- Entry Hunter logic
- existing Render Web

## 1. Public read path must be Snapshot-only by default

During normal use, these pages/data sources must not call Render automatically:

### TODAY
Use:
- GitHub summary Snapshot
- GitHub EARLY Snapshot
- local WATCHLIST cache if available

Do not call:
- /api/summary
- /api/early

### EARLY
Use GitHub EARLY Snapshot only.

Do not call:
- /api/early

### RANKING
Use GitHub summary Snapshot only.

Do not call:
- /api/summary

### CHART
On chart open / ticker change:
- load GitHub J-Quants chart Snapshot
- optionally prefer a fresher local chart cache
- do not call Render chart API automatically

Do not call:
- /api/chart/{ticker}
- /api/stock-context/{ticker}

unless the user explicitly requests Live update.

## 2. Remove hidden auto-live paths

Audit and disable all code paths that can trigger automatic public-data live fetches, including:

- useEffect on mount
- useEffect on page change
- useEffect on ticker change
- startup controller
- refresh-all handler
- chart selection handler
- stale-cache recovery handler
- background refresh
- fallback-after-Snapshot-load
- legacy API client initialization
- error recovery that silently tries Render public endpoints

Snapshot success must end the public-data startup flow.

Do not "double check" Snapshot data against Render in the background.

## 3. Allowed Render reads

Automatic/explicit Render reads are allowed only for private state:

- GET /api/watchlist
- GET /api/watch-board
- GET /api/health as part of an explicit WATCHLIST/private sync sequence

Allowed triggers:

- WATCHLIST同期 button
- successful WATCHLIST write followed by one confirmed refresh
- explicit private-state recovery action

## 4. Explicit Live chart update

Render chart API may be called only when the user presses an explicit button:

```
Live更新
```

On Live更新:

1. run one single-flight health check if needed
2. call /api/chart/{ticker}?period=2y
3. if successful, replace Snapshot chart
4. persist live chart to local cache
5. label source as live
6. if failed/429, keep Snapshot chart visible

Never auto-trigger Live更新.

## 5. Snapshot update button

Top-right action should be:

```
Snapshot更新
```

This must refresh only GitHub Snapshot resources:

- meta
- summary
- early

It must not call Render.

If a chart is currently open:
- chart Snapshot may be refetched from GitHub for the selected ticker if needed
- still no Render request

## 6. WATCHLIST sync button

Keep separate:

```
WATCHLIST同期
```

This is the only normal UI action that wakes Render for private read state.

Do not combine Snapshot更新 and WATCHLIST同期.

## 7. Page navigation must be network-cheap

Switching between:

- TODAY
- EARLY
- RANKING
- CHART

must not create any Render request.

WATCHLIST page may show local cache immediately and must not auto-sync unless the Version 10 UI explicitly defines WATCHLIST page entry as user consent for live sync. Preferred behavior: do not auto-sync; require WATCHLIST同期 button.

## 8. Selected ticker context

Do not call /api/stock-context automatically.

Build selected context from:

1. summary Snapshot row
2. EARLY Snapshot row
3. chart Snapshot rows

For EARLY-only ticker:
- show 初動優先スコア
- 初動ランク
- RR

Do not leave the header as summary-only dashes when EARLY data exists.

## 9. Refresh semantics

### Snapshot更新
GitHub only.

### WATCHLIST同期
Render private reads only.

### Live更新
Render chart only.

No generic "再取得" button may call all three categories together.

Rename ambiguous refresh buttons where necessary.

## 10. 429 behavior

A Render 429 from WATCHLIST/Live操作 must:

- not affect Snapshot data
- not clear TODAY/EARLY/RANKING/CHART
- not trigger public-data refetches
- only degrade the requested private/live operation

Show compact status such as:

```
WATCHLIST Live接続制限中
```

or

```
Live更新できません。Snapshotを表示しています。
```

## 11. Logging / diagnostics

Add a development diagnostic counter or log for Render request categories:

```
render_public_read_count
render_private_read_count
render_write_count
render_live_chart_count
```

Expected during normal fresh startup + TODAY/EARLY/RANKING/CHART navigation:

```
render_public_read_count = 0
render_live_chart_count = 0
```

WATCHLIST同期 should increment only private read count.

## 12. Acceptance verification

Run a clean-browser / cleared-runtime test.

### Scenario A — normal startup
1. open Site
2. do not click WATCHLIST同期
3. do not click Live更新
4. navigate TODAY -> EARLY -> RANKING -> CHART
5. open 5947 chart
6. switch daily/weekly

Expected Render requests:
- /api/summary = 0
- /api/early = 0
- /api/chart/* = 0
- /api/stock-context/* = 0
- /api/watch-board = 0
- /api/watchlist = 0
- /api/health = 0

### Scenario B — WATCHLIST sync
Click WATCHLIST同期 once.

Expected:
- health allowed
- watch-board/watchlist allowed
- summary/early/chart remain 0

### Scenario C — Live chart update
Click Live更新 for 5947.

Expected:
- one deliberate chart live request
- no summary/early requests
- Snapshot remains if live fails

### Scenario D — write
Save memo once.

Expected:
- one write
- one private WATCHLIST refresh after success
- no public Render reads

## 13. Version

Save as Version 10.1 (or the next sequential saved version if Sites requires integer-only versioning).

Deploy private / 本人限定.

Do not modify Version 10.
Do not change Render configuration.

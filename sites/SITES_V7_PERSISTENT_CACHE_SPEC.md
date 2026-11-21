# Takasan Stock Hunter Sites v1 — Version 7 Persistent Last-Known-Good Cache

## Goal

Keep the Site usable on the free Render plan when Cloudflare/Render edge returns HTTP 429 before requests reach the FastAPI application.

Version 6 circuit breaker remains unchanged.

Version 7 adds persistent client-side last-known-good data so the UI does not become blank during 429 periods.

## Scope

Sites-side only.

Do not change:
- Render API
- Google Sheets
- J-Quants configuration
- WATCHLIST server persistence
- EARLY logic
- Entry Hunter logic
- existing Render Web
- Version 6 retry/circuit breaker rules

## 1. Persistent cache

Persist successful HTTP 200 payloads in browser storage.

Required datasets:
- summary
- early
- watch-board
- health metadata
- most recently viewed chart payloads

Preferred storage:
- localStorage for small JSON datasets
- IndexedDB is acceptable if already available

Do not store secrets, API keys, cookies, Authorization headers, service-account credentials, or J-Quants credentials.

## 2. Cache keys

Use versioned keys, for example:

```
takasan:v1:summary
takasan:v1:early
takasan:v1:watch-board
takasan:v1:health
takasan:v1:chart:<ticker>
```

Each stored record should contain:

```
{
  saved_at: ISO8601,
  payload: ...
}
```

## 3. Startup behavior

On Site startup:

1. Read persistent cache first.
2. If valid cached datasets exist, hydrate the UI immediately.
3. Show a compact status:
   `前回データを表示中 · 更新確認中`
4. Start the existing Version 6 health sequence.
5. If network succeeds, replace cache with fresh data.
6. If network returns 429 and circuit breaker opens, keep cached data visible.

Do not clear cached data before a successful fresh response.

## 4. Stale display

When cached data is being shown because fresh network data is unavailable, display:

```
前回データを表示中
最終取得: YYYY-MM-DD HH:mm
```

Use a neutral amber/gray badge, not red.

Do not label cached values as fresh.

## 5. Freshness

Recommended visual thresholds:

- <= 24h: `前回データ`
- > 24h: `データが古い可能性があります`

Do not delete stale data automatically only because it is old.

The user should still be able to inspect it.

## 6. Dataset replacement

Only overwrite a cached dataset when:

- HTTP status is 200
- payload passes the existing validity checks

Never overwrite cached valid data with:
- empty array caused by error handling
- 429
- 502
- 503
- 504
- network exception
- malformed payload

## 7. WATCHLIST writes

Do not implement offline write queueing.

POST / PUT / DELETE:
- relay only
- no automatic retry
- no local optimistic persistence as if the server succeeded

If a write fails:
- keep current server-confirmed WATCHLIST cache
- show the write error
- do not mark it saved

After a successful write:
- perform one deliberate refresh
- persist the confirmed fresh watch-board response

## 8. Chart cache

Persist chart payload only after successful 200.

Store at least the most recent 10 tickers.

Use a simple LRU strategy or equivalent to avoid unbounded storage.

When a chart network request fails:
- if cached chart exists, show it
- label it `前回チャート`
- keep the last saved timestamp visible

Keep J-Quants/Yahoo source metadata from the cached payload.

## 9. Circuit breaker integration

Version 6 remains authoritative for network behavior.

429:
- open 60s circuit breaker
- no automatic repeated network calls during breaker
- cached UI remains visible

After breaker expires:
- exactly one health recovery attempt
- on success, refresh datasets and cache
- on 429, reopen breaker and keep cache

## 10. Manual refresh

If circuit breaker is closed:
- manual refresh uses normal Version 6 single-flight sequence

If breaker is open:
- do not bypass the breaker
- show remaining cooldown
- do not issue network traffic

## 11. Diagnostics

Keep Version 5 diagnostics and Version 6 circuit metrics.

Add:
- cache_hit: true/false
- cache_saved_at
- cache_age_seconds
- cache_dataset

Do not log cached payload contents.

## 12. Verification

Verify:
1. Fresh successful load populates persistent cache.
2. Browser reload hydrates UI from cache before network completes.
3. Simulated 429 keeps KPI/table data visible.
4. 429 does not overwrite cache.
5. 502/503/504 does not overwrite cache.
6. Successful recovery replaces cache.
7. Old cache shows stale warning.
8. WATCHLIST failed write is not queued or falsely persisted.
9. Successful WATCHLIST write refreshes confirmed cache.
10. Cached chart loads when chart GET fails.
11. J-Quants source metadata is preserved in chart cache.
12. No secrets are stored in browser storage.

## 13. Save / deploy

Save as Version 7.
Deploy with existing private / 本人限定 access.
Do not change Version 6.
Do not change Render configuration.

# Takasan Stock Hunter Sites v1 — Version 3 Retry / Cold Start Spec

## Scope

Apply this change to the ChatGPT Sites implementation only.

Do not change:
- Render API
- Google Sheets
- J-Quants configuration
- WATCHLIST persistence contract
- EARLY rules
- Entry Hunter rules
- existing Render Web

Target the Sites relay and startup data-loading behavior.

## Upstream

```
https://takasan-stock-hunter-api-ue7i.onrender.com
```

## 1. Startup sequence

Do not request the four main resources in parallel immediately.

Required order:

1. Request `/api/health`.
2. Wait until health returns HTTP 200.
3. Only then request these in parallel:
   - `/api/summary`
   - `/api/early`
   - `/api/watch-board`

Use the exact same startup sequence when the user presses the refresh button.

## 2. Health retry policy

Retry `/api/health` only when the failure is one of:

- network exception
- HTTP 502
- HTTP 503
- HTTP 504

Backoff schedule:

- attempt 1: immediately
- attempt 2: after 2 seconds
- attempt 3: after 5 seconds
- attempt 4: after 10 seconds
- attempt 5: after 20 seconds

Stop retrying immediately after HTTP 200.

Do not loop forever.

## 3. Relay retry policy

For Sites Worker -> Render upstream requests:

Retry GET requests for:

- network exception
- HTTP 502
- HTTP 503
- HTTP 504

Do not automatically retry:
- POST
- PUT
- PATCH
- DELETE

This avoids duplicate writes.

Use Cloudflare Workers-compatible fetch settings only.
Do not use `redirect: "error"`.

Preserve path and query string exactly.

## 4. Upstream timeout

Keep the upstream timeout long enough for Render free-plan cold start.

Recommended upper bound: 120 seconds.

Do not use an infinite timeout.

## 5. Keep last good data

When a transient request fails, never replace previously loaded valid data with empty arrays.

Do not turn:

- 204 summary rows -> 0
- 16 early rows -> 0
- existing WATCHLIST rows -> 0

because of a temporary 502/503/504/network error.

Only replace a dataset after a successful HTTP 200 response containing a valid payload.

## 6. UI state

During health retry / cold start show:

```
Render APIを起動しています。自動的に再接続します…
```

Do not show the normal fatal data error during retry.

When reconnect succeeds:
- remove the startup message automatically
- load summary / early / watch-board
- preserve the existing screen and state

Only after all configured health attempts fail show:

```
APIに接続できませんでした。再取得してください。
```

## 7. Cache / last-known-good behavior

If the current Sites implementation already has in-memory or local last-known-good data, preserve it.

Do not introduce persistent storage that changes the WATCHLIST or source-of-truth behavior.

The cache is display resilience only.

## 8. J-Quants

Keep current market-data behavior unchanged:

- PRIMARY: J-Quants
- FALLBACK: Yahoo Finance

Do not modify Render JQUANTS_API_KEY or any upstream market-data code.

## 9. Verification

Before saving Version 3 verify:

1. First open can wait for `/api/health`.
2. Health retry follows the configured backoff.
3. `/api/summary` returns 200 after health is ready.
4. `/api/early` returns 200 after health is ready.
5. `/api/watch-board` returns 200 after health is ready.
6. A transient GET 502 is retried.
7. A transient GET failure does not reset valid KPI data to zero.
8. POST/PUT/DELETE are not automatically retried.
9. CHART still reports J-Quants when J-Quants succeeds.
10. WATCHLIST memo save still works.
11. Existing Version 2 remains unchanged.

## 10. Save / deploy

Save this as:

```
Version 3
```

Deploy only with the existing access mode (本人限定 / private).

Do not change sharing or public access.

Do not modify the Render environment.

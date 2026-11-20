# Takasan Stock Hunter Sites v1 — Version 4 Preview Direct-GET Fallback

## Why

Version 3 correctly retries cold starts, but ChatGPT's embedded Sites editor/preview can fail before the Sites Worker reaches Render. In that failure mode, Render receives no /api/health request at all.

The Render API already allows cross-origin browser requests:

- CORS origins: *
- credentials: false
- methods: *

Therefore, add a safe browser-side direct fallback for GET requests only.

## Scope

Sites-side only.

Do not change:
- Render API
- Google Sheets
- J-Quants
- WATCHLIST persistence
- EARLY rules
- Entry Hunter rules
- existing Render Web

## Upstream

```
https://takasan-stock-hunter-api-ue7i.onrender.com
```

## 1. GET transport order

For GET requests, use two transports:

1. Primary: same-origin Sites relay, e.g. `/api/health`
2. Fallback: direct browser GET to Render, e.g.
   `https://takasan-stock-hunter-api-ue7i.onrender.com/api/health`

The direct fallback is allowed only for GET.

## 2. Per-attempt behavior

For each configured GET retry attempt:

1. Try same-origin relay.
2. If it returns 200, use it.
3. If it fails with network error, 502, 503, or 504:
   - immediately try the direct Render GET once.
4. If direct GET returns 200, use it and stop retrying.
5. If both transports fail transiently, wait according to the Version 3 backoff and try again.

Backoff remains:

- attempt 1: immediate
- attempt 2: 2 seconds
- attempt 3: 5 seconds
- attempt 4: 10 seconds
- attempt 5: 20 seconds

Do not double the backoff for relay and direct; the direct check is part of the same attempt.

## 3. Startup order

Keep Version 3 ordering:

1. health first
2. only after health HTTP 200:
   - summary
   - early
   - watch-board in parallel

Each GET may use relay -> direct fallback.

## 4. Write safety

For POST / PUT / PATCH / DELETE:

- same-origin relay only
- no automatic retry
- no direct browser fallback

This prevents duplicate writes when the relay may have reached upstream but the response was lost.

## 5. Last-known-good data

Keep Version 3 resilience:

- never replace valid data with empty arrays after a transient failure
- keep current KPI/table data visible while reconnecting
- only replace after a successful 200 + valid payload

## 6. UI / diagnostics

During reconnection show:

```
APIへ再接続しています…
```

If direct Render fallback succeeds, optionally expose a small non-intrusive diagnostic:

```
DATA ONLINE · DIRECT
```

or log:

```
transport=direct
```

Do not show this as an error.

When normal relay succeeds, use:

```
transport=relay
```

Do not expose secrets.

## 7. J-Quants

Keep existing behavior:

- PRIMARY: J-Quants
- FALLBACK: Yahoo Finance

The transport fallback changes only how Sites reaches the existing Render API; it does not change market-data selection.

## 8. Verification

Before saving Version 4, verify all of these:

1. Embedded ChatGPT Sites editor/preview loads health successfully even when the Sites relay fails before reaching Render.
2. Render receives the direct fallback GET.
3. summary loads.
4. early loads.
5. watch-board loads.
6. Live deployed Site still works.
7. KPI data does not reset to zero on transient failures.
8. CHART remains DATA: J-Quants when J-Quants succeeds.
9. WRITE requests are not retried and do not use direct fallback.
10. WATCHLIST memo save remains valid in the deployed Site.

## 9. Files expected to change

Prefer limiting changes to:

- src/api-client.js
- src/retry-policy.js
- src/main.jsx only if UI status needs adjustment
- worker/index.js only if diagnostics are needed
- related verification scripts/tests

## 10. Save / deploy

Save as:

```
Version 4
```

Deploy with the existing private / 本人限定 access mode.

Do not change Version 3.
Do not change Render configuration.

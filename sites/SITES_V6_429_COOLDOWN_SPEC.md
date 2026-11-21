# Takasan Stock Hunter Sites v1 — Version 6 Cloudflare 429 Cooldown Spec

## Background

Diagnostics from Version 5 showed:

- relay /api/health -> HTTP 429
- direct /api/health -> HTTP 429
- Server: cloudflare
- CF-Ray: NRT
- no Retry-After
- no matching Render application request log

Therefore 429 is occurring before the FastAPI application receives the request.

Do not increase retry frequency for 429.

## Scope

Sites-side only.

Do not change:
- Render API
- Google Sheets
- J-Quants
- WATCHLIST persistence
- EARLY logic
- Entry Hunter logic
- existing Render Web

## 1. Single-flight health request

Only one health startup sequence may run at a time.

If:
- page mount
- refresh button
- route change
- React re-render

tries to start another health sequence while one is already active, reuse the existing promise/state and do not start another network request.

## 2. No background health polling

Do not poll /api/health continuously.

Health requests are allowed only:
- initial page startup
- explicit user refresh
- recovery after a transient failure sequence

Do not issue periodic 5s / 10s / 30s health polling from the browser.

## 3. 429 circuit breaker

When relay or direct returns HTTP 429:

- stop the current retry chain immediately
- do not try the alternate transport again in the same cycle if it already produced 429
- open a 60-second circuit breaker
- during the breaker window, no automatic /api/health requests may be sent
- keep last-known-good data visible
- show a non-fatal message:
  "接続制限中です。約1分後に再接続できます。"
- keep the manual refresh button disabled or guarded until the cooldown ends

After 60 seconds:
- allow exactly one health attempt
- use relay first
- if relay gets 502/503/504/network error, direct fallback is allowed
- if any transport gets 429 again, reopen the 60-second breaker

## 4. 502/503/504 policy unchanged

For non-429 transient failures keep Version 3/4 behavior:

- health first
- relay -> direct fallback
- backoff: 2s -> 5s -> 10s -> 20s
- maximum 5 attempts

## 5. Data fetch de-duplication

After health succeeds:

- issue summary / early / watch-board once each
- do not duplicate those calls because of re-renders
- use a single Promise.all / Promise.allSettled batch
- do not refetch until explicit refresh or a deliberate recovery cycle

## 6. Last-known-good data

Never replace valid data with empty arrays due to:
- 429
- 502
- 503
- 504
- network error

Preserve:
- summary
- early
- watch-board
- current KPI values

## 7. Writes

POST / PUT / PATCH / DELETE:
- relay only
- no direct fallback
- no automatic retry
- no batching that could duplicate writes

## 8. Diagnostics

Keep Version 5 diagnostics.

For 429 add:
- circuit_opened_at
- circuit_expires_at
- suppressed_request_count

This will prove whether repeated calls are being suppressed.

## 9. Verification

Verify:
1. One page load produces one health sequence only.
2. React re-render does not create extra health requests.
3. Route changes do not create extra health requests.
4. 429 opens a 60-second breaker.
5. During breaker no network health calls occur.
6. After 60 seconds exactly one recovery health call occurs.
7. 502/503/504 still use Version 3/4 retry.
8. Existing data never resets to zero.
9. CHART remains J-Quants primary.
10. WATCHLIST memo save still works.
11. Live Site and embedded editor both use the same single-flight guard.

## 10. Save / deploy

Save as Version 6.
Deploy with the existing private / 本人限定 access mode.
Do not change Version 5.
Do not change Render configuration.

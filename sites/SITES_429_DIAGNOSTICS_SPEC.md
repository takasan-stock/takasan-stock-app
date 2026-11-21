# Takasan Stock Hunter Sites v1 — 429 Diagnostics Spec

## Goal

Identify where HTTP 429 is generated without changing the current retry policy or upstream systems.

This is diagnostics only.

Do not change:
- Render API
- Google Sheets
- J-Quants configuration
- WATCHLIST persistence
- EARLY logic
- Entry Hunter logic
- current retry timing
- current write behavior
- current sharing/access mode

## Background

Observed behavior:
- Sites relay/direct validation can return HTTP 429.
- At the same time, Render shows no matching request.
- Therefore the 429 may be generated before the request reaches Render.

We need transport-level diagnostics.

## 1. Capture on every 429

For any GET request that receives HTTP 429, capture:

- timestamp in ISO 8601
- request path only
- transport: relay or direct
- status: 429
- response header: Retry-After
- response header: Server
- response header: CF-Ray
- response header: Content-Type
- response header: x-request-id, if present
- response header: x-openai-request-id, if present
- response body preview, max 200 characters

Do not capture:
- API keys
- cookies
- Authorization headers
- request bodies
- full URLs containing secrets
- user personal data

## 2. Body handling

Read at most the first 200 characters of the 429 response body.

If JSON, preserve only a short sanitized preview.

Do not render raw HTML pages into the normal user UI.

## 3. Where to expose diagnostics

Primary:
- console / diagnostic log in the Sites runtime

Secondary:
- if there is already a debug/status object, add a structured diagnostic entry

Do not clutter the normal production UI.

If a visible diagnostic is needed, keep it compact and only show:
- transport
- status
- Retry-After
- Server

## 4. Do not retry 429 yet

Keep current policy:
- 429 is not automatically retried
- GET retry remains only for network error / 502 / 503 / 504
- WRITE requests remain no-retry

We are diagnosing first.

## 5. Relay vs direct

Record separate diagnostic entries for:

### relay
Example:
```
transport=relay
path=/api/health
status=429
```

### direct
Example:
```
transport=direct
path=/api/health
status=429
```

This distinction is required.

## 6. Render correlation

For each diagnostic event include the exact timestamp to the second so it can be compared with Render logs.

If Render has no matching request at that timestamp, note:
```
upstream_reached=unknown
```

Do not infer that Render was reached unless supported by Render logs.

## 7. Verification

Run these cases:

1. Normal relay GET success.
2. Simulated relay transient failure with direct fallback.
3. Captured relay 429.
4. Captured direct 429.
5. Verify 429 is not retried automatically.
6. Verify 502/503/504 still use Version 3/4 retry behavior.
7. Verify WRITE requests are unchanged.
8. Verify J-Quants chart behavior is unchanged.
9. Verify no secret values appear in logs.

## 8. Save

Save as a new private version after diagnostics are added.

Do not change public sharing.

Report:
- files changed
- one sanitized 429 diagnostic sample for relay
- one sanitized 429 diagnostic sample for direct, if reproducible
- whether Retry-After is present
- Server value
- CF-Ray value
- Content-Type
- whether Render received a matching request

# Takasan Stock Hunter Sites v1 — Version 9 GitHub Chart Bootstrap

## Goal

Allow CHART to display during Cloudflare/Render 429 even when:
- Live Render API is unavailable, and
- the browser has no Version 7 local chart cache yet.

Use J-Quants-generated public market chart snapshots from the existing public `sites-data` branch.

## Security

Chart OHLCV is public market data.

Do not publish:
- WATCHLIST
- memos
- personal notes
- API keys
- J-Quants credentials
- cookies
- write state

## Snapshot generation

GitHub Actions now exports daily chart snapshots for every ticker present in the public summary dataset.

Each chart file contains up to 260 daily bars from J-Quants.

Path pattern:

```
sites-bootstrap/charts/<Ticker>.json
```

Example:

```
https://raw.githubusercontent.com/takasan-stock/takasan-stock-app/sites-data/sites-bootstrap/charts/5947.T.json
```

Payload shape matches the live chart API closely:

```
{
  "ticker": "5947.T",
  "rows": [...],
  "source": "J-Quants",
  "primary_source": "J-Quants",
  "fallback_used": false,
  "fallback_reason": null,
  "generated_at": "..."
}
```

## Chart source priority

Use this order:

1. Live Render chart API
2. Version 7 local chart cache
3. Version 9 GitHub chart snapshot
4. no chart available

Never let an older GitHub snapshot overwrite a fresher local/live chart.

## Loading a GitHub chart snapshot

Only fetch the GitHub chart snapshot when:
- live chart request is unavailable/fails, and
- no valid local cached chart exists.

Use GET only.

One GitHub chart fetch per ticker selection.

Do not poll chart snapshots in the background.

## Display

When GitHub chart snapshot is shown:

- render normal candles
- render normal volume
- keep daily/weekly toggle
- calculate/display the same MA series from snapshot rows
- keep ENTRY CHECK MA checks based on the displayed rows
- set source label:
  `DATA: J-Quants Snapshot`
- show:
  `取得日時: YYYY-MM-DD HH:mm`

This is not live data; visually distinguish it with a neutral amber/gray badge.

## Daily / weekly

Snapshot stores daily rows only.

Weekly view must continue to aggregate those daily rows in the browser using the existing weekly aggregation logic.

Daily:
- MA5
- MA21
- MA200

Weekly:
- MA5
- MA40

## ENTRY CHECK

When GitHub chart snapshot is loaded:

- chart-derived MA checks may be evaluated from snapshot rows
- EARLY/detail conditions may continue to use current bootstrap/cache context
- clearly mark the overall data mode as snapshot
- do not claim live freshness

## Ticker lookup

Normalize ticker to the same canonical form used by summary:
- e.g. `5947.T`

Construct:

```
https://raw.githubusercontent.com/takasan-stock/takasan-stock-app/sites-data/sites-bootstrap/charts/<encoded ticker>.json
```

Validate:
- HTTP 200
- rows is a non-empty array
- source == "J-Quants"
- ticker matches requested ticker

If validation fails, ignore the snapshot and preserve any existing chart state.

## Version 6/7/8 remain

Keep:
- single-flight
- 429 circuit breaker
- cooldown
- last-known-good local cache
- GitHub summary/EARLY bootstrap
- write safety
- J-Quants primary live API

## WATCHLIST

No change.

GitHub chart snapshots contain no WATCHLIST information.

WATCHLIST registration state must come only from:
- live API, or
- existing local WATCHLIST cache.

## Verification

Verify:
1. Live chart success still wins.
2. Local chart cache wins over GitHub snapshot.
3. With live failure + no local chart cache, 5947.T loads GitHub chart.
4. Candles render.
5. Volume renders.
6. Daily MA5/21/200 render.
7. Weekly aggregation and MA5/40 render.
8. ENTRY CHECK MA values are populated from snapshot.
9. DATA label is `J-Quants Snapshot`.
10. No WATCHLIST/personal data exists in chart JSON.
11. Unknown ticker fails safely.
12. 429 does not erase an already displayed chart.

## Save / deploy

Save as Version 9.
Deploy with current private / 本人限定 access.
Do not modify Render configuration.

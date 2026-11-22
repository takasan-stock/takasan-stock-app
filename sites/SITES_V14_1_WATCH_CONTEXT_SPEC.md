# Takasan Stock Hunter Sites v1 — Version 14.1 Full Watch Context Snapshot

## Problem

Version 14 migration succeeds: WATCHLIST rows and memos are stored in the Site Database.

However, the promotion board currently joins DB WATCHLIST rows only against the EARLY TOP20/13 Snapshot.

As a result, WATCHLIST tickers that are valid in the full weekly screening dataset but are not currently in EARLY TOP20 appear as:

- — データなし
- rank —
- stage —
- RR —
- 週足転換Hデータなし

Observed examples after migration:

- 1934.T
- 2292.T
- 7261.T
- 9672.T

This is a data-join bug, not a DB migration bug.

## Goal

Publish a public, minimal, full weekly watch-context Snapshot and use it for WATCHLIST promotion-board enrichment.

Keep DB membership/memo private.

## Public Snapshot

New file:

```
https://raw.githubusercontent.com/takasan-stock/takasan-stock-app/sites-data/sites-bootstrap/watch-context.json
```

It contains only public market-derived fields required by `build_watchlist_promotion_board`:

- Ticker
- 初動優先スコア
- 初動ランク
- 長期ステージ
- RR目安
- 実戦想定損切り距離%
- 反転確認
- 反転定着
- EARLY形状判定
- 急落判定
- 追撃余地
- 初動鮮度週
- 20週MA再奪回から週数
- 40週MA再奪回から週数

Do not publish:
- WATCHLIST membership
- memo
- registration date
- personal notes
- secrets

## Source priority

For WATCHLIST promotion-board enrichment use:

1. watch-context Snapshot
2. EARLY Snapshot only as a fallback if a ticker is absent from watch-context
3. no-data only if both are missing

Do not use EARLY TOP20 alone as the main enrichment source.

## Join behavior

For each Site DB WATCHLIST item:

- join by canonical Ticker
- use the highest 初動優先スコア row if duplicates exist
- preserve DB memo / registered_at
- recompute promotion status from current Snapshot every time Snapshot changes

Do not write derived status back into DB.

## Derived logic

Use the same logic as `early_rules.py::build_watchlist_promotion_board`.

The expected status for migrated rows should now be derived from full weekly data, not TOP20 membership.

## UI

After Snapshot refresh:

- rows that previously showed —データなし should populate rank/stage/RR/stop if the ticker exists in watch-context
- promotion counts should update
- memo must remain unchanged
- WATCHLIST総数 remains DB count

If a ticker truly has no full weekly context, keep:
- — データなし
- 週足転換Hデータなし

## Snapshot freshness

Use the same generated_at as the other Snapshot files.

Display no extra personal data.

## Render

Normal Version 14 WATCHLIST operation remains Render-independent.

Expected Render request count:
- WATCHLIST page load = 0
- Snapshot refresh = 0
- memo save = 0
- add/delete = 0

## Verification

Use the migrated five tickers:

- 5947.T
- 1934.T
- 2292.T
- 7261.T
- 9672.T

Verify:

1. all 5 DB rows remain present
2. 9672 memo remains preserved
3. watch-context contains the expected tickers when present in 週足転換H
4. rank/stage/RR/stop are populated from watch-context
5. promotion status counts are recomputed
6. no WATCHLIST/memo data appears in watch-context.json
7. Render request count remains 0
8. Snapshot refresh updates derived status without changing DB rows

## Save / deploy

Save as the next Site version.

Keep private / 本人限定.

Do not modify Version 14 DB contents except normal reads/writes.
Do not delete legacy Render/Sheets data.

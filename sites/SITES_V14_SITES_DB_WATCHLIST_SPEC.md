# Takasan Stock Hunter Sites v1 — Version 14 Sites Database WATCHLIST

## Goal

Move WATCHLIST private state from Render / Google Sheets to the ChatGPT Site's built-in database.

Public market data remains Snapshot-primary:

- TODAY
- EARLY
- RANKING
- CHART
- ENTRY CHECK

Private state becomes Site-database-primary:

- WATCHLIST membership
- registration date
- memo

After Version 14 is validated, normal WATCHLIST operations must not require Render.

## Why

Version 13 minimizes Render requests, but the single watch-board request can still receive edge HTTP 429 before reaching the Render application.

ChatGPT Sites provides a per-Site database. Use it for the private WATCHLIST state.

Do not move public market snapshots into the database.

## Safety / rollout

This is a migration.

Requirements:

- keep Version 13 unchanged
- do not delete Google Sheets WATCHLIST data
- do not delete Render WATCHLIST endpoints
- do not clear local WATCHLIST cache
- do not change public sharing; keep the Site private / 本人限定
- do not remove rollback paths until the migration is validated in the browser

Code rollback does not imply database rollback. Treat database migrations as forward-only and idempotent.

## 1. Database schema

Use the Site's built-in database.

Create an idempotent migration.

### watchlist_items

Required fields:

```sql
CREATE TABLE IF NOT EXISTS watchlist_items (
  ticker TEXT PRIMARY KEY,
  code TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  memo TEXT NOT NULL DEFAULT '',
  registered_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Use the database's supported SQL dialect/types if exact syntax differs, while preserving these semantics.

### app_meta

```sql
CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Store at minimum:

- schema_version = 14
- watchlist_storage = sites_db
- watchlist_migrated_at
- watchlist_migration_source

Do not store API keys or secrets.

## 2. Server-side data access

Access the Site database from server-side Site code/routes only.

Do not expose arbitrary SQL to the browser.

Provide minimal internal endpoints/actions equivalent to:

- GET WATCHLIST
- POST add WATCHLIST item
- PUT memo
- DELETE WATCHLIST item

Validate all inputs.

### Ticker validation

Accept canonical Japanese ticker forms used by the app, e.g.:

```
5947.T
9672.T
2292.T
```

Normalize whitespace/case.

Reject empty or malformed ticker input.

### Memo validation

- plain text
- trim only where appropriate
- preserve Japanese text
- maximum length: 1000 characters
- do not render memo as raw HTML

## 3. Database WATCHLIST read

On Site startup:

1. load Snapshot public-market data
2. query Site DB WATCHLIST
3. merge the two locally in the Site runtime
4. render WATCHLIST / registration badges / TODAY decorations

No Render request is required.

Status label should show:

```
WATCHLIST: Sites DB
```

Optionally show:

```
保存先: Site Database
```

Remove the normal dependency on:

- WATCHLIST同期
- Render watch-board sync
- Render health preflight

The Version 13 Render path remains available only as legacy diagnostic/migration fallback, not normal operation.

## 4. Promotion board must be derived, not stored

Do not store these derived fields in the DB:

- 理想本命
- ENTRY確認
- WATCH
- rank
- stage
- RR
- stop distance
- next action

Compute them from the current GitHub EARLY Snapshot joined with DB WATCHLIST membership.

Use the same logic as `early_rules.py::build_watchlist_promotion_board`.

### Ideal

All true:

- 初動ランク == S
- 長期ステージ == EARLY_STAGE_OK
- RR目安 >= 3.0
- 実戦想定損切り距離% between 3.0 and 5.0 inclusive
- 反転確認 == ✅反転確認
- 反転定着 == ✅定着確認
- EARLY形状判定 == ✅EARLY適合

Status:

```
⭐ 理想本命
```

Next action:

```
Entry Hunter / Pre-Tradeへ
```

### ENTRY確認

Not Ideal and all true:

- 初動ランク in S/A
- 長期ステージ in EARLY_STAGE_OK / TRANSITION
- RR目安 >= 2.5
- 実戦想定損切り距離% <= 6.0
- 反転確認 == ✅反転確認
- 反転定着 == ✅定着確認
- EARLY形状判定 == ✅EARLY適合

Status:

```
🔥 ENTRY確認
```

Next action:

```
Entry Hunterでタイミング確認
```

### WATCH

Not Ideal / not ENTRY and market data exists, and either:

- 初動ランク in S/A/B
- or 長期ステージ in EARLY_STAGE_OK / TRANSITION / PULLBACK_BORDER

Status:

```
👀 WATCH
```

Next action:

```
週足・反転条件を継続監視
```

### WAIT

Market data exists but no higher status:

```
⏸ WAIT
```

Next action:

```
条件改善待ち
```

### No data

```
— データなし
```

Next action:

```
週足転換Hデータなし
```

Sort order:

1. 理想本命
2. ENTRY確認
3. WATCH
4. WAIT
5. データなし

Within status:
- 初動優先スコア descending
- ticker ascending

## 5. One-time migration from Version 13 local cache

Do not hardcode the user's WATCHLIST or memo values into source code.

When Site DB is empty and a valid Version 13 local WATCHLIST cache exists, show:

```
WATCHLISTをSites DBへ移行
```

This migration must be explicit/user-initiated.

Before import:

- show item count
- show ticker/name/memo preview
- clearly say Render/Sheets data will not be deleted

On confirmation:

1. read local cached WATCHLIST
2. validate each item
3. upsert into Sites DB in a transaction if supported
4. preserve memo
5. preserve registered date if known
6. set app_meta migration fields
7. re-read DB
8. verify row count and ticker set
9. only then show migration success

Do not delete local cache after migration.

Keep it as rollback/reference data.

## 6. Migration conflict policy

If DB already contains a ticker:

- DB membership wins
- if DB memo is non-empty, keep DB memo
- if DB memo is empty and local cache memo is non-empty, local memo may fill it
- do not duplicate ticker rows

Migration must be idempotent.

Running it twice must not create duplicates.

## 7. Add to WATCHLIST

From TODAY/EARLY/RANKING/CHART:

When user presses WATCHLIST:

- write directly to Site DB
- no Render
- no health
- no external sync

Required fields:

- ticker
- code
- name
- registered_at

After success:

- update DB-backed WATCHLIST state
- update star/registration badges
- recompute promotion board using current Snapshot
- persist/update local cache as a convenience copy

If duplicate:
- do not error
- return/display already registered

## 8. Memo save

Memo save:

- update Site DB once
- no Render
- no retry loop
- re-read the affected row or confirm returned row
- update local cache

UI states:

- 保存中…
- 保存済み
- 保存失敗

Never claim success before DB confirmation.

## 9. Delete

Delete:

- keep existing confirmation dialog
- delete Site DB row once
- no Render
- update local cache after DB confirmation
- remove registration badge immediately only after success

## 10. Local cache role

Version 7 local cache remains.

New priority for private state:

1. Site DB confirmed state
2. local cache only as temporary display fallback if DB read fails
3. legacy Render data only through an explicit diagnostic/migration action

Do not silently overwrite newer Site DB state with stale local cache.

Track cache saved_at and DB updated_at where available.

## 11. Multi-device behavior

Site DB is the shared Site-level source of truth.

After a DB write on desktop, a later Site DB read on iPad should see the same WATCHLIST/memo state.

Do not rely on localStorage for cross-device persistence.

## 12. Privacy / sharing guard

The Site is currently private / 本人限定.

Because the Site DB is Site-scoped private state:

- keep the Site private during Version 14 rollout
- do not change sharing/public access

If the Site is ever shared with other viewers in the future, per-viewer isolation/auth must be designed before allowing those viewers to access WATCHLIST data.

Do not assume per-user row isolation automatically.

## 13. Backup / rollback

Before migration:

- leave Google Sheets and Render data untouched
- leave local cache untouched

Add an owner-only export action:

```
WATCHLISTバックアップ(JSON)
```

Export fields:

- ticker
- code
- name
- memo
- registered_at
- exported_at
- schema_version

Do not export secrets.

Optional import may be added only if safely validated.

## 14. Remove normal Render dependency

After successful DB migration, normal operations must produce:

### Startup
- Render health = 0
- Render watch-board = 0
- Render summary/early/chart = 0

### Add
- Render = 0

### Memo save
- Render = 0

### Delete
- Render = 0

### WATCHLIST page
- Render = 0

### Chart/TODAY registration badge
- Render = 0

Render remains only as legacy infrastructure outside the normal Version 14 path.

## 15. UI updates

Replace:

```
WATCHLIST同期
```

with a storage/status area such as:

```
WATCHLIST: Sites DB
保存先: Site Database
```

If DB read succeeds, no sync button is required.

Useful buttons:

- WATCHLISTバックアップ(JSON)
- optional 再読込 (Site DB only)

Do not label a Site DB read as Live Render sync.

## 16. Snapshot / WATCHLIST join

Snapshot remains primary for market analysis.

For each DB WATCHLIST ticker:

- enrich from EARLY Snapshot
- if needed, use summary Snapshot for name/score context
- derived promotion status updates whenever Snapshot updates

This means a ticker can move from WATCH -> ENTRY確認 -> 理想本命 without changing the DB row.

## 17. Tests

### Migration
- empty DB + local cache 5 items -> 5 DB rows
- memo preserved
- second migration -> still 5
- Render/Sheets untouched

### Add
- add new ticker -> one DB row
- duplicate add -> no duplicate

### Memo
- save Japanese memo
- reload page -> memo persists

### Delete
- confirm delete
- reload -> row remains deleted

### Cross-page
- add from TODAY
- WATCHLIST page reflects it
- CHART badge reflects it

### Derived board
Use current Snapshot:
- verify ideal/entry/watch counts from DB membership
- Snapshot update recomputes status without rewriting DB

### Render independence
Verify all normal operations:
- Render request count = 0

### Local cache failure
Clear browser localStorage:
- Site DB WATCHLIST still loads

### Cross-device
If practical:
- desktop save
- iPad refresh
- same DB state visible

## 18. Save / deploy

Save as Version 14.

Keep private / 本人限定.

Do not modify Version 13.

Do not delete or change the existing Render / Google Sheets WATCHLIST data.

Report:

- database schema/migration
- files changed
- migration source/result
- DB row count
- memo persistence
- derived WATCHLIST status counts
- Render request count during normal operations
- backup export result
- Version 14 save/deploy result

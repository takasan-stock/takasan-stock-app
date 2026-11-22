# Takasan Stock Hunter Sites v1 — Version 16 WATCHLIST Promotion History

## Goal

Add a persistent promotion-history feature for WATCHLIST status changes while keeping Version 15 behavior stable.

Track transitions such as:

- 👀 WATCH → 🔥 ENTRY確認
- 🔥 ENTRY確認 → ⭐ 理想本命
- ⭐ 理想本命 → 🔥 ENTRY確認
- 👀 WATCH → ⏸ WAIT

History is for review/audit only.

Do not change the current promotion logic.

## Existing architecture to preserve

Public market data:
- J-Quants Snapshot
- GitHub Snapshot
- full watch-context Snapshot

Private state:
- Sites DB WATCHLIST
- memo
- registered_at

Render:
- normal usage request count = 0

Keep all of this unchanged.

## 1. Database schema

Add an idempotent migration.

### watchlist_status_history

Required semantics:

```sql
CREATE TABLE IF NOT EXISTS watchlist_status_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  previous_status TEXT,
  new_status TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  snapshot_generated_at TEXT,
  initial_rank TEXT,
  long_stage TEXT,
  rr REAL,
  stop_distance REAL,
  reason_summary TEXT,
  created_at TEXT NOT NULL
);
```

Use the Site DB's supported SQL dialect/types if exact syntax differs.

### watchlist_status_state

Store the latest derived status separately:

```sql
CREATE TABLE IF NOT EXISTS watchlist_status_state (
  ticker TEXT PRIMARY KEY,
  current_status TEXT NOT NULL,
  last_changed_at TEXT NOT NULL,
  snapshot_generated_at TEXT,
  updated_at TEXT NOT NULL
);
```

Do not add derived status columns to watchlist_items.

## 2. Status source

Status must be recomputed from:

- Site DB WATCHLIST membership
- current GitHub watch-context Snapshot

Use exactly the same Version 15 promotion logic.

Do not create a second set of threshold rules.

## 3. When to record history

Run status reconciliation after:

- Site startup after Snapshot + WATCHLIST are available
- Snapshot更新
- WATCHLIST add
- WATCHLIST migration/import
- Site DB WATCHLIST再読込 if Snapshot is already loaded

For each WATCHLIST ticker:

1. compute current derived status
2. read watchlist_status_state
3. if no state exists:
   - create state row
   - do NOT create a fake promotion event by default
4. if current status differs from stored status:
   - insert one history row
   - update state row

Never create duplicate events when status is unchanged.

## 4. Idempotency

Re-running reconciliation against the same Snapshot must not create duplicate history rows.

Use:
- current state comparison
- Snapshot timestamp
- transaction where possible

Rapid re-renders must not create duplicate events.

## 5. Reason summary

For a real transition, store a short safe explanation derived from current public fields.

Examples:

- `反転・定着・形状が揃いENTRY条件へ`
- `損切り3〜5%・Rank S・EARLY_STAGE_OKで理想本命`
- `反転条件未達でWATCHへ`
- `長期ステージがTRANSITIONへ`

Do not store raw secrets or personal memo text in history.

## 6. History UI

On WATCHLIST page add a section:

```
昇格・状態履歴
```

Default view:
- newest first
- latest 20 events

Columns/cards:

- 日時
- 銘柄
- 変化
- Rank
- Stage
- RR
- 理由

Example:

```
2026-11-22 18:40
9672 東京都競馬
👀 WATCH → 🔥 ENTRY確認
Rank S / EARLY_STAGE_OK / RR 7.01
反転・定着・形状が揃いENTRY条件へ
```

## 7. Filter

Add lightweight filters:

- 全て
- 昇格のみ
- 降格/悪化
- 銘柄別

Define status priority:

```
⭐ 理想本命 = 4
🔥 ENTRY確認 = 3
👀 WATCH = 2
⏸ WAIT = 1
— データなし = 0
```

Promotion means new priority > previous priority.

## 8. Current-status age

In the existing WATCHLIST board, optionally show:

```
この状態: 3日
```

or

```
昇格: 今日
```

based on watchlist_status_state.last_changed_at.

Keep this visually compact.

## 9. TODAY integration

Do not redesign TODAY yet.

Only add a small optional badge when a ticker was promoted recently:

```
NEW 昇格
```

Condition:
- promotion occurred within last 24 hours

This badge must come from history/state, not from localStorage.

## 10. Cross-device

Because history is stored in Sites DB:

- PC and iPad must show the same history
- localStorage is not authoritative

## 11. Backup

Extend WATCHLIST backup JSON to optionally include:

```
status_history
status_state
```

Keep existing watchlist_items export.

No secrets.

## 12. Retention

Keep at most the latest 1000 history rows.

If above 1000:
- delete oldest rows only after a successful insert/reconciliation
- never delete current state rows

## 13. Delete behavior

Deleting a WATCHLIST item:

- delete from watchlist_items
- delete its watchlist_status_state row
- keep history rows for audit by default

History should show that the ticker was formerly tracked.

Optional later cleanup may be separate.

## 14. Re-add behavior

If a previously deleted ticker is re-added:

- state starts fresh from current status
- old history remains historical
- do not treat the first state after re-add as a promotion event unless explicitly desired later

## 15. No Render

All normal Version 16 operations must keep:

- Render request count = 0

This includes:
- history reconciliation
- history page
- filters
- current status age
- backup export

## 16. Tests

### Initial state
- 5 existing WATCHLIST items
- reconcile
- 5 state rows
- 0 fake history rows

### No change
- reconcile same Snapshot twice
- history count unchanged

### Promotion
- simulate WATCH -> ENTRY
- exactly 1 history row

### Promotion again
- ENTRY -> 理想本命
- exactly 1 additional row

### Downgrade
- 理想本命 -> WATCH
- history records downgrade

### Cross-device
- PC generates a history event
- iPad reload sees same event

### Delete/re-add
- delete ticker
- state removed, history retained
- re-add creates fresh state without fake transition

### Backup
- JSON contains watchlist_items + optional status_history/state

### Render
- request count stays 0

## 17. Save / deploy

Save as Version 16.

Keep private / 本人限定.

Do not modify Version 15.

Do not change:
- Render
- Google Sheets
- J-Quants
- Snapshot generation logic
- current promotion thresholds

Report:
- DB migration result
- files changed
- initial state row count
- history row count
- duplicate-prevention result
- filters
- TODAY recent-promotion badge
- backup export result
- Render request count
- Version 16 save/deploy result

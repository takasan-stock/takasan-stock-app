# Takasan Stock Hunter Sites v1 — Version 19 昇格通知・朝イチ通知

## Goal

Add a persistent in-Site notification center for the most actionable events only.

Version 19 must notify the user about:

1. WATCH -> ENTRY確認
2. ENTRY確認 -> 理想本命
3. any other priority increase into ENTRY確認 / 理想本命
4. one morning summary per market_date

Do not create notification noise.

Keep Version 18 morning mode, Version 17 priority scoring, Version 16 history, Version 15 Sites DB WATCHLIST, and Snapshot architecture unchanged.

## Important scope

Version 19 implements persistent Site notifications.

It does NOT implement:
- email sending
- browser push while the Site is closed
- background polling
- external SMS/LINE
- Render-based notification jobs

Those require a separate outbound notification transport.

Version 19 must remain fully usable with Render request count = 0.

## 1. Database schema

Add an idempotent migration.

### notification_events

Suggested semantics:

```sql
CREATE TABLE IF NOT EXISTS notification_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  market_date TEXT,
  ticker TEXT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  priority TEXT NOT NULL,
  status_from TEXT,
  status_to TEXT,
  today_priority_score INTEGER,
  created_at TEXT NOT NULL,
  read_at TEXT,
  dismissed_at TEXT
);
```

Supported event_type:

- `PROMOTION`
- `MORNING_SUMMARY`

Supported priority:

- `URGENT`
- `TODAY`
- `INFO`

### notification_settings

```sql
CREATE TABLE IF NOT EXISTS notification_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Defaults:
- promotion_notifications = true
- morning_summary = true
- notify_watch_to_entry = true
- notify_entry_to_ideal = true

Do not store secrets.

## 2. Event source

Promotion notifications must come only from Version 16 `watchlist_status_history`.

Do not infer promotion events from UI state alone.

Morning summary comes from Version 18 morning board + Version 17 priority ordering.

## 3. Promotion notification rules

Generate a promotion notification only when:

### Rule A
previous priority < new priority

AND

new status is:
- 🔥 ENTRY確認
- ⭐ 理想本命

### Rule B
Do not notify for:
- WATCH -> WAIT
- ENTRY -> WATCH
- ideal -> ENTRY
- no-data transitions
- unchanged state

### Priority

If new status == ⭐ 理想本命:
- notification priority = URGENT

If new status == 🔥 ENTRY確認:
- notification priority = URGENT

## 4. Promotion notification content

Examples:

### WATCH -> ENTRY

Title:
```
🔥 ENTRY確認へ昇格
```

Body:
```
9672 東京都競馬
WATCH → ENTRY確認
今日優先度 81
RR 7.01 / Rank S
次: Entry Hunterでタイミング確認
```

### ENTRY -> ideal

Title:
```
⭐ 理想本命へ昇格
```

Body:
```
5947 リンナイ
ENTRY確認 → 理想本命
今日優先度 94
RR 7.16 / Rank S
次: Entry Hunter / Pre-Tradeへ
```

Use deterministic fields.

Do not use generative AI text.

## 5. Promotion dedupe

Each Version 16 history transition must create at most one notification.

Recommended event_key:

```
promotion:<history_id>
```

If history_id is unavailable, use a deterministic key from:
- ticker
- previous_status
- new_status
- changed_at

Unique constraint must prevent duplicates across:
- page reload
- PC/iPad
- concurrent reconciliation

## 6. Morning summary notification

Create exactly one morning summary per market_date.

Event key:

```
morning:<market_date>
```

Generate when:
- Version 18 morning board is ready
- morning_summary setting is enabled
- no morning event exists for the market_date

Do not require a fixed wall-clock time.

This is an in-Site morning notification generated on first eligible Site load / Snapshot refresh for that market_date.

## 7. Morning summary content

Use:
- 🔴 count
- 🟠 count
- 🔵 count
- top candidate
- next candidate
- NEW promotion count

Example:

Title:
```
🌅 朝イチ確認
```

Body:
```
🔴 今すぐ確認 2件 / 🟠 今日中 12件
最優先: 5947 リンナイ（理想本命・84点）
次に見る: 5947 リンナイ
NEW昇格: 0件
```

If morning check is already complete:
- do not create another morning summary
- existing notification may show completion state separately in UI if desired

## 8. Notification center UI

Add a bell icon in the global header/sidebar.

Example:
```
🔔 2
```

Badge shows unread count.

Click opens:
```
通知
```

Show newest first.

Each notification card:
- unread marker
- priority
- title
- body
- created time
- ticker button if applicable
- CHART button if ticker applicable
- mark read
- dismiss

## 9. Notification filters

Add:
- 未読
- 全て
- 昇格
- 朝イチ

Keep UI compact.

## 10. Read state

Clicking a notification or "既読" sets `read_at`.

Read state is stored in Sites DB.

PC and iPad must share:
- unread count
- read state
- dismissed state

LocalStorage is not authoritative.

## 11. Dismiss

Dismiss sets `dismissed_at`.

Dismissed notifications:
- hidden from default list
- do not delete DB row by default
- may be visible under optional archived filter later

## 12. Notification retention

Keep latest 500 notifications.

After successful insert:
- if count > 500, delete oldest dismissed/read notifications first
- never delete unread notifications just to enforce the cap
- if still above cap, keep unread and newest read rows

## 13. Morning mode integration

Version 18 "朝イチ運用モード" remains the main workflow.

Notification center should link into it.

Morning summary notification action:
- `TODAYを開く`

Promotion notification action:
- `CHART`
- optional `TODAYで確認`

Do not auto-mark Version 18 review_state as CHECKED when notification is read.

## 14. TODAY integration

If there are unread urgent promotion notifications:

Show a compact banner near the top:

```
🔔 NEW昇格 1件
```

Click opens notification center filtered to promotion.

Do not duplicate large cards.

## 15. WATCHLIST integration

On WATCHLIST row that has an unread promotion notification:
- show small `🔔 NEW` badge

Badge disappears after all promotion notifications for that ticker are read/dismissed.

## 16. Settings UI

Add a compact notification settings section.

Toggles:
- 昇格通知
- 朝イチ通知
- WATCH→ENTRY通知
- ENTRY→理想本命通知

Defaults ON.

Changing a setting writes Sites DB only.

No Render.

## 17. Do not notify every Snapshot update

Snapshot update is not itself a notification.

Only:
- meaningful promotion transition
- one morning summary per market_date

creates notifications.

## 18. Existing history zero case

Current Version 16 history may be 0.

Version 19 initial deployment must NOT create fake promotion notifications from existing current state.

Only future real history transitions create promotion events.

Morning summary may be created for the current market_date.

## 19. Market-date behavior

Use Version 18 market_date logic.

Morning event is unique per market_date.

If the Snapshot market_date does not change:
- repeated Site opens do not create extra morning notifications

When market_date changes:
- one new morning summary is eligible

## 20. Notification generation order

On startup / Snapshot update:

1. load Snapshot
2. load Site DB WATCHLIST
3. reconcile Version 16 status history
4. recompute Version 17 priority
5. recompute Version 18 bands/review state
6. generate missing promotion notifications from new history rows
7. generate morning summary if absent
8. load notification center/unread count

This order is important.

## 21. Transaction / concurrency

Use DB uniqueness + transactions where possible.

Concurrent PC/iPad loads must not duplicate:
- promotion events
- morning summary

Treat unique-key conflict as already-created success.

## 22. No Render

All Version 19 operations must keep Render request count = 0:

- create notification
- read notification
- mark read
- dismiss
- settings
- unread count
- morning summary
- promotion event generation

## 23. iPad

Prioritize iPad landscape.

Requirements:
- bell + unread count visible
- notification drawer/modal fits width
- buttons >=44px
- body text readable
- no horizontal scroll

## 24. Mobile

On narrow screens:
- notification center becomes full-width sheet/page
- actions wrap

## 25. Backup

Extend WATCHLIST backup JSON optionally with:

- notification_settings
- notification_events (latest 100 or all under cap)

Do not export secrets.

## 26. Tests

### Initial deploy
- no fake promotion notifications
- one morning summary for current market_date if enabled

### Morning dedupe
- reload 10 times -> still one morning event

### Promotion
- simulate WATCH -> ENTRY
- one urgent notification
- unread count +1

### Promotion duplicate
- same history reconciled twice
- still one notification

### ENTRY -> ideal
- one urgent notification

### Downgrade
- no notification

### Read
- PC mark read
- iPad unread count reflects it

### Dismiss
- hidden from default list
- DB row remains

### Settings
- disable morning summary
- next market date creates none
- re-enable -> future market dates eligible

### Render
- request count = 0

## 27. Save / deploy

Save as Version 19.

Keep private / 本人限定.

Do not modify Version 18.

Report:
- files changed
- DB migration
- initial morning notification result
- promotion notification tests
- unread/read/dismiss tests
- dedupe tests
- cross-device state test
- iPad layout result
- Render request count
- Version 19 save/deploy result

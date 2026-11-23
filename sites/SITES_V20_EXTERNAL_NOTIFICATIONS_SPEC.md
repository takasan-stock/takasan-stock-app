# Takasan Stock Hunter Sites v1 — Version 20 外部通知（ChatGPT Push / Email）

## Goal

Deliver Version 19 notification events outside the Site by using ChatGPT Site Automations / Scheduled Tasks.

Primary external channels are the user's ChatGPT task notification settings:

- Push notification
- Email notification
- or both

Do not add a third-party mail provider in Version 20.

Keep:
- Version 19 in-Site notification center
- Version 18 morning mode
- Version 17 TODAY priority
- Version 16 promotion history
- Version 15 Sites DB WATCHLIST
- Snapshot-primary architecture
- normal Render request count = 0

## Why this transport

ChatGPT Sites supports linked cloud schedules when available.

A Site schedule can run in the cloud without the browser being open, provided the scheduled task can access the Site database and other required supported data connections.

Version 20 therefore uses Site DB + GitHub Snapshot + ChatGPT task notification delivery.

No API keys for email providers are needed.

## 1. External notification types

Only send externally:

### A. Promotion alert

From Version 19 `PROMOTION` notifications.

Allowed transitions:

- WATCH -> ENTRY確認
- ENTRY確認 -> 理想本命
- any other priority increase into ENTRY確認 or 理想本命

Do not externally send:
- downgrade
- unchanged state
- WAIT / no-data transitions

### B. Morning summary

One external morning summary per market_date.

Use the Version 19 `MORNING_SUMMARY` content / Version 18 morning board.

## 2. Database schema

Add an idempotent migration.

### external_notification_deliveries

Suggested semantics:

```sql
CREATE TABLE IF NOT EXISTS external_notification_deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL,
  channel TEXT NOT NULL,
  delivery_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  claimed_at TEXT,
  claim_expires_at TEXT,
  delivered_at TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Supported channel in Version 20:

```
CHATGPT_TASK
```

Supported status:

- PENDING
- CLAIMED
- DELIVERED
- FAILED

Do not store recipient email addresses here.

ChatGPT Push/Email destination is controlled by the user's ChatGPT notification settings.

### automation_run_log

```sql
CREATE TABLE IF NOT EXISTS automation_run_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_type TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  snapshot_generated_at TEXT,
  market_date TEXT,
  event_count INTEGER NOT NULL DEFAULT 0,
  result TEXT,
  error_summary TEXT
);
```

run_type:
- MORNING
- PROMOTION

Keep latest 200 run rows.

## 3. Delivery dedupe

One Version 19 event must be externally delivered at most once per channel.

Delivery key:

```
chatgpt:<notification_event_id>
```

or, if numeric event id is not stable:

```
chatgpt:<event_key>
```

Use a unique DB constraint.

PC/iPad/browser opens must not affect external-delivery dedupe.

Marking an in-Site notification read must NOT count as external delivery.

External delivery must NOT mark the Site notification as read.

These are separate states.

## 4. Claim / lease

Scheduled cloud tasks may overlap or retry.

Before returning an event for external delivery:

1. create or read its delivery row
2. if DELIVERED -> skip
3. if CLAIMED and claim_expires_at > now -> skip
4. otherwise claim it for 10 minutes
5. increment attempt_count
6. return it to the automation

If a previous claim expired:
- allow retry

This prevents duplicate external pushes from overlapping schedules.

## 5. Promotion cloud sweep

Create a reusable server-side Site automation action/function:

```
runPromotionNotificationSweep()
```

It must be runnable by a linked Site schedule without an interactive browser session.

Processing order:

1. fetch current GitHub Snapshot meta
2. fetch watch-context Snapshot
3. read Sites DB WATCHLIST
4. reconcile Version 16 status_state/history
5. recompute Version 17 priority
6. generate missing Version 19 PROMOTION notification_events
7. select externally-undelivered eligible promotion events
8. claim them
9. return a compact external digest
10. record automation_run_log

Do not require Render.

Do not require localStorage.

Do not require a visitor's connected-app permission.

## 6. Promotion external output

If no eligible new promotion:

Return a silent/no-notification result if the Site automation runtime supports it.

If the runtime always posts task results:
- return a minimal no-update status that does not sound like an alert

Preferred behavior: only produce user-facing notification when there is an eligible event.

If one event:

```
🔥 ENTRY確認へ昇格
9672 東京都競馬
今日優先度 81 / RR 7.01 / Rank S
次: Entry Hunterでタイミング確認
```

If multiple:
- headline with count
- show up to top 3 by Version 17 tier/score
- include `ほかN件` if more

Example:

```
🔔 昇格 2件
1. 5947 リンナイ — ⭐ 理想本命 / 94点
2. 9672 東京都競馬 — 🔥 ENTRY確認 / 81点
TODAYで確認してください
```

## 7. Promotion delivery completion

After the automation has successfully produced the notification result:

- mark claimed event deliveries DELIVERED
- set delivered_at

If the automation run fails before successful result:
- do not mark DELIVERED
- save last_error
- claim expires and a later run may retry

Do not create a second Version 19 notification event.

## 8. Morning cloud sweep

Create:

```
runMorningNotificationSweep()
```

It must also run without browser/localStorage.

Processing order:

1. load latest Snapshot
2. read Site DB WATCHLIST
3. reconcile Version 16 history
4. compute Version 17 priority
5. compute Version 18 morning bands
6. generate any missing Version 19 promotion notifications
7. ensure Version 19 morning:<market_date> event exists if enabled
8. claim undelivered morning event
9. ALSO catch any undelivered promotion events that were missed by the evening sweep
10. return morning notification payload
11. write automation_run_log

This morning sweep is the recovery path if the previous evening Snapshot was delayed.

## 9. Morning external content

Example:

```
🌅 朝イチ確認
🔴 今すぐ確認 2件 / 🟠 今日中 12件
最優先: 5947 リンナイ — ⭐ 理想本命 / 84点
次に見る: 5947 リンナイ
NEW昇格: 0件
```

If there are missed undelivered promotions, put them above the morning summary:

```
🔔 昨夜以降の昇格 1件
9672 東京都競馬 — 🔥 ENTRY確認
---
🌅 朝イチ確認
...
```

Mark each included event externally DELIVERED only after successful task result.

## 10. Recommended Site schedules

Create/link two Site Automations after Version 20 deploy.

### Schedule A — Morning

Name:

```
Takasan Stock Hunter 朝イチ通知
```

Time:

```
平日 08:30 JST
```

Action:

```
runMorningNotificationSweep()
```

Purpose:
- morning summary
- recovery delivery of any undelivered promotion alerts

### Schedule B — Evening promotion check

Name:

```
Takasan Stock Hunter 昇格通知
```

Time:

```
平日 20:30 JST
```

Action:

```
runPromotionNotificationSweep()
```

Reason for 20:30:
- the scanner updates after market close
- this avoids unnecessary intraday polling
- morning sweep catches delayed nightly updates if needed

Do not run hourly in Version 20.

## 11. Schedule availability

If Site Automations are not available on the account/runtime:

- do not silently fall back to Render
- keep Version 19 in-Site notifications working
- show:
  `外部通知スケジュールはこの環境では利用できません`
- leave Version 20 delivery rows untouched/PENDING
- do not add a third-party service without explicit future approval

## 12. External notification settings

Extend Version 19 settings with:

- external_notifications = true
- external_promotion = true
- external_morning = true

Keep existing internal notification settings.

If external_notifications = false:
- automation sweep may run
- it must not claim/send events

If promotion disabled:
- no external promotion delivery

If morning disabled:
- no external morning delivery

Internal Version 19 events may still exist according to internal settings.

## 13. User-facing settings UI

Add a section:

```
外部通知
```

Show:

- 外部通知 ON/OFF
- 昇格通知 ON/OFF
- 朝イチ通知 ON/OFF
- 配信方式: ChatGPT通知
- 朝イチ予定: 平日 08:30
- 昇格確認予定: 平日 20:30
- 最終Automation実行
- 最終外部通知

Also show guidance:

```
Push / Email の受信方法は ChatGPT の「設定 > 通知」で選択します。
```

Do not store ChatGPT account notification preferences in Site DB.

## 14. External delivery status in notification center

For each notification optionally show:

- 外部通知済み
- 外部通知待ち
- 外部通知失敗

This comes from external_notification_deliveries.

Do not confuse:
- read/unread
with
- external delivered/not delivered

## 15. Manual test-send

Add an owner-only:

```
外部通知テスト
```

This must create a TEST delivery result without mutating promotion history.

Do NOT create a fake PROMOTION notification_event.

If Site automation runtime cannot trigger a ChatGPT task on demand:
- provide a safe diagnostic preview instead
- do not claim a real external delivery occurred

## 16. No secrets

Version 20 must not require:

- RESEND_API_KEY
- SMTP password
- Gmail password
- SendGrid key
- recipient email stored in Site

ChatGPT task notification settings are external to the Site.

## 17. No Render

All Version 20 automation sweeps and delivery bookkeeping must keep:

- Render request count = 0

Use:
- Site DB
- GitHub Snapshot
- Site schedule runtime

only.

## 18. Scanner timing

Current market Snapshot generation occurs after the market close, not intraday.

Therefore Version 20 must NOT promise real-time intraday promotion notifications.

Label the setting/help text accurately:

```
昇格通知は最新Snapshot更新後に配信されます。
```

Morning summary uses the latest available Snapshot.

## 19. Notification timing semantics

Promotion changed_at may represent reconciliation time.

External message should include:
- Snapshot date/time
- not claim exact market-event second precision

Example:

```
Snapshot: 2026-11-23 18:xx JST
```

## 20. Failure handling

If GitHub Snapshot fetch fails:
- do not alter status history
- do not create promotion notifications
- do not mark delivery
- log FAILED automation run

If Site DB read fails:
- same fail-closed behavior

If one notification payload fails formatting:
- skip it
- keep delivery retryable
- continue safely if possible

## 21. Idempotency tests

### Morning
- run 10 times same market_date
- one morning event
- one external delivery

### Promotion
- one history transition
- one internal notification
- one external delivery

### Overlap
- morning and evening automation overlap
- unique delivery prevents duplicate

### Expired claim
- claim then simulate crash
- after 10 minutes event can retry

## 22. Cross-device

External delivery state is Site DB-backed.

PC/iPad should show the same:
- 外部通知済み
- waiting/failed state

No localStorage authority.

## 23. ChatGPT notification setup

After schedules are created, instruct the owner to verify:

```
ChatGPT > 設定 > 通知
```

and enable:
- Push
- Email
- or both

Mobile Push requires ChatGPT notification permission on the supported mobile app.

Do not claim the Site itself controls those global settings.

## 24. Acceptance

Verify:

1. Version 19 internal center unchanged
2. no fake promotion event on deploy
3. morning sweep creates/uses one morning event
4. promotion sweep creates no duplicate
5. external delivery unique
6. delivered state separate from read state
7. morning sweep catches missed promotion
8. no Render
9. no third-party email secret
10. schedule runtime can access Site DB
11. automation_run_log records successful/failed runs
12. external settings persist
13. iPad shows delivery state
14. normal Site usage remains unchanged

## 25. Save / deploy

Save as Version 20.

Keep private / 本人限定.

Do not modify Version 19.

Do not create schedules without owner confirmation if the Sites UI/runtime requires explicit confirmation.

After code deploy, report:
- files changed
- DB migration
- external delivery model
- automation cloud-access verification
- morning sweep result
- promotion sweep result
- schedule creation status
- external settings
- Render request count
- Version 20 save/deploy result

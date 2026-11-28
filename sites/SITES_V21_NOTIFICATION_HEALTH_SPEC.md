# Takasan Stock Hunter Sites v1 — Version 21 通知ヘルスモニター

## Goal
Add an owner-facing notification health monitor so the user can immediately tell whether external notifications are healthy.

Version 21 is observability only.

Do not change Version 20.1 automation authorization, notification eligibility, delivery dedupe, claim/receipt protocol, morning/promotion schedules, WATCHLIST, promotion thresholds, TODAY scoring, or Render policy.

## Health summary
Add a compact section named `外部通知ステータス`.

Show separate cards for MORNING and PROMOTION:
- status: 正常 / 注意 / エラー / 未確認
- schedule
- automation verified
- last run
- last result
- last external delivered
- next expected run
- pending claim count

Schedules:
- MORNING: weekdays 05:30 JST
- PROMOTION: weekdays 19:00 JST

## Status rules

### 正常
All true:
- registry enabled
- verified_at exists
- most recent expected run completed successfully
- no non-expired stuck claim
- no recent FAILED automation_run_log

PROMOTION `NO_UPDATE` is healthy.

### 注意
Examples:
- latest expected run is still within a 15 minute grace window
- delivery is CLAIMED and not expired
- result exists but delivery receipt is pending
- latest run is old because weekend/non-scheduled day

### エラー
Examples:
- registry disabled
- verified_at missing after an expected scheduled run
- latest expected run failed
- expired CLAIMED delivery remains unresolved
- repeated runtime errors

### 未確認
Not enough history yet.

## Expected-run logic
Use JST and a 15 minute grace window after the scheduled time.
Do not expect runs on Saturday/Sunday.
If no exchange-holiday source exists, treat weekdays as expected schedule days and label this `平日スケジュール基準`.

## Human-readable result labels
- DELIVERED -> 通知済み
- NO_UPDATE -> 新しい昇格なし
- CLAIMED -> 配信処理中
- FAILED -> 実行失敗
- generated morning payload with pending receipt -> 結果生成済み・配信確認待ち
- no run -> 実行履歴なし

Do not label PROMOTION NO_UPDATE as failure.

## Runtime verification
Read Version 20.1 role-specific registry/status independently for MORNING and PROMOTION.

## Last run
Use automation_run_log. Show started_at, completed_at, result, event_count, and error_summary in JST.

## Last external delivery
Use external_notification_deliveries. Show delivered_at, status, event type, ticker when applicable.
If none: `配信済み履歴なし`.

## Pending/stuck claims
Count active and expired CLAIMED rows separately.
If expired > 0, health must be エラー.
Viewing health must not mutate claims.

## Diagnostic preview
Keep existing preview/test behavior and add `診断プレビュー` showing current morning payload preview, runtime availability, and registry verification.
It must not claim delivery, mark DELIVERED, or create fake promotion history.

## Owner-only details
Add collapsible `詳細診断` showing:
- role
- automation_id
- enabled
- verified_at
- latest run id/result
- pending claims
- latest error
- Snapshot timestamp

Never show credentials, service tokens, or secrets.

## Overall badge
Notification center header should show one:
- 通知 正常
- 通知 注意
- 通知 エラー

Overall is error if either role is error, warning if no error but either role warning/unverified, healthy if both healthy.

## Recent run list
Add `直近のAutomation実行` latest 10, newest first:
- JST date/time
- MORNING/PROMOTION
- result
- event_count

## Important wording
Distinguish:
1. Automation実行
2. Site payload生成
3. ChatGPT task result
4. Push/Email device receipt

Version 21 may confirm 1-3 only.
Use:
`外部通知済み = ChatGPTタスク結果として正常生成・照合済み。Push/Email端末到着はChatGPT通知設定に依存。`

## No polling / no Render
Do not add background browser polling, hourly tasks, or Render checks.
Load health only with normal Site view/refresh.
Render request count must remain 0.

## iPad
Two role cards may be side-by-side on iPad landscape. No horizontal scroll. Details accordion may stack.

## Acceptance tests
1. both roles verified -> not UNVERIFIED
2. PROMOTION NO_UPDATE -> healthy
3. successful MORNING task -> healthy
4. missing verified_at after expected run -> error
5. active claim -> warning
6. expired claim -> error
7. failed run -> error
8. weekend no false missed-run error
9. role status independent
10. recent run list newest first
11. diagnostic preview does not mutate delivery
12. no secrets shown
13. Render 0

## Save / deploy
Save as Version 21, keep private / 本人限定, and do not modify Version 20.1 behavior.

Report files changed, health rules implementation, current MORNING health, current PROMOTION health, overall health, latest run list, pending/expired claims, iPad layout result, Render request count, and Version 21 save/deploy result.

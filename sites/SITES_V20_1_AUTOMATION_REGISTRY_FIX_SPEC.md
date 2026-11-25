# Takasan Stock Hunter Sites v1 — Version 20.1 Automation Registry Fix

## Goal

Fix Version 20 external notifications after real Site-created scheduled tasks were recreated.

Observed production facts:

- Old Version 20 automations are paused:
  - MORNING old: `Automation_09c928960b388191a559b5cbf5ef3b86`
  - PROMOTION old: `Automation_b342d33b97608191a85f38c6053562fe`
- New Site-created tasks are enabled:
  - MORNING new: `6b056526833881908d924c80ef961f54`
  - PROMOTION new: `6b0565a645888190b66e0338e3b3efdf`
- New schedule:
  - MORNING: weekdays 05:30 JST
  - PROMOTION: weekdays 19:00 JST
- Site-created tasks may have an empty internal Automation title even when the Sites UI shows the task by schedule.
- Version 20 instructions currently rely on resolving the task by exact title, which is not reliable for these Site-created tasks.
- Runtime currently reports only one registered automation_id and may therefore reject MORNING even when PROMOTION has already verified successfully.

Version 20.1 must fix task identity and authorization only.

Do not change:
- promotion logic
- Version 19 notification event logic
- Version 18 morning logic
- Version 17 priority scoring
- WATCHLIST / memo / history
- Snapshot generation
- Render usage

## 1. Add explicit role-based automation registry

Add an idempotent Site DB table:

```sql
CREATE TABLE IF NOT EXISTS external_automation_registry (
  role TEXT PRIMARY KEY,
  automation_id TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1,
  registered_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  revoked_at TEXT
);
```

Supported roles:

- MORNING
- PROMOTION

Exactly one active automation per role.

## 2. Register the new owner-approved task IDs

On Version 20.1 migration, register:

```
MORNING
6b056526833881908d924c80ef961f54
```

```
PROMOTION
6b0565a645888190b66e0338e3b3efdf
```

These are owner-approved Site-created tasks.

Do not infer or discover replacements automatically.

## 3. Revoke the old paused task IDs

The following are no longer authorized cloud callers:

```
Automation_09c928960b388191a85f38c6053562fe
Automation_b342d33b97608191a85f38c6053562fe
```

If an old registry/config row exists:
- mark revoked/inactive
- do not delete delivery history
- do not delete automation_run_log
- do not alter notification_events

Old paused tasks must fail authorization cleanly if they ever run again.

## 4. Remove title-based task resolution

Version 20 runtime instructions must no longer require:

```
Resolve your own task id by exact requested title
```

because Site-created Automations may expose a blank internal title.

Replace this with explicit role-based identity.

The scheduled task prompt must call the runtime with its known automation_id.

### MORNING

```
automation_id = 6b056526833881908d924c80ef961f54
role = MORNING
```

### PROMOTION

```
automation_id = 6b0565a645888190b66e0338e3b3efdf
role = PROMOTION
```

## 5. Runtime authorization

For `cloud_probe`, `morning_sweep`, `promotion_sweep`, and `ack`:

1. receive automation_id
2. determine requested role from operation
3. load external_automation_registry for that role
4. require:
   - enabled = true
   - revoked_at IS NULL
   - automation_id exact match
5. reject any other ID

Do not authorize based on:
- task title
- conversation title
- localStorage
- caller-provided role alone

## 6. Role separation

MORNING automation may call:
- instructions
- status
- pending_receipts where needed
- cloud_probe as MORNING
- morning_sweep
- ack only for claims issued to MORNING

PROMOTION automation may call:
- instructions
- status
- pending_receipts where needed
- cloud_probe as PROMOTION
- promotion_sweep
- ack only for claims issued to PROMOTION

A MORNING task must not be accepted as PROMOTION merely because it has valid Site access, and vice versa.

## 7. Runtime status must report both roles

Update status output from a single:

```
automation_id
```

to role-specific state:

```json
{
  "runtime": {
    "availability": "AVAILABLE",
    "automations": {
      "MORNING": {
        "automation_id": "6b056526833881908d924c80ef961f54",
        "verified_at": null,
        "enabled": true
      },
      "PROMOTION": {
        "automation_id": "6b0565a645888190b66e0338e3b3efdf",
        "verified_at": null,
        "enabled": true
      }
    }
  }
}
```

After each role successfully cloud-probes, store role-specific verified_at.

Overall runtime may be:
- AVAILABLE when both are verified
- PARTIAL when only one is verified
- UNVERIFIED when neither is verified

Do not let successful PROMOTION verification falsely imply MORNING is verified.

## 8. Cloud probe

Each new task must cloud-probe itself using its explicit automation_id.

### MORNING expected

```
cloud_probe(
  automation_id="6b056526833881908d924c80ef961f54"
)
```

### PROMOTION expected

```
cloud_probe(
  automation_id="6b0565a645888190b66e0338e3b3efdf"
)
```

A successful probe updates only that role's verified_at.

## 9. New task prompt contract

The Site/runtime instructions should explicitly tell the cloud task:

### MORNING

```
Use automation_id 6b056526833881908d924c80ef961f54.
Do not search by title.
Run MORNING cloud_probe and morning_sweep only.
```

### PROMOTION

```
Use automation_id 6b0565a645888190b66e0338e3b3efdf.
Do not search by title.
Run PROMOTION cloud_probe and promotion_sweep only.
```

## 10. Scheduling display

Update user-facing settings/help to the actual schedules:

```
朝イチ予定: 平日 05:30
昇格確認予定: 平日 19:00
```

Do not show the old Version 20 recommended times 08:30 / 20:30.

## 11. Morning behavior

MORNING remains:

- one morning summary per market_date
- recovery of undelivered promotion events
- no fake promotion events
- no duplicate delivery

The schedule is now 05:30 JST.

If the latest Snapshot is from the prior market day, still use the latest available Snapshot and state its market date accurately.

## 12. Promotion behavior

PROMOTION remains:

- only real promotion events
- if no eligible event: NO_UPDATE / silent
- schedule 19:00 JST

Do not emit a failure notification simply because there are zero promotions.

## 13. Delivery / receipt compatibility

Keep Version 20:
- claim lease
- delivery_key uniqueness
- receipt protocol
- DELIVERED only after verified completed task result

Migration must not reset existing delivery rows.

If an old task has a pending claim:
- allow lease to expire normally
- new authorized task may retry after expiry
- do not transfer a live claim between automation IDs

## 14. Existing successful PROMOTION run

A production PROMOTION run already succeeded with:

- run_type PROMOTION
- result NO_UPDATE
- event_count 0

Preserve that run log.

Version 20.1 must not fabricate a notification from it.

## 15. Initial verification after deploy

After Version 20.1 deploy:

### MORNING
Run a safe cloud-linked verification from the new MORNING task:
- cloud_probe with new MORNING id
- morning sweep only if actual scheduled/cloud execution
- no manual authoring-session claim

### PROMOTION
Run cloud_probe with new PROMOTION id.

Expected final registry:

```
MORNING     6b056526833881908d924c80ef961f54   verified
PROMOTION   6b0565a645888190b66e0338e3b3efdf   verified
```

## 16. Do not auto-pause tasks

Runtime failure must not automatically disable/pause either Site schedule.

Return a clear failure result but leave schedule enabled.

Task lifecycle is controlled by the owner / Sites schedule UI.

## 17. No Render

Keep Render request count = 0.

Use only:
- Site DB
- GitHub Snapshot
- linked Site cloud task runtime

## 18. Acceptance tests

1. registry has exactly 2 active roles
2. new MORNING ID accepted for MORNING
3. new PROMOTION ID accepted for PROMOTION
4. old MORNING ID rejected
5. old PROMOTION ID rejected
6. MORNING ID rejected for PROMOTION operation
7. PROMOTION ID rejected for MORNING operation
8. no title lookup anywhere in runtime authorization path
9. MORNING cloud_probe can verify independently
10. PROMOTION cloud_probe can verify independently
11. status reports both role IDs and verification times
12. same morning market_date still sends at most once
13. promotion no-update stays silent/no-update
14. no delivery/history reset
15. Render request count 0
16. schedules remain enabled after a failed run

## 19. Save / deploy

Save as Version 20.1 or the next available saved version.

Keep private / 本人限定.

Do not modify Version 20 behavior beyond task identity/authorization and displayed schedule times.

Report:
- files changed
- registry migration
- old IDs revoked
- new IDs registered
- MORNING authorization test
- PROMOTION authorization test
- cross-role rejection test
- title lookup removal
- status output
- schedule display 05:30 / 19:00
- Render request count
- save/deploy result

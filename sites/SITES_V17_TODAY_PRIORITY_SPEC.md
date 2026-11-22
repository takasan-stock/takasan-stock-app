# Takasan Stock Hunter Sites v1 — Version 17 TODAY Priority Engine

## Goal

Improve TODAY ACTION QUEUE so it answers:

- 今日、最初に何を見るか
- なぜその銘柄が上なのか
- 次に何をするか

Keep Version 16 promotion/history logic unchanged.

This is an explainable prioritization layer only.

## Existing architecture to preserve

Market data:
- GitHub Snapshot
- J-Quants Snapshot
- full watch-context Snapshot

Private state:
- Sites DB WATCHLIST
- memo
- status_state
- status_history

Render:
- normal usage request count = 0

Do not change any promotion thresholds.

## 1. Candidate pool

TODAY candidate pool is built from:

### A. WATCHLIST candidates
Site DB WATCHLIST rows enriched by current watch-context.

Include:
- ⭐ 理想本命
- 🔥 ENTRY確認
- 👀 WATCH

Exclude:
- ⏸ WAIT by default
- — データなし

### B. EARLY candidates not already in WATCHLIST
From current EARLY Snapshot.

Include:
- Rank S or A
- or current ideal condition

Do not duplicate a ticker already in WATCHLIST.

## 2. Recent promotion

Read Version 16 history/state.

A ticker is `NEW 昇格` when:
- latest transition is a promotion
- changed_at is within the last 24 hours

Status priority:

```
⭐ 理想本命 = 4
🔥 ENTRY確認 = 3
👀 WATCH = 2
⏸ WAIT = 1
— データなし = 0
```

Promotion means new priority > previous priority.

Do not create a NEW badge from browser localStorage alone.

## 3. TODAY tier

Assign a deterministic tier before numeric scoring.

Lower tier number = higher priority.

```
Tier 0: NEW昇格 to ⭐ 理想本命 or 🔥 ENTRY確認
Tier 1: ⭐ 理想本命
Tier 2: 🔥 ENTRY確認
Tier 3: WATCHLIST外のEARLY Rank S/A候補
Tier 4: 👀 WATCH
Tier 5: other eligible candidates
```

Tier always wins over numeric score.

Example:
- Tier 1 score 70 stays above Tier 2 score 99.

This prevents a weaker status from jumping above a more actionable status.

## 4. TODAY priority score

Within the same tier, calculate `今日優先度` from 0 to 100.

Do not store it in DB; calculate from current Snapshot/history each time.

### Initial move score — max 35

Use `初動優先スコア`.

```
component = clamp(初動優先スコア, 0, 100) * 0.35
```

### RR — max 20

```
RR >= 8.0  -> 20
RR >= 6.0  -> 17
RR >= 4.0  -> 14
RR >= 3.0  -> 10
RR >= 2.5  -> 7
else        -> 0
```

### Freshness — max 15

Use `初動鮮度週`.

```
<= 2 weeks -> 15
3 weeks    -> 12
4 weeks    -> 9
5-6 weeks  -> 5
7-8 weeks  -> 2
> 8 / 999  -> 0
```

### Follow-through room — max 10

Use `追撃余地`.

```
contains "◎" -> 10
contains "○" -> 6
contains "△" -> 2
else         -> 0
```

### Rank quality — max 10

```
S -> 10
A -> 7
B -> 4
C -> 2
D -> 0
```

### Recent promotion bonus — max 10

```
NEW昇格 within 24h -> 10
else                -> 0
```

Final:

```
today_priority_score =
  initial_component
  + rr_component
  + freshness_component
  + follow_through_component
  + rank_component
  + promotion_component
```

Round to nearest integer.

Clamp to 0-100.

## 5. Sorting

Sort by:

1. TODAY tier ascending
2. today_priority_score descending
3. 初動優先スコア descending
4. RR descending
5. ticker ascending

Take top 6 for TODAY ACTION QUEUE.

Keep top 6 to preserve iPad usability.

## 6. Explainability

For every queue row generate `優先理由`.

Show at most 3 concise reasons.

Examples:

- `NEW昇格 / 初動92.8 / RR7.16`
- `理想本命 / 鮮度2週 / 追撃余地◎`
- `ENTRY確認 / RR7.01 / Rank S`
- `EARLY S / 初動93.5 / 鮮度1週`

Do not show opaque AI-generated reasons.

Reasons must be deterministic from the same fields used in scoring.

## 7. TODAY row UI

Each row should show:

- queue rank number
- status badge
- NEW昇格 badge if applicable
- ticker + name
- 今日優先度 score
- 初動
- Rank
- RR
- 優先理由
- next action
- WATCHLIST / CHART buttons

Recommended display:

```
#1  ⭐ 理想本命   NEW 昇格
5947 リンナイ
今日優先度 92
初動 92.8 / Rank S / RR 7.16
理由: NEW昇格 / 鮮度2週 / 追撃余地◎
次: Entry Hunter / Pre-Tradeへ
```

## 8. Queue summary

Above the queue, add a compact summary:

```
今日の最優先
#1 5947 リンナイ
⭐ 理想本命 / 今日優先度 92
```

If there is a recent promotion:

```
NEW昇格 1件
```

If no candidates:
- keep existing empty-state guidance

## 9. KPI behavior

Keep existing KPI cards:

- ⭐ 理想本命
- 🔥 ENTRY確認
- 🌱 EARLY TOP20
- 🏆 総合候補

Optionally add a small NEW昇格 count near TODAY, not as a fifth large KPI card.

Do not overcrowd iPad layout.

## 10. WATCHLIST integration

If candidate is in Site DB WATCHLIST:
- show registered state
- no external sync
- no Render

If not in WATCHLIST:
- allow Site DB add directly
- after add, recompute queue immediately
- avoid duplicate row

## 11. CHART integration

CHART button must open the exact selected ticker.

Keep Snapshot chart behavior.

No Render.

## 12. Snapshot refresh

On Snapshot更新:

1. refresh meta/summary/early/watch-context/chart metadata as Version 15
2. reconcile Version 16 status history
3. recompute recent promotion badges
4. recompute TODAY tiers/scores
5. rerender queue

Do not write TODAY priority score to DB.

## 13. Missing values

Missing values must not become artificial positives.

- missing 初動 -> 0 component
- missing RR -> 0
- missing freshness -> 0
- missing 追撃余地 -> 0
- missing rank -> 0

Do not treat unknown as zero quality in status derivation; only in priority-score component.

Promotion/status logic remains separate.

## 14. Stability

Do not modify:
- promotion thresholds
- ENTRY CHECK thresholds
- WATCHLIST DB schema except Version 16 history tables already present
- J-Quants Snapshot generation
- Render behavior

No new DB migration is required for TODAY score.

## 15. iPad / responsive

Maintain:
- one-row compact action cards on wide/iPad landscape when possible
- no clipped buttons
- horizontal overflow avoided
- score/reason may wrap under identity on narrower widths

On mobile:
- priority score and reason remain readable
- buttons remain tappable

## 16. Acceptance examples

Using current data, verify deterministic ordering.

For 5947:
- status ⭐ 理想本命
- 初動 92.8
- Rank S
- RR 7.16
- freshness 2
- 追撃余地 ◎

Expected components:
- initial ~32
- RR 17
- freshness 15
- follow-through 10
- rank 10
- promotion 0 unless recent history

Expected score around 84 without NEW promotion.

If 5947 gets a recent promotion event:
- +10
- score around 94
- Tier 0

For 9672:
- status 🔥 ENTRY確認
- initial 91.8
- Rank S
- RR 7.01
- freshness 3
- 追撃余地 ◎

Expected score around:
- 32 + 17 + 12 + 10 + 10 = 81
- Tier 2 unless NEW promotion

WATCH rows with Rank D should stay below ENTRY/ideal due tiering even if RR is high.

## 17. Tests

### Determinism
Same data -> same tier/score/order.

### No-history case
history 0 -> no NEW badges.

### New promotion
simulate WATCH -> ENTRY within 24h:
- NEW badge appears
- Tier 0
- +10 score

### Expired promotion
changed_at >24h:
- no NEW badge
- normal tier

### Missing data
no RR/freshness:
- component 0
- no crash

### Add WATCHLIST
EARLY candidate added to Site DB:
- becomes WATCHLIST-backed candidate
- no duplicate
- queue recomputed

### Snapshot update
status changes:
- Version 16 records transition
- Version 17 queue updates

### Render
all normal TODAY operations -> Render request count 0

## 18. Save / deploy

Save as Version 17.

Keep private / 本人限定.

Do not modify Version 16.

Report:
- files changed
- scoring implementation
- current top 6
- current top 1 and reasons
- NEW promotion count
- deterministic-order tests
- iPad layout verification
- Render request count
- Version 17 save/deploy result

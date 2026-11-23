# Takasan Stock Hunter Sites v1 — Version 18 朝イチ運用モード

## Goal

Turn Version 17 TODAY priority into a practical morning operating screen.

The user should be able to answer, at a glance:

1. 今すぐ見る銘柄はどれか
2. 今日中に見る銘柄はどれか
3. 継続監視だけでよい銘柄はどれか
4. 朝の確認がどこまで終わったか

Keep Version 17 scoring, Version 16 history, Version 15 Site DB WATCHLIST, and Snapshot architecture unchanged.

This version is an operational layer only.

## Existing architecture to preserve

Market/public:
- J-Quants Snapshot
- GitHub summary / EARLY / watch-context / chart Snapshot

Private:
- Sites DB WATCHLIST
- memo
- status_state
- status_history

Render:
- normal operations = 0 requests

Do not change:
- promotion thresholds
- ENTRY CHECK thresholds
- Version 17 priority score formula
- Version 17 Tier definitions

## 1. Morning action bands

Map Version 17 candidates into three action bands.

### 🔴 今すぐ確認

Include:

- Tier 0 NEW昇格
- Tier 1 ⭐ 理想本命
- Tier 2 🔥 ENTRY確認

These are the highest-action candidates.

### 🟠 今日中

Include:

- Tier 3 WATCHLIST外 EARLY Rank S/A candidates

These require chart review / WATCHLIST consideration today.

### 🔵 継続監視

Include:

- Tier 4 👀 WATCH

These are not immediate entry candidates but should remain visible.

Do not mix WAIT / データなし into the default morning board.

## 2. Ordering inside each band

Use Version 17 deterministic order:

1. Tier ascending
2. 今日優先度 descending
3. 初動優先スコア descending
4. RR descending
5. ticker ascending

Do not define a new score.

## 3. Morning summary header

Replace the duplicated Version 17 top summary with a compact operational summary.

Recommended layout:

```
朝イチ運用モード

🔴 今すぐ確認 2
🟠 今日中 4
🔵 継続監視 3
✅ 確認済み 0
```

Below:

```
最優先
#1 5947 リンナイ
⭐ 理想本命 / 今日優先度84
Entry Hunter / Pre-Tradeへ
```

Avoid repeating the full #1 card twice.

## 4. Default morning board

Show three sections in order:

1. 🔴 今すぐ確認
2. 🟠 今日中
3. 🔵 継続監視

Default collapsed behavior:

- 🔴 expanded
- 🟠 expanded
- 🔵 collapsed on iPad/mobile if space is tight

Desktop may keep all expanded.

## 5. Maximum visible candidates

Morning focus must stay compact.

Recommended defaults:

- 🔴 up to 4
- 🟠 up to 4
- 🔵 up to 4

If more exist:
- show `+N件を表示`

Do not make the morning screen an endless ranking list.

Version 17 Top6 can remain available in a secondary "優先順位一覧" view if useful.

## 6. Row content

Each candidate row/card should show:

- action band
- Version 17 queue rank if available
- status
- NEW昇格 if applicable
- ticker / name
- 今日優先度
- 初動 / Rank / RR
- 優先理由
- 次アクション
- WATCHLIST state
- CHART button
- 朝の確認 state

Keep reason to max 3 concise reasons.

## 7. Morning review state

Add persistent Site DB state so PC and iPad share morning progress.

### table: today_review_state

Suggested semantics:

```sql
CREATE TABLE IF NOT EXISTS today_review_state (
  market_date TEXT NOT NULL,
  ticker TEXT NOT NULL,
  review_state TEXT NOT NULL,
  checked_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (market_date, ticker)
);
```

Allowed review_state:

- `PENDING`
- `CHECKED`
- `LATER`

Do not store market-derived fields in this table.

## 8. Market date

Use the Snapshot/market data date, not device-local wall-clock date alone.

Prefer:

1. latest Snapshot market date if available
2. fallback to Snapshot generated_at JST date

This avoids resetting morning progress incorrectly across midnight / stale Snapshot cases.

## 9. Review controls

For each candidate provide:

- `確認済み`
- `あとで`

Behavior:

### 確認済み
- save CHECKED in Site DB
- visually de-emphasize row
- keep row accessible
- increment ✅ 確認済み count

### あとで
- save LATER
- keep candidate visible
- show small `あとで` badge
- do not remove from its action band

Provide `未確認に戻す` from row menu or toggle.

## 10. Daily reset behavior

Do not delete old rows every day.

Review state is keyed by market_date.

When new Snapshot market_date arrives:
- new day's candidates start PENDING
- prior day's review rows remain historical data

Retention:
- keep last 30 market dates
- older review rows may be deleted safely

## 11. Morning completion indicator

Show:

```
朝チェック 3 / 6 完了
```

Count only candidates in 🔴 + 🟠 by default.

🔵 継続監視 is not required for morning completion.

Completion %:

```
checked_immediate_and_today / total_immediate_and_today
```

If all are checked:

```
✅ 朝チェック完了
```

## 12. "次に見る1銘柄"

Add a compact next-action helper.

Choose the first candidate in Version 17 order among:

- 🔴 PENDING first
- then 🟠 PENDING
- ignore CHECKED
- LATER comes after PENDING within the same band

Display:

```
次に見る
9672 東京都競馬
🔥 ENTRY確認 / 今日優先度81
CHART
```

This must update immediately after marking a row checked.

## 13. NEW promotion handling

Version 16 history remains authoritative.

If NEW昇格 within 24h:
- candidate remains Tier 0
- show `NEW 昇格`
- keep in 🔴 even if score is lower than another Tier1/2 candidate

Do not auto-mark NEW promotion as checked.

## 14. WATCHLIST add behavior

For 🟠 EARLY candidates not yet in WATCHLIST:

- allow Site DB WATCHLIST add
- after add:
  - recompute promotion status
  - recompute Version 17 tier/score
  - move candidate to appropriate band immediately
  - preserve review state for the same market_date/ticker

No Render.

## 15. CHART flow

CHART opens exact ticker using Snapshot chart.

When returning to TODAY:
- preserve scroll/section if practical
- preserve morning review state from Site DB

Do not auto-mark checked merely because CHART was opened.

## 16. Optional quick flow

Add one compact action:

```
朝イチ順に見る
```

Behavior:
- opens the first PENDING candidate's CHART
- after user returns, next PENDING candidate remains suggested

Do not automatically navigate through all tickers without user action.

## 17. Status language

Use clear labels:

- 🔴 今すぐ確認
- 🟠 今日中
- 🔵 継続監視
- ✅ 確認済み
- ⏰ あとで

Avoid ambiguous labels like "high/medium/low".

## 18. iPad landscape

Prioritize iPad landscape.

Requirements:

- summary counters fit in one row or clean 2x2 wrap
- 🔴 candidate cards fully readable
- CHART / 確認済み / あとで buttons >= 44px tap target
- no horizontal page scroll
- reason/action text may wrap
- collapsed 🔵 section should reduce vertical length

## 19. Mobile

On narrow screens:

- one candidate card per row
- action controls wrap under metrics
- action band label remains visible
- next-candidate helper stays near top

## 20. Accessibility / clarity

- color is not the only signal
- always include text label with 🔴/🟠/🔵
- checked rows keep readable contrast
- disabled states remain legible

## 21. No notification yet

Version 18 does NOT send push/email notifications.

It only prepares the actionable states that a later notification version may use.

Do not add background polling or scheduled external requests.

## 22. No Render

All Version 18 normal operations:

- load morning board
- review state read/write
- completion status
- next candidate
- add WATCHLIST
- filters
- CHART open

must keep Render request count = 0.

## 23. Tests

### Band mapping
- Tier0/1/2 -> 🔴
- Tier3 -> 🟠
- Tier4 -> 🔵

### Ordering
- same Version17 ordering inside each band

### Review state
- mark CHECKED
- reload page
- remains checked
- iPad sees same state

### LATER
- LATER remains visible
- PENDING sorts before LATER within same band

### New market date
- new market date -> candidates PENDING
- previous date remains historical

### Completion
- only 🔴+🟠 count
- all checked -> 朝チェック完了

### Next candidate
- skips CHECKED
- PENDING before LATER
- updates immediately

### WATCHLIST add
- EARLY candidate added
- band/tier recalculated
- no duplicate
- review state preserved

### NEW promotion
- Tier0 -> 🔴
- remains un-checked until user action

### Cross-device
- PC CHECKED
- iPad reload -> same CHECKED

### Render
- request count = 0

## 24. Save / deploy

Save as Version 18.

Keep private / 本人限定.

Do not modify Version 17.

Report:
- files changed
- DB migration
- current band counts
- current top/next candidate
- review-state tests
- cross-device test if practical
- iPad layout result
- Render request count
- Version 18 save/deploy result

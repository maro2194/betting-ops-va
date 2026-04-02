# Expload (JSON Upload) Improvements — From Shadow & Diji Feedback

## 1. Checkbox Selection per Bet (Shadow)
**What**: Add tick boxes next to each bet row so users can deselect successful ones, swap account queue, and re-run only selected bets.
**Why**: After a batch, you might want to retry specific failed bets on different accounts, or skip ones that placed fine.
**Where**: `frontend/src/pages/JsonUpload.jsx` — add checkbox column to bets table, filter `handlePlaceAll` to only place checked bets.

## 2. Timestamp on Bet History (Shadow)
**What**: Show when each bet was placed in the History page, so you can tell which bets are from which batch.
**Why**: Currently hard to distinguish batches placed minutes apart.
**Where**: `frontend/src/pages/History.jsx` — already has `placed_at` from DB, just needs formatting in the table.

## 3. Liability Limit + Auto-Split Across Accounts (Shadow)
**What**: When a bet's liability (= (odds-1) × stake) exceeds a max cap (e.g. $600), auto-split the bet across multiple accounts so each stays under the cap.
**Why**: Single large bets draw attention from bookmakers. Splitting across accounts keeps each bet's payout under the radar.

### Shadow's Approach (from Google Sheets script):
- **Inputs**: `maxLiability` (e.g. $600), `unitSize` (e.g. $10), `roundingIncrement` ($10)
- **Logic**:
  1. Calculate total stake = units × unitSize
  2. Calculate max stake per bet: `maxStakeForLiability = maxLiability / (odds - 1)`, rounded down to nearest $10
  3. Calculate min bets needed: `ceil(totalStake / maxStakeForLiability)`
  4. Distribute evenly: base = `floor(totalStake / numBets / $10) * $10`, distribute remainder in $10 increments to first bets
  5. Example: $330 total across 3 accounts → $110, $110, $110 (not $106.67 × 3)

### Adapted for BotOps:
- Split by number of **enabled accounts** available
- If bet needs 3 splits but only 2 accounts → split across 2 (higher per-account liability, user's choice)
- Each split becomes a separate placement in the queue
- Color-code split bets in the UI so you know which ones belong together

### Config (user-configurable):
- `maxLiability`: default $600
- `roundingIncrement`: $10 (stakes rounded to nearest $10)
- Toggle: on/off (some users might not want splitting)

**Where**: 
- Backend: `main.py` `_resolve_json_bet` or new pre-processing step before `handlePlaceAll`
- Frontend: `JsonUpload.jsx` — add liability settings panel, split bets before placement loop

## Implementation Priority
1. **Checkboxes** — quick win, improves retry UX
2. **Timestamps in history** — trivial, just display formatting
3. **Liability splitting** — more complex, needs Shadow's input on exact params

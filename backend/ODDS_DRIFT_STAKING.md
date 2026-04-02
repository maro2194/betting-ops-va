# Odds Drift Staking — Implementation Plan

## Problem
When TAB odds are lower than the tipster's quoted odds, the EV shrinks. Betting full units at worse odds is -EV. Need proportional stake reduction.

## Proposed Formula (Option A — Profit Ratio)

```
If actual_odds >= tipped_odds:
    bet_units = full units (EV intact or better)

If actual_odds < tipped_odds AND actual_odds >= min_odds:
    bet_units = units * (actual_odds - 1) / (tipped_odds - 1)

If actual_odds < min_odds:
    skip (EV negative)
```

### Example
- Tipped: 7.00 odds, 1.5 units
- Actual: 6.50 odds
- bet_units = 1.5 * (6.50 - 1) / (7.00 - 1) = 1.5 * 5.5/6.0 = **1.375 units**

## Alternative Formula (Option B — Linear Min-to-Tipped)

```
ratio = (actual_odds - min_odds) / (tipped_odds - min_odds)
bet_units = units * ratio
```

This gives 100% at tipped odds, 0% at min odds, linear in between.

## Where to Implement
- `backend/csv_processor.py` or `backend/resolver.py` — wherever stake is calculated before placement
- CSB Upload page frontend — show adjusted units next to original units
- Configurable: user should be able to toggle this on/off and pick formula

## Status
Waiting for Shadow's input on preferred formula before implementing.

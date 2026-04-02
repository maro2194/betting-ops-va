# Sportsbet SGM Correlation Analysis — Brisbane v Collingwood

**Date:** 2026-04-02
**Match:** Brisbane Lions v Collingwood (Event 10294344)
**Market:** 20+ Disposals
**Combos Priced:** 990 (all 2-leg combinations)
**Pricing Endpoint:** POST /apigw/multi-pricer/combinations/price

## Key Findings

### 1. Same Team vs Cross Team
| | n | Avg Correlation | Above Fair |
|---|---|---|---|
| Same team | 484 | 0.928 (-7.2%) | 25% |
| Cross team | 506 | **1.226 (+22.6%)** | 66% |

**Cross-team combos pay 22.6% above fair. Same-team combos are discounted 7.2%.**

### 2. Position Combos (Cross Team Only — where the value is)
| Combo | n | Avg Corr | Edge |
|---|---|---|---|
| FWD+FWD | 36 | 1.551 | **+55.1%** |
| FWD+RUCK | 18 | 1.398 | **+39.8%** |
| FWD+MID | 114 | 1.329 | **+32.9%** |
| DEF+FWD | 66 | 1.241 | **+24.1%** |
| MID+RUCK | 27 | 1.165 | +16.5% |
| MID+MID | 88 | 1.140 | +14.0% |
| DEF+RUCK | 19 | 1.141 | +14.1% |
| DEF+MID | 112 | 1.116 | +11.6% |
| DEF+DEF | 24 | 1.056 | +5.6% |

### 3. By Odds Range
| Range | n | Avg Corr | Above Fair |
|---|---|---|---|
| Short (1-3) | 105 | 1.005 (+0.4%) | 51% |
| Mid (3-10) | 274 | **0.908 (-9.2%)** | 13% |
| Long (10-30) | 337 | 1.073 (+7.3%) | 53% |
| Very Long (30+) | 274 | **1.290 (+29.0%)** | 69% |

### 4. Same-Team Position (avoid these)
| Combo | Avg Corr |
|---|---|
| RUCK+RUCK | 0.659 (-34.1%) |
| DEF+RUCK | 0.791 (-20.9%) |
| FWD+RUCK | 0.797 (-20.3%) |
| MID+RUCK | 0.861 (-13.9%) |
| DEF+DEF | 0.903 (-9.7%) |
| DEF+MID | 0.907 (-9.3%) |

## Strategy

1. **Always cross teams** — SB pays +22.6% above fair for cross-team SGMs
2. **FWD+FWD cross team** is the golden combo (+55% above fair)
3. **Avoid same-team ruck combos** (-34% below fair)
4. **Longshot combos (30+) are overpriced** (+29% above fair)
5. **Mid-range (3-10) is the trap** — SB takes 9.2%, only 13% above fair

## TAB vs SB Comparison

| Metric | TAB | Sportsbet |
|---|---|---|
| Avg SGM correlation | +8.11% | +8.01% |
| Same-team | N/A (not tested) | -7.2% |
| Cross-team | N/A (not tested) | +22.6% |
| Best combo type | Safe + longshot | Cross-team FWD+FWD |

TAB and SB have nearly identical overall correlation (+8%), but SB's distribution is wildly different — heavily penalizing same-team and rewarding cross-team.

## Technical Details

**Endpoint:** `POST /apigw/multi-pricer/combinations/price`
**Auth:** accesstoken + apptoken headers
**Payload:**
```json
{
    "classExternalId": 103,
    "competitionExternalId": 17131,
    "eventExternalId": 15448758,
    "outcomesExternalIds": [
        {"marketExternalId": 566290744, "outcomeExternalId": 2703789586},
        {"marketExternalId": 566290744, "outcomeExternalId": 2703789590}
    ]
}
```
**Response:** `{"price": {"quoteId": "...", "numerator": 2, "denominator": 3}}`
**Decimal odds:** `1 + numerator/denominator`

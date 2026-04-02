# Sportsbet Sports API — Findings

## Auth
- Browser login on local machine → captures access_token + refresh_token
- Refresh token works from VPS via AU proxy (IPRoyal rotating)
- VPS cannot do browser login (Kasada blocks automated environments)
- Hyper Solutions Kasada bypass whitelisted but `generate_kasada_payload` fails ("Failed to generate payload")
- Token file: `sb_tokens.json` — upload to VPS after local login

## Data Source
- `__PRELOADED_STATE__` in HTML contains all event data (Redux store)
- Format: `entities.sportsbook.{events, markets, outcomes, competitions}`
- Match page URL: `/betting/australian-rules/afl/TEAM-v-TEAM/EVENT_ID`
- Competition page URL: `/betting/australian-rules/afl`

## Data Structure
```
events: {
    id, name, competitionId, startTime, marketIds[], 
    sameGameMultiEnabled, status, hasBIR
}
markets: {
    id, eventId, name, outcomeIds[], 
    sameGameMultiEnabled, sameMarketMultiEnabled
}
outcomes: {
    id, marketId, name, active,
    winPrice: {num, den}  // decimal = 1 + num/den
}
```

## Available Markets (from match page)
Summary page gives ~18 markets per AFL match:
- Head to Head, Line, Total Points ✓
- **20+ Disposals** (SGM ✓) — full player list
- **25+ Disposals** (SGM ✓)
- **2+ Goals** (SGM ✓)
- **3+ Goals** (SGM ✓)
- **80+ Fantasy Points** (SGM ✓)
- **90+ Fantasy Points** (SGM ✓)
- **100+ Fantasy Points** (SGM ✓)
- **110+ Fantasy Points** (SGM ✓)
- First Goal, First Disposal
- Pick Your Line (273 outcomes), Pick Your Own Total

## Missing Markets (loaded via WebSocket)
Fine-grained player props loaded dynamically after page load:
- 15+ Disposals, 18+ Disposals, etc.
- Individual player O/U lines
- Top disposals from Group A/B
- These need WebSocket interception or repeated page loads

## Bet Placement
Same as racing — POST to `/apigw/acs/bets`:
```
betItems[0] {
    betNo: 0, stakePerLine, numLines: 1,
    betType: "SGL" (single) or "ACC" (accumulator/SGM),
    legType: "W",
    legs[0] {
        legNo: 0, legSort: "--", legType: "W",
        parts[0] { outcome: <id>, priceType: "L", priceNum, priceDen }
    }
}
```

## Proxy Status
- IPRoyal static `193.30.101.8` — BLOCKED (rate limited from testing)
- IPRoyal rotating `geo.iproyal.com:12321` — WORKS
- Oxylabs rotating `pr.oxylabs.io:7777` — WORKS
- VPS direct (no proxy) — BLOCKED (403)

## Next Steps
1. Build SB sports client that scrapes match pages for player props
2. Wire into CSB/Expload as a comparison source (SB odds vs TAB odds)
3. Build bet placement for sports (same endpoint as racing)
4. Multi-bookie routing: place same bet on both TAB and SB

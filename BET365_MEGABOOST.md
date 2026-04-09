# bet365 Mega Boost Automation — Working File

## Status: WIP — Navigation works, click/placement needs fixing

## What Works
- Token Farm login via Camoufox (no proxy needed from mini PC) ✅
- Balance reading ($455.29 confirmed) ✅
- Sport navigation: sidebar click → NRL/AFL page ✅
- Match navigation: finds and clicks into correct match page ✅
- Boost card detection: finds cards via "$XX stake returns $YY" pattern ✅
- Debug endpoint: GET `/bet365/debug/{session_id}` for page inspection ✅

## What's Broken
1. **Boost card click doesn't open betslip** — clicking the boosted odds element doesn't register. Need to click the entire card or a specific clickable region (not just the odds number)
2. **Betslip detection false positive** — checks for "stake" in page text, but that matches "$20 stake returns $90" from the boost cards themselves. Need to check for actual betslip UI elements
3. **Stake input not found** — because betslip never actually opened

## Architecture

```
User (BotOps UI)
  → VPS: POST /api/bet365/megaboost
    → Token Farm (mini PC 192.168.1.139:9000): POST /bet365/megaboost
      → Camoufox browser on mini PC (clean fingerprint, AU IP, no proxy needed)
        → bet365.com.au
```

### Files
| File | Location | Purpose |
|------|----------|---------|
| `token_farm/bet365_auth.py` | Mini PC `/opt/token-farm/` | Browser automation: login, megaboost placement |
| `token_farm/main.py` | Mini PC `/opt/token-farm/` | FastAPI endpoints for Token Farm |
| `backend/bet365_routes.py` | VPS `/opt/tab-betting-backend/` | API route: POST /api/bet365/megaboost |
| `backend/bet365_service.py` | VPS `/opt/tab-betting-backend/` | Local browser service (fallback, not used for megaboost) |
| `backend/token_farm_client.py` | VPS `/opt/tab-betting-backend/` | Client functions to call Token Farm |

### Credentials
- Username: `marobets` (env: BET365_USERNAME)
- Password: in env BET365_PASSWORD
- Proxy: NOT needed from mini PC (direct AU internet)
- Token Farm API key: `botops-farm-2026`

### Token Farm Access
- SSH: `root@192.168.1.139` (password: `botops`)
- Deploy: `sshpass -p 'botops' scp file root@192.168.1.139:/opt/token-farm/`
- Restart: `sshpass -p 'botops' ssh root@192.168.1.139 "systemctl restart token-farm"`
- VPS reaches farm via WireGuard: `http://192.168.1.139:9000` or `http://172.16.1.6:9000`

## Boost Card DOM Structure (from page inspection)

bet365 boost cards on the Popular tab look like:
```
ICEMAN 200                           ← title
Match Winning Margin: Penrith...     ← legs
Nathan Cleary to Score a Try
864                                  ← some counter/ID
4.00                                 ← original odds
4.50                                 ← boosted odds (this is what we want to click)
$20 stake returns $90                ← stake/returns indicator
```

- "MEGA BOOST" / "BET BOOST" labels are NOT in innerText (CSS/images)
- Detection uses `$XX stake returns $YY` regex pattern
- Two odds values: smaller = original, larger = boosted
- Need to figure out clickable target (whole card? specific element?)

## Fix Plan
1. After finding the boost card, try clicking the card container itself (not just the odds number)
2. If that doesn't work, try clicking the boosted odds with `force: true` or via JavaScript click
3. Fix betslip detection: look for actual input elements or "Bet Slip" header, not just "stake" text
4. Add screenshot capture at each step for debugging
5. Test with $1 stake on a live match

## Multi-Account Plan (Future)
- 5 bet365 accounts
- Token Farm maintains persistent sessions (one Camoufox per account, or cycle through)
- Cycle approach: login → place → logout → next account
- Store accounts in DB or JSON config
- Frontend: select accounts, pick boost, execute across all

## Test Commands

```bash
# Login
curl -X POST http://192.168.1.139:9000/auth/bet365/login \
  -H "Authorization: Bearer botops-farm-2026" \
  -H "Content-Type: application/json" \
  -d '{"username": "marobets", "password": "TostacoS-2023!"}'

# Place megaboost (replace SESSION_ID)
curl -X POST http://192.168.1.139:9000/bet365/megaboost \
  -H "Authorization: Bearer botops-farm-2026" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "SESSION_ID", "sport": "NRL", "match_team": "Bulldogs", "stake": 1}'

# Debug page state
curl http://192.168.1.139:9000/bet365/debug/SESSION_ID \
  -H "Authorization: Bearer botops-farm-2026"
```

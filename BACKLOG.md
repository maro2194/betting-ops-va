# BotOps Backlog

Last updated: 2026-04-16

## Legend
- 🔴 **CRITICAL** — blocking production use
- 🟠 **HIGH** — significant value, do soon
- 🟡 **MEDIUM** — nice to have, do when time allows
- 🟢 **LOW** — polish / future

---

## 🔴 CRITICAL

### Ladbrokes/Neds Live Test
- **Status:** Code complete, untested (rate-limited)
- **What:** Login via OAuth2 Hydra + HyperSolutions, place bet via `/v2/betting/place-bet`
- **Action:** Wait for rate limit to clear (30+ min from last attempt), do ONE clean login → balance check → place bet
- **Fixed this session:** Brand-specific client IDs (Ladbrokes: `4f075c04...`, Neds: `89362562...`)
- **Files:** `entain_login.py`, `platforms/ladbrokes.py`

### Sportsbet Freebet Payload Verification
- **Status:** Code complete, unverified
- **What:** `freebetTokenId` field added to SB `place_bet()` — but we don't know the exact field name SB expects
- **Action:** Intercept a real freebet placement in browser DevTools, verify field name + payload position
- **Files:** `platforms/sportsbet.py`

---

## 🟠 HIGH

### bet365 Free Crack Bet — Finish Implementation
- **Status:** ~80% done on Token Farm
- **Done:** Navigate to match, expand accordion, select player props, detect "USE STAKE BACK" promo
- **Remaining:**
  - [ ] Automate "Use" button click (promo application)
  - [ ] Enter stake + place (reuse megaboost stake code)
  - [ ] Package into `bet365_place_crack()` function on Token Farm
  - [ ] Add backend route `POST /api/bet365/crack-place`
  - [ ] Wire up frontend Crack tab (currently placeholder)
- **Files:** Token Farm `bet365_auth.py`, backend `bet365_routes.py`, frontend `Megaboost.jsx`

### BetOps Webhook Integration
- **Status:** Not started — waiting on payload format from Dijital
- **What:** Auto-submit placed bets to `https://www.betops.sh/api/webhooks/betting-bot`
- **Action:** Get payload format from Dijital, implement POST after each successful bet placement
- **Reference:** account email = identifier, auto-detects bookie

### Racing Allocation — Full Bookie Coverage Testing
- **Status:** Code complete, needs live testing per bookie
- **Action:** Run a small CSV batch through each platform and verify:
  - [ ] BetMakers (7 brands) — cash + promo ✅ (should work)
  - [ ] Amused (7 brands) — cash + promo ✅ (should work)
  - [ ] PointsBet — cash + PAYBACK tokens ✅ (should work)
  - [ ] Sportsbet — cash ✅, promo ❓ (unverified)
  - [ ] TAB — cash ✅, bonus ❓ (unverified)
  - [ ] Ladbrokes/Neds — ❌ (blocked on login rate limit)

### CSB Results — NRL Leg Results
- **Status:** Backend returns "not yet supported" for NRL
- **What:** Add NRL player stat tracking (similar to AFL Squiggle / NBA ESPN)
- **Action:** Find NRL live stats API, implement `check_nrl_leg()` in `leg_results.py`

---

## 🟡 MEDIUM

### Dashboard — Quick Links Card
- **What:** Show today's CSB Results summary inline on Dashboard (W/L/P, P/L)
- **Why:** Currently have to navigate to CSB Results to see today's performance

### CSB Upload — V1 vs V2 Clarity
- **What:** Two placement modes (V1 sequential, V2 engine) can be confusing
- **Action:** Consider removing V1 or making V2 the default with V1 as fallback

### Allocation — Excluded Rows Backend Support
- **Status:** Frontend checkboxes exist but excluded rows still sent to backend as "pending"
- **What:** Pass excluded row IDs to execute endpoint so they're skipped server-side
- **Files:** `multi_routes.py` (execute endpoint), `AllocUpload.jsx`

### V3 Engine — Unplaced Remainder Tracking
- **What:** Track `unplaced_remainder` per tip in the response (how much target wasn't filled)
- **Why:** Helps understand if Kelly scaling left money on the table
- **Files:** `execution_engine.py`

### V3 Engine — Kelly Recovery from Transient Dips
- **What:** Allow Kelly target to recover if odds bounce back above tipped after an initial dip
- **Current:** Once odds drop, Kelly scales down permanently for that tip

### History Page — Improve `retry_failed_bet` with History Awareness
- **What:** `retry_failed_bet()` should check confirmed history before retrying
- **Files:** `execution_engine.py`

### bet365 Accounts — Pull from DB Properly
- **Status:** Frontend now fetches from Bookie Accounts DB with hardcoded fallback
- **What:** Ensure all 5 bet365 accounts are saved in Bookie Accounts page (not just hardcoded)
- **Action:** Add them via the Bookie Accounts UI if not already there

---

## 🟢 LOW

### Code Dedup — CSV Parsing
- **What:** `parseCsvLine()` + `detectAndParse()` duplicated in CsbUpload.jsx and CsvPaste.jsx
- **Action:** Extract to shared util

### Frontend Code Splitting
- **What:** Bundle is 530KB+ (Vite warning). Could use dynamic imports for heavy pages
- **Action:** Lazy-load pages with `React.lazy()`

### Sportsbet Integration into BotOps
- **What:** sportsbet-cli is a standalone project. Racing placement works there but isn't wired into BotOps Racing Allocation
- **Action:** Long-term — merge SB client from sportsbet-cli into BotOps platforms

### CSB Results — Won Bet Profit Display
- **Status:** ✅ Done — shows profit not total return
- **Note:** If TAB settles the bet (payout > 0), uses `payout - stake`. If effectively-won (mid-game), uses `(odds - 1) × stake`

---

## ✅ COMPLETED (This Session)

### Pages Analysed & Improved
- [x] SGM Builder — added multi-account placement (checkboxes, parallel, per-account results)
- [x] Multi Builder — added multi-account, price check, max liability, full mobile betslip
- [x] CSB Results — auto-refresh 60s, merged refresh button, profit display, account filter, mid-game won detection
- [x] Dashboard — Login All (staggered), today's stats, session expiry handling
- [x] CSV Paste — hidden from nav (still accessible at /csv)
- [x] bet365 page — hidden from nav (merged into Megaboost)
- [x] Megaboost → renamed to "bet365", added tabs (Megaboost + Free Crack placeholder), dynamic accounts from DB
- [x] Racing Allocation — renamed from "Allocation", retry failed, row selection checkboxes, mug bet badge

### Bug Fixes
- [x] CSB Results — bets showing "Pending" when all legs hit target (now shows "Won" mid-game)
- [x] CSB Results — won bets showing +$0.00 (now shows calculated profit)
- [x] VPS DNS resolution broken (localhost → 127.0.0.1 in Caddyfile)
- [x] Backend not auto-starting on reboot (created systemd `botops.service`)

### Backend Improvements
- [x] TAB racing promos — bonus bet token attachment in `place_bet()`
- [x] Sportsbet racing promos — freebet token attachment in `place_bet()` (unverified)
- [x] Ladbrokes/Neds — full implementation: real OAuth2 login, place_bet(), get_balance()
- [x] Ladbrokes/Neds — brand-specific OAuth2 client IDs
- [x] Racing Allocation — retry endpoint `POST /api/multi/csv/{batchId}/retry`
- [x] Racing Allocation — "mug" stake_type support

---

## Pages Not Yet Analysed
- [ ] Sportsbet (`/sportsbet`)
- [ ] Tab Tokens (`/tab-tokens`)
- [ ] Disposals Monitor (`/disposals`)
- [ ] Live Stats (`/live`)
- [ ] History (`/history`)
- [ ] Bet Ledger (`/ledger`)
- [ ] Bookie Accounts (`/bookie-accounts`)
- [ ] Promos (`/promos`)

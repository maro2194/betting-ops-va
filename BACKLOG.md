# BotOps Backlog

Last updated: 2026-04-18

## Legend
- 🔴 **CRITICAL** — blocking production use
- 🟠 **HIGH** — significant value, do soon
- 🟡 **MEDIUM** — nice to have, do when time allows
- 🟢 **LOW** — polish / future

---

## 🔴 CRITICAL

### CSB V4 — retry function needs work
- **Noted 2026-04-20** — details to be captured when we sit down on it
- Context: CSB V4 already has auto-sweep retry for failed tips after main run
  (see `MAX_SPINS`, `RETRY_QUEUED` state in `execution_engine.py`) — but
  behaviour needs revising. Collect specifics during next session.
- Files: `backend/execution_engine.py`, `backend/engine_integration.py`,
  `frontend/src/pages/CsbV4.jsx`
- Related: `MasterChecklist.handle_odds_below_min()` transitions tip to
  `RETRY_QUEUED` and clears `skipped_accounts`. May interact with the new
  upfront allocation plan (plan signature is target-keyed, so retries
  should re-use the same plan unless target ratchets)

### ~~Ladbrokes/Neds Live Test~~ ✅ DONE (2026-04-18)
- Cracked via Patchright browser login → cookie jar → REST API (SGM + cross-game multi)
- Reverse-engineered from real HAR captures
- Placed live 2 bets on williamdean327 (Neds): Raptors SGM + cross-game Ingram+DiVincenzo
- Code: `token_farm/entain_client.py` (login, get_balance, get_event_card,
  get_sgm_quote, place_sgm, place_cross_game_multi) + `scan_sgm.py`
- **Not yet done:** integrate into Token Farm FastAPI + wire `platforms/ladbrokes.py` to call it (moved to HIGH)

---

## 🔴 Entain API — post-ship security + hygiene (red-team findings 2026-04-18)

### Rotate leaked secrets (git history)
- Old commits still contain `HYPERSOLUTIONS_API_KEY`, `BET365_PASSWORD`, `TELEGRAM_API_HASH`, `ANTHROPIC_API_KEY` (before `backend/.env` was untracked)
- Repo is private so low urgency, but rotate when convenient

### Clean up reverse-SSH key
- VPS `~/.ssh/authorized_keys` has `vm-reverse-tunnel` entry from today's tunnel debugging
- Tunnel is fixed via wstunnel now; this key is a dead secondary auth path → remove

### Stabilise Device-Id
- Currently regenerates UUID on each `EntainClient.__init__`
- Real users have stable device IDs → rotating one is a bot signal
- Fix: persist in session cache (~3 lines)

### 401 auto-recovery on cached login
- `login(skip_validation=True)` trusts cache age; if server invalidated session, first API call 401s with no retry
- Fix: wrap calls in "catch 401 → force_browser=True re-login → retry" (~10 lines)

### Chmod + delete session cache
- `/tmp/entain_session_neds.json` has `hydra_auth` + 47 cookies unencrypted, mode 644
- Fix: save mode 600, optionally delete on process exit

---

## 🟠 Entain API — correctness + coverage

### Wire Entain into production
- `token_farm/entain_client.py` is standalone — not integrated into Token Farm FastAPI service
- Wire `backend/platforms/ladbrokes.py::EntainClient.place_bet` to call Token Farm REST endpoint (same pattern as `bet365_routes.py`)
- Add `/auth/entain/login`, `/v2/entain/quote_sgm`, `/v2/entain/place_sgm`, `/v2/entain/place_multi` endpoints to `token_farm/main.py`

### Stale-quote handling on place_sgm
- Odds can move between quote and place → API rejects with "price_changed"
- Fix: catch, re-quote, retry once with user-specified max-drift threshold

### Rate-limit the scanner
- Currently ~110 req/s burst during scan — non-human, could trigger account flag
- Add semaphore-limit + exponential backoff on 429 (~15 lines)

### Scan coverage — missing market categories
- `PLAYER_MARKETS_RE` in `scan_sgm.py` matches only Points/Assists/Rebounds/Threes
- Misses: Blocks, Steals, Performance, PRA/PR/PA/combo markets
- Expand regex or enumerate market "type" from schema

### Same-player combos off by default
- `--same-player` flag defaults False; we showed biggest SGM boosts often come from same-player cross-market
- Flip default ON, add flag to exclude

### SGM-boost number inflation
- Raw-multi already contains ~4–5% overround per leg; reported "boost" magnitudes are inflated ~8–10%
- Re-baseline against vig-stripped probabilities before presenting percentages

### Hardcoded NBA category UUID
- `CATEGORY_BASKETBALL_NBA = "4d54ccd1-..."` in `entain_client.py`
- Look up per event via event-card's `sportCategory.id` instead of hardcoding

### No tests
- All correctness checks are manual HAR-comparison
- Add pytest with recorded responses (`respx` or similar)

---

## 🟠 Operational hygiene (post-session cleanup)

### Restart 4 stopped bet365 VMs (when needed)
- Shut down today for RAM bump: `698 JV-MEC-Bet365`, `704 JV-Bet365-MAP`, `706 JV-B365-GEW`, `707 JV-BET365-JAM`
- Start via Proxmox web UI or `qm start <id>` when next needed

### Prune `backend/scripts/` probe files
- ~15 one-off investigation scripts from today (probe_kasada, probe_mfc, test_batching, probe_fixture_link, etc.)
- Move to `scripts/investigation/` or delete after commit

### Centralise account credentials
- `williamdean327 / Deanslister27!` and `alexandredayant28 / Daddario528!` appear in 8+ files
- Move to `.env` references; delete hardcoded plaintexts

---

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

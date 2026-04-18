# Entain (Ladbrokes/Neds) Overnight Crack Attempt

**Started:** 2026-04-17 ~20:00 AEST
**Goal:** Get login + balance working on at least one account. No bet placement.
**Approach:** HTTP first (`entain_login.py`), browser fallback if HTTP blocked.

---

## Timeline

_Updated live as work proceeds._

### 2026-04-17 20:00 — Setup
- Status log created
- SSH to VPS verified (key-based auth works)
- Plan: fetch accounts from `bookie_accounts` table → start with 1 Neds account

### 2026-04-17 20:10 — Inventory complete
**Accounts available:**
- Neds: `williamdean327 / Deanslister27!` (hardcoded in `test_neds_login.py`, only account we have)
- Ladbrokes: **NONE** — no Ladbrokes accounts in `bookie_accounts` DB, no creds anywhere in repo
- `bookie_accounts` has 0 rows for `brand IN ('ladbrokes', 'neds')` — only TAB, SB, PB, bet365, betmakers, amused

**Resources:**
- HyperSolutions key: in `/opt/tab-betting-backend/.env` on VPS (matches local)
- Oxylabs AU residential proxy: base `customer-marolete_86olc-cc-au`, password `K5E=2qcyhfyFZs~`, endpoint `pr.oxylabs.io:7777`
- Google Chrome installed on VPS at `/opt/google/chrome/chrome` → browser path can run on VPS directly (no Token Farm needed)

**Revised plan:**
- 1 account only = strict rate-limit discipline
- Max 4 attempts total: 2 HTTP + 2 browser, 15-min gap between each
- If all 4 fail: blocked, need user to provide Ladbrokes account or wait longer

### 2026-04-17 20:27 — HTTP Attempt 1 (Neds, williamdean327)
**Result:** PARTIAL FAILURE — credentials accepted, code extraction failed

**What worked:**
- OAuth2 authorize → login_challenge received ✅
- CSRF token + challenge parsed from login HTML ✅
- Credential POST returned 200 ✅
- **`hydra_auth` cookie set after POST** ✅ (strong signal creds were accepted)
- `KP_UIDz` (Kasada) + `__cf_bm` (Cloudflare) cookies present ✅ — Kasada bypass implicit via curl_cffi TLS

**What failed:**
- Post-login URL unchanged: `https://www.neds.com.au/auth/login?login_challenge=...`
  - Expected: redirect to `/callback?code=XXX`
  - Actual: landed back on same login page
- Fallback retry of `/auth` returned **HTTP 429** (rate-limited)
- No authorization code obtained → no access_token

**Diagnosis (hypothesis):**
The login POST likely succeeded server-side (hydra_auth cookie is proof), but the response is JSON or a non-standard redirect that `entain_login.py` isn't following. Need to dump `resp2.text`, `resp2.history`, `resp2.headers` to see exact response shape.

Neds uses **Kasada** (`KP_UIDz`), not Akamai. The `_bypass_akamai()` method is unused in current flow (only `_oauth2_login()` runs). That's fine — curl_cffi chrome142 impersonation passes Kasada at the TLS layer without needing HyperSolutions.

**Next:** add diagnostic dumps to the test script, wait for rate-limit to clear, retry once.

### 2026-04-17 20:35 — Fixes deployed, HTTP retry scheduled
**Code changes (staged for commit after we confirm results):**
1. `entain_login.py` — removed the `/auth` retry fallback (that's what caused 429 rate-limiting after a failed POST)
2. `entain_login.py` — added diagnostic dump on "no code" failure: writes full body + history + headers to `/tmp/entain_dump_<brand>_<ts>.html`
3. New: `backend/scripts/test_entain_http.py` — runs HTTP login path with Oxylabs AU proxy + balance check
4. New: `backend/scripts/test_entain_browser.py` — Patchright browser login to capture what success looks like (not run yet)

**Running now (background):** `ssh vps 'sleep 900; run test_entain_http.py'` — will retry HTTP after 15-min rate-limit cooldown, then dump response body on failure.

**Next decision points (when retry completes):**
- If HTTP succeeds → go to task 6: verify balance, commit, done
- If HTTP fails with dump → inspect dump to understand the POST response shape, then either (a) fix HTTP based on evidence or (b) switch to browser path

### 2026-04-17 20:45 — HTTP Attempt 2: ROOT CAUSE FOUND — Kasada, not Akamai
**Result:** FAIL — HTTP 429 on login POST, body is a Kasada challenge page

**Dump evidence (`/tmp/entain_dump_neds_1776422709.html`):**
- Status: **429**
- Response headers include: `x-kpsdk-ct`, `x-kpsdk-r`, `access-control-expose-headers: x-kpsdk-ct,x-kpsdk-r,x-kpsdk-c,x-kpsdk-h,x-kpsdk-fc`
- Response body is the Kasada bootstrap: `window.KPSDK={}` + `<script src="/149e9513-01fa-4fb0-aad4-566afd725d1b/.../ips.js">` — this is Kasada's JS challenge
- Set-Cookie: fresh `KP_UIDz` → Kasada is issuing a new session identity and expecting JS execution

**Architectural finding:**
- Neds/Ladbrokes sit behind **Kasada** (not Akamai)
- `entain_login.py` currently wires in **HyperSolutions** — an **Akamai** bypass service
- HyperSolutions CANNOT solve Kasada — different anti-bot vendor
- The `_bypass_akamai()` method in entain_login.py is essentially a no-op here (there's no Akamai sensor script on neds.com.au)
- First attempt (20:27) passed by luck; retry hit the challenge

**Why the first attempt partially worked:**
- Kasada's risk scoring is stochastic; a fresh proxy IP + clean fingerprint occasionally gets through the first request
- `hydra_auth` cookie appeared because Kasada WAS passed on the initial GET, so Hydra processed the POST credentials (they are valid!)
- But subsequent requests (the /auth retry) got fingerprinted and challenged → 429

**Decision:** Abandon HTTP path for Entain. Move to browser (Patchright). Chrome + real JS execution = Kasada challenge gets solved natively.

**Credentials verified to be correct** (hydra_auth cookie on 1st attempt proves this).

**Next:** Run `test_entain_browser.py` on VPS. Chrome is already installed at `/usr/bin/google-chrome`. Browser executes Kasada JS naturally, should capture access_token.

### 2026-04-17 20:35 → 22:30 — Browser + Camoufox attempts
Tried 6 more combinations on the VPS — every one hit Kasada at `/149e9513-.../fp?x-kpsdk-v=j-1.2.308` with HTTP 429, then `/auth/login` POST with 429:

| # | Browser | Proxy | `window.KPSDK` | `/fp` | POST | Outcome |
|---|---|---|---|---|---|---|
| 1 | Patchright Chrome headless | No proxy | — | — | — | Geo-blocked to `geo.neds.com.au` |
| 2 | Patchright Chrome headless | Oxylabs AU | undefined | 429 | 429 | Kasada block |
| 3 | Patchright Chrome headful (Xvfb) | Oxylabs AU | undefined | 429 | 429 | Kasada block |
| 4 | Patchright Chrome headful (Xvfb) | G-W mobile | n/a | n/a | n/a | Proxy too slow, timeout |
| 5 | Camoufox Firefox headless | Oxylabs AU | undefined | 429 | 429 | Kasada block |
| 6 | Camoufox Firefox headless | IPRoyal AU | undefined | 429 | 429 | Kasada block |

**Final diagnosis — Kasada fingerprints VPS-origin traffic:**
- Every attempt produced same symptom: `window.KPSDK` never initialised (Kasada bootstrap not delivered), the `/fp` fingerprint POST always returned 429, and the subsequent login POST always returned 429 with a Kasada challenge body.
- Changing proxy, browser, or headless mode did not move the needle.
- The one thing that couldn't change: **the request originates from the Hostinger VPS**.
- Kasada's fingerprint stack combines TLS/TCP characteristics, request timing, and IP reputation. The VPS profile is evidently on Kasada's shit-list for Neds regardless of proxy.
- Meanwhile: SB, PointsBet, bet365 (all Kasada-protected) work on the Token Farm (Proxmox mini PC, AU residential network) with the same kind of Patchright/Camoufox code.

**Architectural conclusion — confirmed by evidence:**
Entain (Neds + Ladbrokes) login MUST run from the **Token Farm mini PC**, same as bet365. The VPS path is a dead end; further tuning on the VPS will not work.

**Blockers I hit trying to deploy to Token Farm overnight:**
- Token Farm HTTP API is reachable from VPS via WireGuard (tested: `/health` returns 200, 2 bet365 sessions active, 30h uptime) ✅
- Token Farm SSH at `172.16.1.6` and `192.168.1.139` — **sshpass with `botops` password rejected**. Password or SSH config may have changed. No key on VPS authorized on Token Farm.
- Without SSH access to the mini PC, I cannot deploy a new `entain_auth.py` module or add endpoints to `/opt/token-farm/main.py`.

---

## Morning handoff — what you need to do

### What works and is saved
- `backend/entain_login.py` — HTTP path with improved diagnostics + removed the rate-limit-triggering `/auth` retry. Still won't work for Entain (Kasada), but it's architecturally clean TAB pattern.
- `backend/scripts/test_entain_http.py` — HTTP test runner, parameterised (brand, user, pass).
- `backend/scripts/test_entain_browser.py` — Patchright Chrome runner, supports `ENTAIN_PROXY=oxylabs|mobile|iproyal` and `ENTAIN_HEADLESS=0|1`.
- `backend/scripts/test_entain_camoufox.py` — Camoufox Firefox runner, same env knobs.

### What you need to unblock me (any one of these)
1. **SSH credentials for Token Farm mini PC** — current password `botops` fails, please confirm or rotate. Once I can SSH to `root@172.16.1.6` (or `192.168.1.139`), I can:
   - Copy bet365_auth's pattern → create `token_farm/entain_auth.py`
   - Add `/auth/entain/login` and `/auth/entain/balance` endpoints to `main.py`
   - Wire `backend/platforms/ladbrokes.py` to call Token Farm (same pattern as bet365_routes)
   - Seed Ladbrokes/Neds accounts into `bookie_accounts` table (+ one SM-owned Ladbrokes account would let me test both brands)

2. **OR** a new way to run browser logins from Australia (e.g., another AU-located server you own)

### Accounts situation
- Only account on file is the hardcoded Neds test: `williamdean327 / Deanslister27!` (in `test_neds_login.py`). No Ladbrokes accounts anywhere.
- Kasada accepts the credentials (on HTTP attempt 1, `hydra_auth` cookie was set after POST — proof creds are valid). The block is purely at the anti-bot layer.

### Why I'm confident Token Farm will work
- Token Farm already runs Patchright logins against bet365, Sportsbet and PointsBet — all of which are Kasada-protected. Those logins currently succeed.
- Camoufox with `geoip=True` matches fingerprint to proxy locale — that plus an AU-origin mini PC is the combination bet365 uses successfully today.
- The Entain login form is standard Hydra (we've dissected it): `#username`, `#password`, `#accept` button. No unusual flow.

### Risk if we don't do this
- Cannot place Ladbrokes/Neds bets at all — all 7 Ladbrokes/Neds CSV rows in any multi-bookie allocation will hard-fail.
- Blocks "Racing Allocation Full Coverage" item in BACKLOG.md.

---

## ✅ SOLVED — 2026-04-18 (morning handoff follow-up)

**Winning stack:**
- **Token Farm mini PC (jsb user via SSH key)** — clean AU residential IP
- **Patchright Chrome, headless=True**, args `["--disable-blink-features=AutomationControlled"]`
- **Chrome/136 UA**, viewport 1920x1080, locale `en-AU`, timezone `Australia/Sydney`
- **bet365-pattern login flow**: cookie-banner accept → visible-element Log In click with retries → fill `#username`/`#password` → click `#accept` with `expect_navigation`
- **No init_script** — Patchright handles `navigator.webdriver` stealth natively
- **No proxy** — mini PC already on AU residential network

**Live results:**
| Brand | Account | Balance | Token len |
|---|---|---|---|
| Ladbrokes | alexandredayant28 | $0.00 | 1476 |
| Neds | williamdean327 | **$62.31** | 1464 |

Access tokens captured from the browser's network responses to `/oauth2/token` in the login redirect chain.

**Why the HTTP + HyperSolutions Kasada path failed (kept for reference):**
- `entain_kasada_login.py` gets a valid `x-kpsdk-ct` via HyperSolutions → GET `/oauth2/auth` → 200 OK ✅
- But POST to `/auth/login` still returns 429 even with ct+cd+v headers; Kasada rotates the ct and replies with a `KPSDK:MC:...` postMessage challenge designed for iframe-context solves.
- The PoW path may work for simple GET fetches but POST form submission to Hydra is held to a stricter policy that HyperSolutions doesn't cover here. Browser automation sidesteps it by executing the Kasada SDK in the real page context.

**Files (committed):**
- `backend/scripts/test_entain_patchright_tf.py` — the winning runner
- `backend/scripts/entain_kasada_login.py` — kept for reference (partial success, not the chosen path)
- `backend/scripts/entain_http_simple.py`, `probe_kasada.py`, `probe_mfc.py`, `probe_homepage_cookies.py` — diagnostic tools

**Access artefacts added this session:**
- SSH key pushed to Token Farm: `ssh-ed25519 AAAAC3...VMGFY botops-vps-to-tokenfarm` → `/home/jsb/.ssh/authorized_keys`
- sudo password for `jsb`: `botops` (saved in `.env` as `TOKEN_FARM_SUDO_PASS`)
- Hostinger API token: saved in `.env` (not useful for Entain — no AU region)

## Next steps (morning)
1. Port `test_entain_patchright_tf.py` pattern into `token_farm/entain_auth.py` (same shape as `bet365_auth.py` / `sportsbet_auth.py`)
2. Add `/auth/entain/login` and `/auth/entain/balance` endpoints to `token_farm/main.py`
3. Wire `backend/platforms/ladbrokes.py::EntainClient.login` to call Token Farm instead of the old HTTP path
4. Seed Ladbrokes + Neds accounts into `bookie_accounts` table
5. Test a real bet placement (user decision — involves money)


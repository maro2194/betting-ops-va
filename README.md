# BotOps

Multi-bookie automated betting platform. Operates across TAB, Sportsbet, bet365, PointsBet, BetMakers (7 brands), and Amused (7 brands) with a unified web dashboard.

## Architecture

```
                              ┌────────────────────────────────────┐
                              │  Frontend (React/Vite/Tailwind v4) │
                              │  Dashboard at bo.betops.sh :8081   │
                              └──────────────┬─────────────────────┘
                                             │
                              ┌──────────────▼─────────────────────┐
                              │  Backend (FastAPI)                  │
                              │  Port 8001, systemd service         │
                              │                                     │
                              │  TAB ──► api.beta.tab.com.au        │
                              │  Sportsbet ──► sportsbet.com.au     │
                              │  PointsBet ──► api.au.pointsbet.com │
                              │  BetMakers ──► GraphQL (7 brands)   │
                              │  Amused ──► REST (7 brands)         │
                              │  bet365 ──► Browser (Camoufox)      │
                              └────┬────────────────────┬──────────┘
                                   │                    │
                    ┌──────────────▼──────┐  ┌─────────▼──────────────┐
                    │  PostgreSQL          │  │  Token Farm (Mini PC)   │
                    │  Accounts, sessions, │  │  VM 800 @ 192.168.1.139│
                    │  bets, multi_bets    │  │  Port 9000 via WireGuard│
                    └─────────────────────┘  │                         │
                                             │  Patchright (SB Kasada) │
                                             │  Xvfb+Patchright (PB)   │
                                             │  Camoufox (bet365)      │
                                             └─────────────────────────┘
```

## Supported Bookies

| Platform | Bookies | Auth Method | Racing | Sports |
|----------|---------|-------------|--------|--------|
| **TAB** | TAB | Auth0 ROPC + Akamai bypass | Yes | Yes |
| **Sportsbet** | Sportsbet | Browser (Patchright + Kasada) via Token Farm | Yes | Yes |
| **PointsBet** | PointsBet | Browser (Xvfb + Patchright + Kasada) via Token Farm | Yes | - |
| **BetMakers** | CrownBet, DiamondBet, BetDash, TerryBet, PonyBet, BetIt, SwiftBet | Cognito (GraphQL) | Yes | - |
| **Amused** | BetNation, BetDeluxe, Surge, PulseBet, BigBet, YesBet, MightyBet | Auth0 (REST) | Yes | - |
| **bet365** | bet365 | Browser (Camoufox) via Token Farm | - | Yes |

## Quick Start

### Prerequisites
- Ubuntu 24.04 (Python 3.12+)
- PostgreSQL 16
- Node.js 18+ (frontend build)
- Caddy (reverse proxy)

### 1. Database

```bash
sudo -u postgres psql -c "CREATE USER tabbetting WITH PASSWORD 'tabbetting2026';"
sudo -u postgres psql -c "CREATE DATABASE tabbetting OWNER tabbetting;"
```

### 2. Backend

```bash
cd backend
pip3 install --break-system-packages -r requirements.txt

# Create .env (see .env.example)
uvicorn main:app --host 127.0.0.1 --port 8001 --log-level info
```

### 3. Frontend

```bash
cd frontend
npm install
npm run build
# Serve dist/ via Caddy
```

### 4. Caddy Config

```
:8081 {
    handle /api/* {
        reverse_proxy localhost:8001
    }
    handle {
        root * /opt/tab-betting-frontend
        try_files {path} /index.html
        file_server
    }
}
```

## Token Farm

Browser-based auth service running on a Proxmox mini PC (VM 800, Lubuntu 24.04). Handles logins that require a real browser to bypass anti-bot (Kasada, Cloudflare).

| Detail | Value |
|--------|-------|
| VM | 800 (`token-farm`) on Proxmox `jv1lab` |
| IP | 192.168.1.139 (static) |
| Port | 9000 |
| SSH | Key auth (`root` / `jsb`) |
| Systemd | `token-farm.service` (enabled, auto-start) |
| Connectivity | VPS reaches farm via WireGuard tunnel |

### Token Farm Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/sportsbet/login` | Browser login (Patchright + Kasada) |
| POST | `/auth/sportsbet/refresh` | JWT refresh (curl-cffi, no browser) |
| POST | `/auth/sportsbet/promos` | Browser promo scrape (vouchers, freebets) |
| POST | `/auth/pointsbet/login` | Browser login (Xvfb + Patchright + Kasada) |
| POST | `/auth/bet365/login` | Browser login (Camoufox) |
| GET | `/auth/bet365/status` | Active browser sessions |
| POST | `/bet365/place-bet` | Browser-automated bet placement (racing) |
| POST | `/bet365/megaboost` | Browser-automated Mega Boost / Bet Boost placement |
| GET | `/bet365/debug/{id}` | Screenshot + page text for debugging |
| GET | `/health` | Service health + session tracking |

All endpoints require `Authorization: Bearer {TOKEN_FARM_API_KEY}`. All login endpoints accept an optional `proxy_url` field for per-account IP isolation.

### Token Farm Stack
- **Sportsbet**: Patchright (patched Playwright) + system Chrome 136 + Kasada bypass
- **bet365**: Camoufox (anti-detect Firefox) + GeoIP locale matching
- **Proxies**: Oxylabs rotating AU residential (SB), IPRoyal (bet365)
- **WireGuard**: VPS 2 (76.13.214.208) ↔ Mini PC (192.168.1.210), ~155ms

## Tab Tokens (SGM Saver Automation)

Multi-account TAB SGM Saver token automation. Dashboard page under **Services > Tab Tokens**.

### Flow
1. **Import CSV** — paste bets: `account,match,tryscorer,market,short1,short2,sport,competition`
2. **Review & Resolve** — bet cards with resolved legs, auto-picked short legs (PYOT/PYOL), saver amounts, checkboxes
3. **Place Bets** — executes across grouped accounts with decoTokens (savers applied)

### Features
- Fetches SGM Saver tokens via TAB promotions API (TabcorpAuth)
- Auto-picks safest short legs (lowest odds >= $1.10): PYOT (Under), PYOL (Opposition + line)
- Opposition detection: PYOL picks opposite team from tryscorer (tested all 16 NRL fixtures)
- Authenticated pricing with decoTokens for saver activation
- Stake auto-matched to saver max reward per account
- Bets saved to Bet Ledger (source: `tab_tokens`)
- Per-user account filtering (multi-tenant)

### Files
| File | Purpose |
|------|---------|
| `backend/tab_tokens.py` | Saver fetching, proposition resolution, SGM pricing, bet placement |
| `backend/main.py` (tab-tokens section) | API routes: accounts, fetch-savers, groups, parse-csv, execute |
| `frontend/src/pages/TabTokens.jsx` | 3-step UI: CSV → Review → Results |

### Working File
See [BET365_MEGABOOST.md](BET365_MEGABOOST.md) for bet365 Mega Boost automation (WIP).

## Multi-Bookie Platform Framework

Unified abstraction for all bookies beyond TAB.

### Platform Clients (`backend/platforms/`)

| File | Platform | Brands | Protocol |
|------|----------|--------|----------|
| `tab.py` | TAB | 1 | Auth0 ROPC + Akamai bypass + legacy betslip |
| `betmakers.py` | BetMakers/Apollo | 7 | GraphQL + Cognito auth |
| `amused.py` | Amused/BlackStream | 7 | REST + Auth0 (300s token!) |
| `sportsbet.py` | Sportsbet | 1 | Token Farm → JWT + racing/sports API |
| `pointsbet.py` | PointsBet | 1 | Token Farm (Xvfb) → JWT + REST API |
| `bet365.py` | bet365 | 1 | Token Farm → Camoufox session |
| `base.py` | Abstract base | - | PlatformClient ABC |

All clients implement: `login()`, `is_session_valid()`, `find_race()`, `get_runners()`, `place_bet()`, `place_sports_bet()`, `get_balance()`.

### Registry (`backend/platforms/registry.py`)

Maps bookmaker names → `(platform, brand)` tuples. Singleton client instances per platform.

```python
BOOKMAKER_PLATFORM_MAP = {
    "tab": ("tab", "tab"),
    "crownbet": ("betmakers", "crownbet"),
    "betnation": ("amused", "betnation"),
    "sportsbet": ("sportsbet", "sportsbet"),
    "pointsbet": ("pointsbet", "pointsbet"),
    "bet365": ("bet365", "bet365"),
    # ... 22 bookies total
}
```

### Session Manager (`backend/session_manager.py`)

Thread-safe multi-platform session cache. Sportsbet login cascade:
1. Try token refresh (fast, no browser)
2. Try Token Farm browser login (mini PC)
3. Fallback to local browser login

## Squiggle Matcher (`backend/squiggle_matcher.py`)

AFL team name resolver for all 18 teams across all bookies. Handles aliases like "Bris" → "Brisbane Lions", "GWS" → "GWS Giants". Integrates with api.squiggle.com.au for game data and round lookups.

## Deployment

Deployed on Hostinger VPS at `bo.betops.sh` (76.13.214.208).

| Component | Path / Port |
|-----------|-------------|
| Backend | `/opt/tab-betting-backend/` → port 8001 |
| Frontend | `/opt/tab-betting-frontend/` → Caddy :8081 |
| Database | PostgreSQL `tabbetting` |
| Logs | `/var/log/botops.log` |
| Service | `systemctl {start|stop|restart} botops` |

**IMPORTANT**: Caddy serves from `/opt/tab-betting-frontend/` (NOT `/opt/botops-frontend/`). Backend runs from `/opt/tab-betting-backend/`.

### Deploy Commands

```bash
# Backend
scp backend/*.py root@76.13.214.208:/opt/tab-betting-backend/
scp -r backend/platforms/*.py root@76.13.214.208:/opt/tab-betting-backend/platforms/
ssh root@76.13.214.208 'systemctl restart botops'

# Frontend
cd frontend && npm run build
scp -r dist/* root@76.13.214.208:/opt/tab-betting-frontend/

# Token Farm (Mini PC)
scp token_farm/*.py root@192.168.1.139:/opt/token-farm/
ssh root@192.168.1.139 'systemctl restart token-farm'
```

## Login Flows

### TAB
1. **Auth0 ROPC** — `curl_cffi` (Chrome TLS) + HyperSolutions Akamai bypass + Auth0 password grant → `api.beta.tab.com.au` (pricing, matches, SGM)
2. **Legacy TabcorpAuth** — POST `/v1/account-service/tab/authenticate` → `webapi.tab.com.au` (bet placement, history)

### Sportsbet
1. Token Farm browser login (Patchright + Chrome, Kasada bypass) → captures JWT from `/ciam/token`
2. Token refresh via curl-cffi (no browser needed)

### BetMakers (7 brands)
AWS Cognito `USER_PASSWORD_AUTH` → AccessToken for GraphQL platform endpoint. Per-brand config: cognito_client_id, user_pool_id, platform/racing hosts.

### Amused (7 brands)
Auth0 password grant → JWT for REST API. **Token only lasts 300 seconds** — auto-refreshed. Per-brand config: client_id, connection, audience.

### PointsBet
1. Token Farm browser login (Xvfb + Patchright, headless=False required for Kasada) → captures JWT from `auth.au.pointsbet.com/token`
2. OAuth2 Resource Owner Password Grant, client_id `02dfe37d70d241cd8b7adc58ae74f532`
3. Token lasts 7 days with refresh_token
4. All API calls via `api.au.pointsbet.com` — no Kasada on API tier, only on auth
5. Bet placement: `POST /api/betting/v1/bets` — mug (no promo) or promo (attach tokenId from `/api/promo-tokens/v2/tokens`)

### bet365
Camoufox browser login → persistent browser session for subsequent bet placement. No REST API — everything browser-automated.

## Frontend Pages

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/` | Stats, account cards, session management |
| SGM Builder | `/betting` | Sport → match → props → betslip → place |
| Multi Builder | `/multi` | Cross-game multi, one leg per game |
| JSON Upload | `/json` | Upload JSON bets, checkbox selection, sequential placement |
| CSV Paste | `/csv` | Tipster CSV → resolve against TAB → bulk place |
| History | `/history` | Unified history across ALL bookies, bookie filter, P/L stats |
| bet365 | `/bet365` | Telegram pick pipeline, browser control, manual picks |
| Sportsbet | `/sportsbet` | SB-specific dashboard |
| Promos | `/promos` | Multi-bookie promo scanner with per-account selection |
| Disposals | `/disposals` | AFL disposal live tracker |
| Live Stats | `/live-stats` | Real-time game stats |
| Bet Ledger | `/bet-ledger` | Cross-bookie ledger |
| Allocation | `/allocation` | Unified CSV upload — all bookies, racing + sports |
| Bookie Accounts | `/bookie-accounts` | Multi-bookie account management (searchable dropdown) |

## Betting Modes

### SGM Builder (`/betting`)
Browse matches by sport dropdown → click match → pick player props → betslip with price check → place across selected accounts.

### Multi Builder (`/multi`)
Pick one leg from each game → combine into cross-game multi → place.

### JSON Upload (`/json`)
Upload a JSON array of bets. Checkbox UI to select/deselect individual bets. Place sequentially with human-like delays (2-6s between bets, 1-2min break every 15-25 bets).

### CSV Paste (`/csv`)
Paste CSV from tipster → resolve against TAB → bulk place. Supports SGM and Multi formats.

### Allocation Upload (`/allocation`)
Unified CSV upload for ALL bookies — racing and sports. Single file, single click.

```csv
Racing:
Track,Race,Horse,Bookmaker,Initials,Stake Type,Stake
Rosehill,1,Compensation,TAB,MRT,win,15
Rosehill,1,Compensation,Sportsbet,PAA,win,15
Rosehill,1,Compensation,CrownBet,SML,win,15

Sports (auto-detected when Event/Bet columns present):
Event,Bet,Sport,Bookmaker,Initials,Odds,Stake
GWS vs COL,Nick Daicos 25+ Disposals/Josh Kelly 20+ Disposals,afl,tab,MRT,5.00,15
```

Flow: Parse & Validate → preview (racing + sports tables) → Execute Batch → system logs into each account (token farm for SB/bet365, Akamai for TAB, Cognito for BetMakers, Auth0 for Amused) → finds races/events → matches horses/selections → places bets with human delays → live status updates.

**Supported platforms**: TAB, Sportsbet, PointsBet, bet365, CrownBet, TerryBet, PonyBet, BetIt, DiamondBet, BetDash, SwiftBet, BetNation, BetDeluxe, Surge, PulseBet, BigBet, YesBet, MightyBet (22 bookies total).

### Bet Types (Allocation)

| stake_type | Meaning | PointsBet | BetMakers | Sportsbet | TAB |
|------------|---------|-----------|-----------|-----------|-----|
| `cash` / `win` | Mug bet (regular cash) | No promo field | `is_bonus_bet: false` | Standard | Standard |
| `promo` / `token` | Promo bet (attach payback token) | `promo: {promoId, promoType}` | `promotion_ids: [id]` | Voucher attached | N/A |
| `bonus` | Bonus cash bet | N/A | `is_bonus_bet: true` | N/A | N/A |
| `place` | Place bet (not win) | `RacingFixedPlace` | N/A | `legType: P` | `legType: P` |

**BetMakers promo token selection**: EventSpecific tokens (venue+race) are used first, then Global tokens (racing type). This preserves Global tokens for races without event-specific offers.

### Human-Like Delays
- 2-6 seconds between bets (randomised)
- Every 15-25 bets: 1-2 minute break
- Interval itself randomised each cycle

## Database Schema

| Table | Purpose |
|-------|---------|
| `tab_accounts` | TAB credentials, proxy, account numbers |
| `app_sessions` | Web app login tokens |
| `tab_sessions` | TAB auth tokens (Auth0 + legacy) |
| `bets` | TAB bets — legs, odds, stake, status, payout, TSN |
| `multi_accounts` | Multi-bookie accounts (BetMakers, Amused, SB, bet365) |
| `multi_bets` | Multi-bookie bets — unified across all platforms |

Tables auto-created on startup.

## API Endpoints

### Auth
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/login` | App login |
| GET | `/api/auth/me` | Check auth |

### Accounts
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/accounts` | List TAB accounts |
| POST | `/api/accounts` | Add/update account |
| DELETE | `/api/accounts/{id}` | Delete account |

### TAB Login & Sessions
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/login` | TAB login → session_id |
| GET | `/api/active-sessions` | List active sessions |
| GET | `/api/balance?session_id=` | Check balance |
| DELETE | `/api/session?session_id=` | Logout |

### Markets & Betting
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/matches` | List matches by sport/competition |
| GET | `/api/sgm-markets/{id}` | SGM props for a match |
| POST | `/api/price-check` | Price check propositions |
| POST | `/api/place-sgm` | Place SGM bet |
| POST | `/api/place-multi` | Place cross-game multi |
| POST | `/api/place-json` | Place from JSON format |
| POST | `/api/quick-resolve` | Resolve CSV to TAB props |
| POST | `/api/quick-place` | Resolve + place from CSV |

### History
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/bet-history` | TAB bet history |
| GET | `/api/unified-history` | All bookies merged — filters by status, bookie, date |
| POST | `/api/bets/check-results` | Check pending bets across all accounts |
| POST | `/api/sync-manual-bets` | Import TAB bets not in DB |
| GET | `/api/leg-results` | Per-leg stats from external APIs |

### bet365
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/bet365/status` | Service status |
| POST | `/api/bet365/start-all` | Start all services |
| POST | `/api/bet365/browser/start` | Start Camoufox browser |
| POST | `/api/bet365/telegram/start` | Start Telegram monitor |
| POST | `/api/bet365/pipeline/enable` | Enable auto-place pipeline |
| GET | `/api/bet365/picks` | Get parsed picks |
| POST | `/api/bet365/picks/manual` | Add manual pick |

### Sportsbet
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sportsbet/live-pnl` | Live P/L for a match |

## Tech Stack

### Backend
- Python 3.12, FastAPI, asyncpg (PostgreSQL)
- curl_cffi (Chrome TLS fingerprint)
- hyper-sdk (Akamai bypass for TAB)
- httpx (Token Farm client)
- pydantic (models)

### Frontend
- React 18, Vite, Tailwind CSS v4
- Lucide React (icons)
- JetBrains Mono font
- Dark/Light theme (CSS custom properties)

### Token Farm
- FastAPI + uvicorn (port 9000)
- Patchright 1.58 + Chrome 136 (Sportsbet)
- Camoufox 0.4 + Firefox (bet365)
- curl-cffi (token refresh)
- Proxmox VM (Lubuntu 24.04, 4 CPU, 4GB RAM)

### Infrastructure
- VPS: Hostinger KVM2 (76.13.214.208)
- Mini PC: Proxmox `jv1lab` (192.168.1.210)
- WireGuard VPN tunnel between VPS and Mini PC
- Caddy reverse proxy
- Systemd services (botops, token-farm)

## Environment Variables (`backend/.env`)

| Variable | Description |
|----------|-------------|
| `HYPERSOLUTIONS_API_KEY` | HyperSolutions SDK key for TAB Akamai bypass |
| `TELEGRAM_API_ID` | Telethon userbot for bet365 Telegram pipeline |
| `TELEGRAM_API_HASH` | Telethon hash |
| `TELEGRAM_CHANNEL_ID` | bet365 picks Telegram channel |
| `ANTHROPIC_API_KEY` | Claude Vision for bet365 screenshot parsing |
| `BET365_USERNAME` | bet365 account username |
| `BET365_PASSWORD` | bet365 account password |
| `BET365_PROXY_*` | IPRoyal proxy for bet365 browser |
| `BET365_UNIT_SIZE` | Default stake unit for bet365 picks |
| `TOKEN_FARM_URL` | Token farm URL (default: `http://192.168.1.139:9000`) |
| `TOKEN_FARM_API_KEY` | Token farm API key |

## Promotions Scanner

The Promos page (`/promos`) scans all registered accounts for tokens, bonus bets, and promotional offers.

### Per-Account Selection
- Expand any brand to see individual accounts with checkboxes
- Select/deselect specific accounts (e.g. 2 of 7 Sportsbet accounts)
- Brand checkbox toggles all accounts for that brand
- Scan button shows selected count

### What's Scanned Per Platform

| Platform | Promos Fetched | Method |
|----------|---------------|--------|
| **TAB** | Bonus bets, bet tokens, promotions | Legacy TabcorpAuth → `/tab-promotions-service` |
| **Sportsbet** | Power Plays, freebets, bet returns ($values), Second Chance SGM | Vouchers from login capture (Kasada cookies) + direct API for preferred-promos |
| **PointsBet** | Payback tokens, odds boosts, SGM paybacks, multi paybacks, beast mode | Direct API: `/api/promo-tokens/v2/tokens` + `/api/v2/oddsboost/getremainingboost` |
| **BetMakers** | Boost tokens, BonusBack tokens (event-specific + global) | GraphQL `getEligiblePromosForUser` + `getRedeemedPromoInfoForUser` |
| **Amused** | Bonus bets, promotions | REST API promo endpoints |
| **bet365** | Not supported (no promo API) | — |

### Key Details
- **SB Power Plays**: Captured from `/apigw/vouchers/customer/voucher` (requires Kasada cookies from browser)
- **SB Bet Returns**: Browser navigates to `/account/bet-returns` to scrape customer-facing descriptions with $ values (e.g. "Get up to $50 back in Bonus Bets")
- **SB Second Chance**: From `/apigw/preferred-promotions/v2/models/trending/customers/{id}/promotions`
- **TAB Bonus Bets**: Uses `TabcorpAuth` header (not Bearer) — promotions service rejects ROPC tokens
- **TAB promos require legacy auth** — the ROPC Bearer token doesn't have promotions scope

## WireGuard Auto-Recovery

The token farm on the mini PC connects to the VPS via WireGuard tunnel. Auto-recovery handles dynamic home IP:

1. **Mini PC** reports public IP to VPS every 5 minutes (`/opt/report-ip.sh` → SSH → `/opt/home-ip.txt`)
2. **VPS** checks tunnel health every 5 minutes (`/opt/wg-update-endpoint.sh`)
3. If tunnel is down and IP changed, VPS auto-updates WireGuard peer endpoint

No manual intervention needed when ISP changes your home IP.

## Key Technical Notes

- **Customer ID != Account Number** — TAB JWT `customerId` differs from `accountNumber`. Resolved via account-list endpoint.
- **Proxy required** — VPS is in the US. All AU bookie API calls go through per-account AU residential proxies (Oxylabs).
- **Deploy paths** — Caddy serves from `/opt/tab-betting-frontend/`, backend at `/opt/tab-betting-backend/`. NOT the `botops-*` paths.
- **Sessions persist** — App auth, TAB sessions, multi-bookie sessions all in PostgreSQL. Survive restarts.
- **Kasada bypass** — Patchright uses real system Chrome (matching UA + TLS fingerprint). UA must match Chrome version exactly.
- **TAB racing API is public** — No Bearer auth needed, just AU proxy for geo-restriction.
- **TAB racing betslip** — Uses `propositionNumber` from racing endpoint as `propositionId` in betslip. Flat leg structure (no nested propositions).
- **TAB promotions use legacy auth** — Bearer token gets 401 on promotions service. Must use `TabcorpAuth` header.
- **SB promos need browser cookies** — Voucher/freebet APIs require Kasada cookies. Token farm captures during login (navigates to /promotions, /account/bet-returns, /account/power-plays).
- **SB promo display** — Vouchers (power plays, bet returns) = actual tokens. Preferred-promotions = informational cards (type "Promo", not counted as tokens).
- **SB balance includes freebets** — `freebetAmount` from balance API shown as Bonus Cash.
- **PointsBet Kasada** — Auth endpoint (`auth.au.pointsbet.com/token`) blocked by Kasada in headless mode. Requires `Xvfb + headless=False` on the token farm. API endpoints (`api.au.pointsbet.com`) have NO Kasada.
- **PointsBet promo bets** — Add `promo: {promoCategory: "Token", promoId: "<uuid>", promoType: "PAYBACK"}` to bet payload. Token ID from `/api/promo-tokens/v2/tokens`.
- **PointsBet race URLs** — `/racing/{racingType}/{countryCode}/{venue}/race/{raceId}` (SPA routing).
- **PointsBet race prices** — NOT on runner objects. In `markets[]` array: `marketType: "FixedWin"/"FixedPlc"`, each with `selections[{runnerId, price, legacyMarketId}]`.
- **BetMakers promo bets** — Add `promotion_ids: ["<promo_id>"]` to the bet object in createBet mutation. Token is consumed server-side. EventSpecific tokens match by venue+race, Global tokens match by racing type.
- **BetMakers is_bonus_bet** — ONLY for bonus cash balance, NOT for promo tokens. `promotion_ids` is the field for token attachment.
- **Session cleanup** — Expired TAB sessions purged on startup. Health endpoint shows valid count. Manual purge via `POST /api/sessions/purge`.
- **bet365 has no API** — Everything browser-automated via Camoufox. Persistent sessions for bet placement.
- **bet365 balance takes 12-16s** — DOM elements appear quickly but dollar values populate slowly. Poll with retries.
- **Amused tokens expire in 300s** — Auto-refreshed before each operation.
- **BetMakers amounts in cents** — `amount: 500` = $5.00. Position always 0, is_boxed always false.
- **Systemd services** — `botops` on VPS, `token-farm` on mini PC. Use `systemctl restart` not manual kill/start.
- **Never assume credential issues** — When logins fail, debug proxy/browser/network. The user manages accounts daily and knows passwords are correct.

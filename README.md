# BotOps

Multi-bookie bot operations platform. Automated betting across TAB accounts with a web dashboard.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  Frontend (React/Vite)                                │
│  Signal-style dashboard, JetBrains Mono, dark theme  │
│  Caddy :8081                                          │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│  Backend (FastAPI)                                     │
│  Port 8001, systemd service                           │
│                                                       │
│  Auth0 ROPC Token ──► api.beta.tab.com.au             │
│    (pricing, matches, SGM markets)                    │
│                                                       │
│  Legacy TabcorpAuth ──► webapi.tab.com.au             │
│    (bet placement, bet history, account ops)          │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│  PostgreSQL                                           │
│  Accounts, sessions, bets — all persistent            │
└──────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites
- Ubuntu 24.04 (or any Linux with Python 3.12+)
- PostgreSQL 16
- Node.js 18+ (for frontend build only)
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

# Create .env (or copy the included one)
echo "HYPERSOLUTIONS_API_KEY=2f71b97d-0289-47e5-ba8e-d60c321f959a" > .env

# Run
uvicorn main:app --host 127.0.0.1 --port 8001 --log-level info
```

### 3. Frontend

```bash
cd frontend
npm install
npm run build
# Serve the dist/ folder via Caddy or any static server
```

### 4. Caddy Config

```
:8081 {
    handle /api/* {
        reverse_proxy localhost:8001
    }
    handle {
        root * /path/to/frontend/dist
        try_files {path} /index.html
        file_server
    }
}
```

### 5. Systemd Service

```bash
cat > /etc/systemd/system/botops.service << 'EOF'
[Unit]
Description=BotOps API
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/botops-backend
ExecStart=/usr/bin/uvicorn main:app --host 127.0.0.1 --port 8001 --log-level info
Restart=always
RestartSec=5
Environment=HYPERSOLUTIONS_API_KEY=2f71b97d-0289-47e5-ba8e-d60c321f959a

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable botops && systemctl start botops
```

## Deployment

Runs on any VPS with Ubuntu 24.04. Currently deployed on Hostinger KVM2 with Caddy reverse proxy.

| Component | Default |
|-----------|---------|
| Backend port | 8001 |
| Caddy port | 8081 |
| Backend path | `/opt/botops-backend/` |
| Frontend path | `/opt/botops-frontend/` |
| Database | PostgreSQL `tabbetting` |

### Deploy

```bash
# Backend — copy Python files, restart service
scp backend/*.py root@YOUR_VPS:/opt/botops-backend/
ssh root@YOUR_VPS 'systemctl restart botops'

# Frontend — build locally, copy dist
cd frontend && npm run build
scp -r dist/* root@YOUR_VPS:/opt/botops-frontend/
```

## Login Flow

Each TAB account login obtains two tokens:

1. **Auth0 ROPC** — `curl_cffi` (Chrome 142 TLS fingerprint) + HyperSolutions Akamai bypass + Auth0 password grant. Used for `api.beta.tab.com.au` (pricing, matches, SGM markets). Lasts ~9 hours.

2. **Legacy TabcorpAuth** — POST `/v1/account-service/tab/authenticate` with `{accountNumber, password, channel}`. Used for `webapi.tab.com.au` (bet placement, history). Lasts ~9 hours.

**Balance** uses the account-list endpoint which works with ROPC tokens and resolves the real TAB account number (different from customerId in the JWT).

**Key rule:** `api.beta.tab.com.au` uses `Authorization: Bearer` only. `webapi.tab.com.au` uses `TabcorpAuth` only. Never mix them — sending TabcorpAuth to api.beta causes 401, sending Bearer to webapi betslip causes 401.

## App Auth

Web app users are defined in `backend/main.py` (SHA256 hashed passwords). Add new users by adding entries to the `APP_USERS` dict.

Sessions persist in PostgreSQL — survive server restarts, stay logged in until explicit logout.

## TAB Accounts

Managed via the Dashboard UI. Each account needs:
- **Label** — display name (e.g., "Account1")
- **Email** — TAB login email
- **Password** — TAB login password
- **Proxy URL** — AU residential proxy in format `http://user:pass@host:port`
- **Account Number** — auto-detected on first login

Each account uses its own proxy for IP isolation. The VPS is in the US so all TAB API calls must go through an AU proxy.

## Database Schema

| Table | Purpose |
|-------|---------|
| `tab_accounts` | TAB credentials, proxy, account numbers |
| `app_sessions` | Persistent web app login tokens |
| `tab_sessions` | Persistent TAB auth tokens (Auth0 + legacy) |
| `bets` | Every bet placed through BotOps — legs (JSONB), odds, stake, status, payout, TSN |

Tables auto-created on first startup.

## Frontend Pages

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/` | Stat cards (bets, P/L, win rate) + account cards with table/card toggle |
| SGM Builder | `/betting` | Sport dropdown, match browser, prop grid, betslip with stake/liability mode |
| Multi Builder | `/multi` | Cross-game multi — one leg per game |
| JSON Upload | `/json` | Upload JSON bet array, select accounts, place sequentially with human delays |
| CSV Paste | `/csv` | Paste CSV from tipster, resolve against TAB, bulk place |
| History | `/history` | Our bets from DB — table/card views, filters, P/L stats, check results |

## Betting Modes

### SGM Builder (`/betting`)
Browse matches by sport dropdown → click match → pick props → betslip with price check → place.

### Multi Builder (`/multi`)
Pick one leg from each game → combine into cross-game multi → place.

### JSON Upload (`/json`)
Upload a JSON array of bets:
```json
[
  {
    "category": "sports",
    "is_same_event_multi": true,
    "sport": "afl",
    "event": "Fremantle v Melbourne",
    "legs": [
      {"market": "player_prop", "player": "Andrew Brayshaw", "stat": "disposals", "line": 20, "selection": "over"},
      {"market": "player_prop", "player": "Matthew Johnson", "stat": "disposals", "line": 25, "selection": "over"}
    ]
  }
]
```
Select accounts (ordered), set staking mode (from JSON / fixed stake / max liability), hit Place All. Bets placed sequentially with 2-6 second random delays between each. Auto-switches to next account when balance runs out.

### CSV Paste (`/csv`)
Paste CSV from tipster in either format:

**SGM:**
```
Bet Type,Game ID,Bet,Odds,SGM Min Odds,EV %,Units,Leg 1 TAB Odds,Leg 2 TAB Odds
SGM,20260327_GWS_COL,Billy Frampton 15+ Disposals/Phoenix Gothard 15+ Disposals,5.0,3.834,30.38%,1.5,2.6,2.05
```

**Multi (cross-game):**
```
Bet Type,Bet,Odds,Min Odds,EV %,Units,Teams,Leg 1 TAB Odds,Leg 2 TAB Odds
Multi,Luke Davies-Uniacke 30+ Disposals/Campbell Chesser 15+ Disposals,3.89,3.262,19.10%,1.7,,2.1,1.85
```

### Staking Modes
- **Fixed Stake** — flat dollar amount per bet
- **Max Liability** — set max payout, stake auto-calculated: `stake = liability / odds`, rounded down to a human number (under $10 = exact dollar, $10-99 = nearest $5 down, $100+ = nearest $10 down)

### Human-Like Delays
- 2-6 seconds between each bet (randomised)
- Every 15-25 bets: 1-2 minute break (randomised)
- Break interval itself randomised each cycle

## Bet Tracking & Results

Every bet placed through BotOps is saved to PostgreSQL with:
- Ticket serial number (TSN)
- Legs with descriptive names from TAB (e.g., "NBA Den-GSW 10+Pts Peyton Watson (DEN)")
- Combined odds, stake, account label
- Status: Pending → Won / Lost

**Check Results**: Click the button on History page — loops through all accounts with pending bets, queries TAB's my-bets API by TSN, updates Won/Lost with payout amounts. Each account can only see its own bets.

**P/L**: Calculated from settled bets only (Won + Lost). Pending bets don't affect P/L.

## API Endpoints

### Auth
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/login` | App login `{username, password}` → `{token}` |
| GET | `/api/auth/me` | Check auth |

### TAB Accounts
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/accounts` | List accounts from DB |
| POST | `/api/accounts` | Add/update account |
| DELETE | `/api/accounts/{id}` | Delete account |
| POST | `/api/accounts/sync` | Bulk sync from frontend |

### TAB Login
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/login` | Login to TAB `{email, password, proxy_url}` → `{session_id, balance}` |
| GET | `/api/active-sessions` | List active TAB sessions (for restoring state) |
| GET | `/api/balance?session_id=` | Check balance |
| GET | `/api/session?session_id=` | Session info |
| DELETE | `/api/session?session_id=` | Logout TAB session |

### Markets
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/matches?session_id=&sport=&competition=` | List matches |
| GET | `/api/sgm-markets/{match_id}?session_id=&sport=&competition=` | SGM props (falls back to regular markets if SGM empty) |

### Betting
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/price-check` | Price check `{session_id, propositions, stake, bet_type}` |
| POST | `/api/place-sgm` | Place SGM `{session_id, propositions, combined_odds, stake}` |
| POST | `/api/place-multi` | Place multi `{session_id, legs, stake}` |
| POST | `/api/place-json` | Place single bet from JSON format |
| POST | `/api/place-json-batch` | Place batch with human delays |
| POST | `/api/quick-resolve` | Resolve CSV rows to TAB propositions |
| POST | `/api/quick-place` | Resolve + place with human delays |

### History
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/bet-history?status=&account_number=&limit=` | Our bets from DB |
| POST | `/api/bets/check-results` | Check all pending bets across all accounts |

## Token Types

| Token | Source | Used For | Domain | Header | Duration |
|-------|--------|----------|--------|--------|----------|
| Auth0 ROPC | Auth0 password grant | Pricing, matches, SGM | `api.beta.tab.com.au` | `Authorization: Bearer` | ~9 hours |
| Legacy TabcorpAuth | `/account-service/tab/authenticate` | Bet placement, history | `webapi.tab.com.au` | `TabcorpAuth:` | ~9 hours |

## Key Technical Notes

- **Customer ID ≠ Account Number** — JWT `customerId` differs from TAB `accountNumber`. Resolved via `/customers/{customerId}/account-list?channel=TABCOMAU`.
- **SGM vs regular markets** — SGM player props open 12-24h before game. Regular markets (H2H, Line, Totals) available days in advance. API falls back automatically.
- **Akamai bypass** — HyperSolutions SDK, 3 sensor POSTs to `www.tab.com.au`. Required before Auth0 ROPC login.
- **Bet leg names** — TAB's placement response doesn't include leg names. After placement, the system queries `/my-bets` by TSN (with 1s delay) to get descriptive names.
- **Proxy required** — VPS is in the US. All TAB API calls go through per-account AU residential proxies.
- **Sessions persist** — both app auth and TAB sessions stored in PostgreSQL. Survive server restarts.

## Frontend Stack

- React 18 + Vite
- Tailwind CSS v4
- Lucide React (icons)
- JetBrains Mono font
- Signal Dashboard design language (oklch colors, dark sidebar, stat cards)
- Dark/Light theme toggle

## Backend Stack

- Python 3.12 + FastAPI
- curl_cffi (Chrome 142 TLS fingerprint)
- hyper-sdk (Akamai bypass)
- asyncpg (PostgreSQL)
- pydantic (models)

## Sport Options

Pre-configured in dropdown: AFL, NRL, NBA, NBL, NCAA Basketball, A-League Men, EPL, Champions League, Cricket BBL, Tennis ATP/WTA, NFL, MLB, NHL.

## Adding a New TAB Account

1. Go to Dashboard → Add Account
2. Fill in: Label, Email, TAB Password, Proxy URL (`http://user:pass@host:port`)
3. Account number auto-detected on first login
4. Click Login → balance appears, ready to bet

## Adding a New Bookie (Future)

The architecture is bookie-agnostic. To add a new bookie:
1. Create a new login module (like `login.py` for TAB)
2. Create betting API module (like `betting.py`)
3. Add endpoints to `main.py`
4. Update frontend with bookie selector

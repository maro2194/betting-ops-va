# BotOps — Project Instructions

## Overview

Multi-bookie bot operations platform. Automated betting across TAB accounts with a web dashboard.

- **Backend**: Python 3.12, FastAPI, asyncpg (PostgreSQL), curl_cffi
- **Frontend**: React 18, Vite, Tailwind CSS v4, Lucide React icons
- **Font**: JetBrains Mono (monospace only, no other fonts)
- **Deployed on**: Hostinger VPS 2 (`76.13.214.208`), Caddy reverse proxy

## Design System

**The circuit board IC chip logo is the brand identity. All UI must follow this design system.**

### Brand Assets

Located in `frontend/public/brand/`:
- `icon-sidebar.svg` — 32px sidebar icon (always on dark bg)
- `favicon.svg` — in `frontend/public/favicon.svg`
- `logo-dark.svg` — 512px full mark for dark backgrounds
- `logo-light.svg` — 512px full mark for light backgrounds
- `logo-wordmark-dark.svg` — horizontal logo + "BotOps" text (dark)
- `logo-wordmark-light.svg` — horizontal logo + "BotOps" text (light)
- `design-system.html` — full brand guidelines reference page

### Color Palette

| Token | Dark Mode | Light Mode | Usage |
|-------|-----------|------------|-------|
| Primary | `#00c896` | `#00a37a` | Accent, interactive elements, brand |
| Background | `oklch(8% .015 165)` | `oklch(97.5% .005 165)` | Page background |
| Card | `oklch(14% .015 165)` | `oklch(99.5% .002 165)` | Card surfaces |
| Text Primary | `oklch(87% .01 165)` | `oklch(13% .02 165)` | Body text |
| Text Secondary | `oklch(65% .01 165)` | `oklch(40% .02 165)` | Supporting text |
| Text Muted | `oklch(50% .015 165)` | `oklch(50% .015 165)` | Disabled/hint text |
| Border | `oklch(22% .012 165)` | `oklch(91% .008 165)` | Dividers, borders |
| Danger | `oklch(57.7% .245 27)` | same | Errors, destructive |
| Success | `oklch(62% .2 145)` | same | Win, positive |
| Warning | `oklch(75% .18 75)` | same | Caution states |
| Sidebar | Always dark (`oklch(10% .02 165)`) | Always dark | Sidebar bg |

### Design Rules

1. **Use CSS variables** from `index.css` — never hardcode colors in components
2. **Inline styles** with `var(--bg-card)`, `var(--text-primary)`, etc. — this is the established pattern
3. **Sidebar is always dark** regardless of theme — never apply light mode to sidebar
4. **JetBrains Mono only** — no other fonts, ever
5. **13px base font size** — set in `:root`
6. **oklch color space** — all theme colors use oklch for perceptual uniformity
7. **No gradients** — flat fills with opacity for depth
8. **Lucide React** for all icons — don't add other icon libraries
9. **Both themes must work** — test dark AND light mode for every UI change
10. **Signal Dashboard aesthetic** — clean, techy, monospace, information-dense

### Component Patterns

- Cards: `className="card"` (gets bg, border, shadow from CSS)
- Buttons: `className="btn btn-primary"`, `btn-secondary`, `btn-danger`, `btn-ghost`
- Inputs: `className="t-input"` (themed input styling)
- Badges: `className="badge badge-success"`, `badge-danger`, `badge-warning`
- Tables: `className="data-table"` (themed table)
- Section labels: `className="section-label"` (small caps, muted)

### Logo Usage

- Sidebar: `<img src="/brand/icon-sidebar.svg">` (32x32, always dark bg)
- Favicon: already set in `index.html`
- Splash/loading: use `logo-dark.svg` or `logo-light.svg` based on theme
- Never stretch, rotate, or recolor the logo
- Minimum clear space: 1x the chip width on all sides

## Project Structure

```
botops/
  backend/
    main.py          — FastAPI app, routes, auth, TAB session management
    betting.py        — TAB API client (matches, markets, pricing, placement)
    login.py          — TAB Auth0 ROPC + Akamai bypass login
    resolver.py       — CSV/JSON bet resolution against TAB markets
    database.py       — PostgreSQL (asyncpg) — accounts, sessions, bets
    models.py         — Pydantic models
    history.py        — Bet history queries
  frontend/
    src/
      App.jsx         — Routes
      api.js          — API client helper
      sportOptions.js — TAB sport/competition dropdown config
      context/        — AuthContext, SessionContext, ThemeContext
      components/     — Layout, NavBar
      pages/          — Dashboard, Betting, History, CsvPaste, MultiBuilder, JsonUpload, Disposals
    public/
      favicon.svg
      brand/          — All logo variants + design system reference
```

## Deployment

```bash
# Backend — copy Python files to VPS, restart
scp backend/*.py root@76.13.214.208:/opt/tab-betting-backend/
ssh root@76.13.214.208 'kill $(ss -tlnp | grep 8001 | grep -oP "pid=\K[0-9]+"); cd /opt/tab-betting-backend && nohup python3 /usr/local/bin/uvicorn main:app --host 127.0.0.1 --port 8001 --log-level info > /var/log/botops.log 2>&1 &'

# Frontend — build + copy dist
cd frontend && npx vite build
# Then upload dist/ to VPS /opt/tab-betting-frontend/
```

SSH password for VPS 2: `TostacoS-2023`

## Key Technical Notes

- **Two token types**: Auth0 ROPC (Bearer) for `api.beta.tab.com.au`, Legacy TabcorpAuth for `webapi.tab.com.au`. Never mix them.
- **Customer ID != Account Number** — resolved via account-list endpoint
- **Proxy required** — VPS is US-based, all TAB calls go through AU residential proxies
- **Sessions persist in PostgreSQL** — survive restarts
- **Akamai bypass** via HyperSolutions SDK — 3 sensor POSTs before Auth0 login

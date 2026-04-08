"""
Token Farm — Browser-based auth service for BotOps.

Runs on Mini PC (Proxmox Windows VM) behind WireGuard VPN.
Handles auth that requires real browsers: Sportsbet (Kasada), bet365 (Camoufox).

Start: uvicorn main:app --host 0.0.0.0 --port 9000
"""
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from sportsbet_auth import sportsbet_browser_login, sportsbet_token_refresh, sportsbet_get_promos
from pointsbet_auth import pointsbet_browser_login
from bet365_auth import bet365_browser_login, bet365_place_browser_bet, bet365_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("token-farm")

API_KEY = os.getenv("TOKEN_FARM_API_KEY", "botops-farm-2026")
security = HTTPBearer()


def verify_key(creds: HTTPAuthorizationCredentials = Depends(security)):
    if creds.credentials != API_KEY:
        raise HTTPException(401, "Invalid API key")
    return creds.credentials


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Token Farm starting...")
    yield
    logger.info("Token Farm shutting down")


app = FastAPI(title="BotOps Token Farm", lifespan=lifespan)

# Track active sessions for health reporting
_sessions: dict[str, dict] = {}
_start_time = time.time()


# ─── Models ──────────────────────────────────────────────────────────────────

class SBLoginRequest(BaseModel):
    email: str
    password: str
    proxy_url: str | None = None

class SBRefreshRequest(BaseModel):
    refresh_token: str
    proxy_url: str | None = None

class PBLoginRequest(BaseModel):
    email: str
    password: str
    proxy_url: str | None = None

class B365LoginRequest(BaseModel):
    username: str
    password: str
    proxy_url: str | None = None

class B365BetRequest(BaseModel):
    session_id: str
    bet: dict


# ─── Sportsbet endpoints ────────────────────────────────────────────────────

@app.post("/auth/sportsbet/login")
async def sb_login(req: SBLoginRequest, _=Depends(verify_key)):
    """Browser-based Sportsbet login (patchright + Kasada bypass)."""
    logger.info(f"SB login request for {req.email}")
    result = await sportsbet_browser_login(req.email, req.password, proxy_url=req.proxy_url)

    if result.get("success"):
        key = f"sb:{result.get('account_number', req.email)}"
        _sessions[key] = {
            "bookie": "sportsbet",
            "account": result.get("account_number"),
            "email": req.email,
            "expires_at": result.get("expires_at"),
            "logged_in_at": time.time(),
        }

    return result


@app.post("/auth/sportsbet/refresh")
async def sb_refresh(req: SBRefreshRequest, _=Depends(verify_key)):
    """Refresh Sportsbet JWT (no browser needed)."""
    return await sportsbet_token_refresh(req.refresh_token, proxy_url=req.proxy_url)


class SBPromosRequest(BaseModel):
    email: str
    password: str
    proxy_url: str | None = None

@app.post("/auth/sportsbet/promos")
async def sb_get_promos(req: SBPromosRequest, _=Depends(verify_key)):
    """Fetch Sportsbet promotions via browser login + API interception.

    Logs in via patchright browser, intercepts vouchers/freebets/promos
    responses that SB frontend auto-fetches after login.
    """
    logger.info(f"SB promos request for {req.email}")
    return await sportsbet_get_promos(req.email, req.password, proxy_url=req.proxy_url)


# ─── PointsBet endpoints ────────────────────────────────────────────────────

@app.post("/auth/pointsbet/login")
async def pb_login(req: PBLoginRequest, _=Depends(verify_key)):
    """Browser-based PointsBet login (patchright + Kasada bypass)."""
    logger.info(f"PointsBet login request for {req.email}")
    result = await pointsbet_browser_login(req.email, req.password, proxy_url=req.proxy_url)
    if result.get("success"):
        key = f"pb:{result.get('customer_id', req.email)}"
        _sessions[key] = {
            "bookie": "pointsbet",
            "customer_id": result.get("customer_id"),
            "email": req.email,
            "expires_at": result.get("expires_at"),
            "logged_in_at": time.time(),
        }
    return result


# ─── bet365 endpoints ───────────────────────────────────────────────────────

@app.post("/auth/bet365/login")
async def b365_login(req: B365LoginRequest, _=Depends(verify_key)):
    """Camoufox-based bet365 login."""
    logger.info(f"bet365 login request for {req.username}, proxy={'YES' if req.proxy_url else 'NONE'}")
    if req.proxy_url:
        logger.info(f"bet365 proxy: {req.proxy_url[:50]}...")
    result = await bet365_browser_login(req.username, req.password, proxy_url=req.proxy_url)

    if result.get("success"):
        sid = result.get("session_id", req.username)
        _sessions[f"b365:{sid}"] = {
            "bookie": "bet365",
            "username": req.username,
            "session_id": sid,
            "logged_in_at": time.time(),
        }

    return result


@app.get("/auth/bet365/status")
async def b365_get_status(_=Depends(verify_key)):
    """Check bet365 browser sessions."""
    return await bet365_status()


@app.post("/bet365/place-bet")
async def b365_place(req: B365BetRequest, _=Depends(verify_key)):
    """Place a bet on bet365 via browser automation."""
    return await bet365_place_browser_bet(req.session_id, req.bet)


# ─── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check(_=Depends(verify_key)):
    """Health endpoint for VPS monitoring."""
    now = time.time()
    active = {
        k: {**v, "age_seconds": int(now - v.get("logged_in_at", now))}
        for k, v in _sessions.items()
    }
    return {
        "status": "ok",
        "uptime_seconds": int(now - _start_time),
        "active_sessions": len(_sessions),
        "sessions": active,
    }

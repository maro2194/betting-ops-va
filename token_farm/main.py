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
from bet365_auth import bet365_browser_login, bet365_place_browser_bet, bet365_place_megaboost, bet365_megaboost_all, bet365_place_custom_sgm, bet365_list_boosts, bet365_status

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


class B365MegaboostRequest(BaseModel):
    session_id: str
    sport: str = "AFL"
    match_team: str
    stake: float = 20
    boost_index: int = 0


@app.post("/bet365/megaboost")
async def b365_megaboost(req: B365MegaboostRequest, _=Depends(verify_key)):
    """Place a Mega Boost / Bet Boost via browser automation."""
    return await bet365_place_megaboost(
        session_id=req.session_id,
        sport=req.sport,
        match_team=req.match_team,
        stake=req.stake,
        boost_index=req.boost_index,
    )


class B365CustomSGMRequest(BaseModel):
    session_id: str
    selections: list[dict]  # [{"player": "Patrick Voss", "odds": "13.00"}, ...]
    stake: float = 25
    apply_promo: bool = True


@app.post("/bet365/place-sgm")
async def b365_place_sgm(req: B365CustomSGMRequest, _=Depends(verify_key)):
    """Place a custom SGM by clicking individual selections on bet365."""
    return await bet365_place_custom_sgm(
        session_id=req.session_id,
        selections=req.selections,
        stake=req.stake,
        apply_promo=req.apply_promo,
    )


class B365MegaboostAllRequest(BaseModel):
    sport: str = "NRL"
    match_team: str
    stake: float = 20
    boost_index: int = 0
    accounts: list[str] | None = None  # Optional filter: list of usernames


@app.post("/bet365/megaboost-all")
async def b365_megaboost_all(req: B365MegaboostAllRequest, _=Depends(verify_key)):
    """Place megaboost across all configured bet365 accounts in parallel."""
    logger.info(f"bet365 megaboost-all: sport={req.sport} team={req.match_team} stake=${req.stake} filter={req.accounts}")
    return await bet365_megaboost_all(
        sport=req.sport,
        match_team=req.match_team,
        stake=req.stake,
        boost_index=req.boost_index,
        account_filter=req.accounts,
    )


@app.get("/bet365/list-boosts/{session_id}")
async def b365_list_boosts(session_id: str, _=Depends(verify_key)):
    """List all visible boost cards on bet365 homepage."""
    return await bet365_list_boosts(session_id)


class B365ClickRequest(BaseModel):
    session_id: str
    text: str
    index: int = 0  # Which occurrence to click (0 = first)
    exact: bool = True


@app.post("/bet365/click")
async def b365_click(req: B365ClickRequest, _=Depends(verify_key)):
    """Click a visible text element on the page and return updated page text."""
    from bet365_auth import _browser_sessions
    s = _browser_sessions.get(req.session_id)
    if not s:
        return {"error": f"No session {req.session_id}"}
    page = s["page"]
    try:
        els = page.get_by_text(req.text, exact=req.exact)
        count = await els.count()
        clicked = False
        idx = 0
        for i in range(count):
            if await els.nth(i).is_visible():
                if idx == req.index:
                    bbox = await els.nth(i).bounding_box()
                    # Use mouse click at center of element (more reliable for bet365)
                    if bbox:
                        await page.mouse.click(bbox["x"] + bbox["width"]/2, bbox["y"] + bbox["height"]/2)
                    else:
                        await els.nth(i).click()
                    clicked = True
                    break
                idx += 1
        if not clicked:
            return {"error": f"'{req.text}' not found (visible count={count})", "clicked": False}
        await page.wait_for_timeout(3000)
        body = await page.evaluate("document.body.innerText")
        return {"clicked": True, "text": body[:5000]}
    except Exception as e:
        return {"error": str(e), "clicked": False}


class B365NavRequest(BaseModel):
    session_id: str
    url: str


@app.post("/bet365/navigate")
async def b365_navigate(req: B365NavRequest, _=Depends(verify_key)):
    """Navigate a bet365 session to a specific URL and return page text."""
    from bet365_auth import _browser_sessions
    s = _browser_sessions.get(req.session_id)
    if not s:
        return {"error": f"No session {req.session_id}"}
    page = s["page"]
    await page.goto(req.url, timeout=30000)
    await page.wait_for_timeout(5000)
    # Dismiss cookies
    try:
        await page.evaluate("""() => {
            const buttons = document.querySelectorAll('button, div[role="button"], a');
            for (const btn of buttons) {
                if ((btn.textContent || '').trim() === 'Accept All') { btn.click(); return; }
            }
        }""")
        await page.wait_for_timeout(2000)
    except Exception:
        pass
    body = await page.evaluate("document.body.innerText")
    return {"url": page.url, "text": body[:5000]}


class B365EvalRequest(BaseModel):
    session_id: str
    js: str


@app.post("/bet365/eval")
async def b365_eval(req: B365EvalRequest, _=Depends(verify_key)):
    """Run arbitrary JS on a bet365 session page."""
    from bet365_auth import _browser_sessions
    s = _browser_sessions.get(req.session_id)
    if not s:
        return {"error": f"No session {req.session_id}"}
    page = s["page"]
    try:
        result = await page.evaluate(req.js)
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}


@app.get("/bet365/debug/{session_id}")
async def b365_debug(session_id: str, _=Depends(verify_key)):
    """Debug: screenshot + page text for a bet365 session."""
    from bet365_auth import _browser_sessions
    s = _browser_sessions.get(session_id)
    if not s:
        return {"error": f"No session {session_id}", "active": list(_browser_sessions.keys())}
    page = s["page"]
    await page.screenshot(path="/tmp/b365_debug.png")
    body = await page.evaluate("document.body.innerText")
    url = page.url
    return {"url": url, "text": body[:3000]}


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

"""
Sportsbet API routes — token management, racing data, bet placement.
Uses local-login → refresh-token pattern (browser login on local machine,
VPS refreshes token via proxy).
"""
import json
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from curl_cffi.requests import Session

logger = logging.getLogger("sportsbet_routes")

router = APIRouter(prefix="/api/sportsbet", tags=["sportsbet"])

# Config
SB_TOKENS_FILE = os.path.join(os.path.dirname(__file__), "sb_tokens.json")
SB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
SB_PROXIES = [
    "http://FuTUVMvrSTa9cYM8:XZbc7POb6z75bzCb_country-au@geo.iproyal.com:12321",
]


def _load_tokens() -> dict:
    if os.path.exists(SB_TOKENS_FILE):
        with open(SB_TOKENS_FILE) as f:
            return json.load(f)
    return {}


def _save_tokens(tokens: dict):
    with open(SB_TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def _get_proxy() -> str:
    return SB_PROXIES[0]


def _make_session() -> Session:
    session = Session(impersonate="chrome131")
    proxy = _get_proxy()
    session.proxies = {"http": proxy, "https": proxy}
    return session


def _refresh_token(tokens: dict) -> dict | None:
    """Refresh the access token using the stored refresh token."""
    refresh = tokens.get("refresh_token")
    if not refresh:
        return None

    session = _make_session()
    try:
        resp = session.post("https://www.sportsbet.com.au/apigw/ciam/token", headers={
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json",
            "user-agent": SB_UA,
            "origin": "https://www.sportsbet.com.au",
        }, data=f"grant_type=refresh_token&refresh_token={refresh}", timeout=15)

        if resp.status_code == 200:
            data = resp.json()
            if "access_token" in data:
                tokens["access_token"] = data["access_token"]
                tokens["refresh_token"] = data.get("refresh_token", refresh)
                # Decode expiry
                import base64
                parts = data["access_token"].split(".")
                payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
                tokens["token_exp"] = claims.get("exp", 0)
                tokens["customer_id"] = str(claims.get("sub", tokens.get("customer_id", "")))
                _save_tokens(tokens)
                return tokens
        return None
    except Exception as e:
        logger.error(f"SB refresh failed: {e}")
        return None
    finally:
        session.close()


def _ensure_token() -> dict:
    """Get a valid access token, refreshing if needed."""
    tokens = _load_tokens()
    if not tokens.get("access_token"):
        raise HTTPException(503, "No Sportsbet tokens. Run sb_local_login.py on your machine first.")

    # Check expiry (with 5 min buffer)
    exp = tokens.get("token_exp", 0)
    if time.time() > exp - 300:
        refreshed = _refresh_token(tokens)
        if not refreshed:
            raise HTTPException(401, "Sportsbet token expired and refresh failed. Re-login locally.")
        return refreshed

    return tokens


def _sb_get(path: str, tokens: dict) -> dict:
    """Make an authenticated GET to Sportsbet API."""
    session = _make_session()
    try:
        resp = session.get(f"https://www.sportsbet.com.au{path}", headers={
            "accesstoken": tokens["access_token"],
            "apptoken": "cxp-desktop-web",
            "channel": "cxp",
            "user-agent": SB_UA,
            "accept": "application/json",
        }, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        else:
            raise HTTPException(resp.status_code, f"SB API error: {resp.text[:200]}")
    finally:
        session.close()


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/status")
async def sb_status():
    """Get Sportsbet connection status."""
    tokens = _load_tokens()
    has_token = bool(tokens.get("access_token"))
    exp = tokens.get("token_exp", 0)
    valid = time.time() < exp if exp else False
    return {
        "connected": has_token and valid,
        "has_tokens": has_token,
        "token_valid": valid,
        "expires_at": exp,
        "expires_in": max(0, int(exp - time.time())) if exp else 0,
        "customer_id": tokens.get("customer_id"),
        "email": tokens.get("email"),
    }


@router.post("/refresh")
async def sb_refresh():
    """Force refresh the access token."""
    tokens = _load_tokens()
    if not tokens.get("refresh_token"):
        raise HTTPException(503, "No refresh token. Run sb_local_login.py locally.")
    refreshed = _refresh_token(tokens)
    if refreshed:
        return {"ok": True, "expires_at": refreshed.get("token_exp")}
    raise HTTPException(500, "Refresh failed")


@router.get("/balance")
async def sb_balance():
    """Get Sportsbet account balance."""
    tokens = _ensure_token()
    data = _sb_get("/apigw/accounts/balance?pendingbetcount=true&freebetcount=true", tokens)
    return {
        "balance": data.get("balance", 0),
        "available": data.get("availableFunds", 0),
        "withdrawable": data.get("withdrawableFunds", 0),
        "freebet": data.get("freebetAmount", 0),
    }


@router.get("/racing")
async def sb_racing():
    """Get all upcoming races."""
    tokens = _ensure_token()
    data = _sb_get("/apigw/sportsbook-racing/Sportsbook/Racing/AllRacing/sportsbet", tokens)
    # Parse dates/meetings/races
    dates = data.get("dates", [])
    races = []
    for d in dates:
        for meeting in d.get("meetings", []):
            track = meeting.get("meetingName", "")
            for event in meeting.get("events", []):
                races.append({
                    "id": event.get("id"),
                    "track": track,
                    "race_number": event.get("raceNumber"),
                    "name": event.get("raceName", ""),
                    "start_time": event.get("startTime"),
                    "status": event.get("raceStatus"),
                })
    return {"races": races}


@router.get("/racing/{event_id}/runners")
async def sb_runners(event_id: int):
    """Get runners + odds for a race."""
    tokens = _ensure_token()
    data = _sb_get(f"/apigw/sportsbook-racing/Sportsbook/Racing/Events/{event_id}/Racecard", tokens)
    runners = []
    for market in data.get("markets", []):
        for sel in market.get("selections", []):
            prices = sel.get("prices", [])
            win_price = 0
            place_price = 0
            for p in prices:
                if p.get("priceCode") == "L":
                    win_price = 1 + p.get("priceDec", 0)
                elif p.get("priceCode") == "P":
                    place_price = 1 + p.get("priceDec", 0)
            runners.append({
                "id": sel.get("id"),
                "name": sel.get("name"),
                "number": sel.get("runnerNumber"),
                "jockey": sel.get("jockey"),
                "trainer": sel.get("trainer"),
                "win_odds": round(win_price, 2),
                "place_odds": round(place_price, 2),
                "status": sel.get("statusCode"),
            })
    return {"runners": runners, "event_id": event_id}


@router.get("/sports/events/{event_id}/markets")
async def sb_event_markets(event_id: int):
    """Get ALL markets for a sports event (granular player props)."""
    tokens = _ensure_token()
    data = _sb_get(f"/apigw/sportsbook-sports/Sportsbook/Sports/Events/{event_id}/Markets", tokens)
    if not isinstance(data, list):
        return {"markets": [], "total": 0}

    markets = []
    for m in data:
        selections = []
        for s in m.get("selections", []):
            price = s.get("price", {})
            selections.append({
                "id": s.get("id"),
                "name": s.get("name"),
                "odds": price.get("winPrice", 0),
                "price_num": price.get("winPriceNum", 0),
                "price_den": price.get("winPriceDen", 0),
                "status": s.get("statusCode"),
            })
        markets.append({
            "id": m.get("id"),
            "name": m.get("name"),
            "sgm": m.get("sameGameMultiEnabled", False),
            "selections": selections,
        })

    return {"markets": markets, "total": len(markets)}


class SBUploadTokens(BaseModel):
    access_token: str
    refresh_token: str
    customer_id: str = ""
    email: str = ""


@router.post("/tokens")
async def sb_upload_tokens(req: SBUploadTokens):
    """Upload tokens from local login."""
    import base64
    try:
        parts = req.access_token.split(".")
        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp", 0)
    except:
        exp = 0

    tokens = {
        "access_token": req.access_token,
        "refresh_token": req.refresh_token,
        "customer_id": req.customer_id or str(claims.get("sub", "")),
        "email": req.email,
        "token_exp": exp,
        "saved_at": time.time(),
    }
    _save_tokens(tokens)
    return {"ok": True, "expires_at": exp}

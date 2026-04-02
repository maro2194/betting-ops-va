"""
Sportsbet API routes — token management, racing data, bet placement.
Uses local-login → refresh-token pattern (browser login on local machine,
VPS refreshes token via proxy).
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
from curl_cffi.requests import Session

from database import get_app_session, get_bets

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


# ─── Live Stats ──────────────────────────────────────────────────────────────

@router.get("/live-stats/{match_id}")
async def sb_live_stats(match_id: int):
    """Get live AFL player stats from Footywire."""
    from live_stats import get_live_stats
    return get_live_stats(match_id)


@router.get("/live-stats/find/current")
async def sb_find_current_match():
    """Find the current live AFL match ID."""
    from live_stats import find_current_match_id
    mid = find_current_match_id()
    return {"match_id": mid}


# ─── Auth dependency (mirrors multi_routes.py pattern) ──────────────────────

async def _verify_app_token(authorization: str = Header(None)) -> dict:
    """Verify app auth token from Authorization header."""
    if not authorization:
        raise HTTPException(401, "Missing authorization. Please login to the app first.")
    token = authorization.replace("Bearer ", "").replace("bearer ", "")
    from main import app_tokens
    if token in app_tokens:
        return app_tokens[token]
    session = await get_app_session(token)
    if session:
        app_tokens[token] = {
            "username": session["username"],
            "name": session["name"],
            "created_at": session["created_at"],
        }
        return app_tokens[token]
    raise HTTPException(401, "Invalid or expired app token. Please login again.")


# ─── Leg parsing helpers ─────────────────────────────────────────────────────

# Patterns for disposal lines in bet leg names:
# "AFL Brs-Col 20Disps Nick Daicos (COL)"
# "AFL Brs-Col 15Disps Harry Perryman (COL) @Price:1.48"
# "Nick Daicos (COL) — 20+ Disposals"
_AFL_DISPS_RE = re.compile(
    r'(\d+)\s*Disps?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z\']+)+)\s*\(',
    re.IGNORECASE,
)
_ALT_DISPS_RE = re.compile(
    r'([A-Z][a-z]+(?:\s+[A-Z][a-z\']+)+)\s*\([^)]+\)\s*[—\-]+\s*(\d+)\+?\s*Disposals',
    re.IGNORECASE,
)


def _parse_disposal_leg(leg_name: str) -> Optional[dict]:
    """Return {player, line} for a disposal leg, or None if not parseable."""
    # Strip @Price suffix first
    name = re.sub(r'\s*@Price:[^\s]+', '', leg_name).strip()

    m = _AFL_DISPS_RE.search(name)
    if m:
        return {"player": m.group(2).strip(), "line": int(m.group(1))}

    m = _ALT_DISPS_RE.search(name)
    if m:
        return {"player": m.group(1).strip(), "line": int(m.group(2))}

    return None


def _match_player(full_name: str, live_players: list[dict]) -> Optional[dict]:
    """Match a full name like 'Nick Daicos' against Footywire abbreviated names like 'N Daicos'.
    Returns the matching live player dict or None."""
    parts = full_name.strip().split()
    if len(parts) < 2:
        return None
    first_initial = parts[0][0].upper()
    last_name = parts[-1].lower()

    for p in live_players:
        lname = p.get("name", "")
        p_parts = lname.strip().split()
        if len(p_parts) < 2:
            continue
        p_initial = p_parts[0][0].upper()
        p_last = p_parts[-1].lower()
        if p_last == last_name and p_initial == first_initial:
            return p
    return None


# ─── Live P/L endpoint ───────────────────────────────────────────────────────

@router.get("/live-pnl")
async def sb_live_pnl(match_id: int, user: dict = Depends(_verify_app_token)):
    """Cross-reference today's pending bets with live disposal counts for a match.

    Returns per-bet status (winning/losing/partial/unknown) and summary P/L projections.
    """
    from live_stats import get_live_stats

    # Fetch live stats for the match
    stats_data = get_live_stats(match_id)
    live_players = stats_data.get("players", [])

    # Fetch today's pending bets for this user (AEST)
    aest = timezone(timedelta(hours=10))
    today_str = datetime.now(aest).strftime("%Y-%m-%d")
    all_bets = await get_bets(
        user["username"],
        status="Pending",
        date_from=today_str,
        date_to=today_str,
    )

    # Analyse each bet
    bet_results = []
    for bet in all_bets:
        legs = bet.get("legs") or []
        leg_analyses = []
        has_disposal_leg = False

        for leg in legs:
            leg_name = leg.get("name", "") if isinstance(leg, dict) else str(leg)
            parsed = _parse_disposal_leg(leg_name)

            if parsed is None:
                # Not a disposal leg — skip but record
                leg_analyses.append({
                    "name": leg_name,
                    "type": "other",
                    "hitting": None,
                    "current": None,
                    "line": None,
                    "player_found": False,
                })
                continue

            has_disposal_leg = True
            live_player = _match_player(parsed["player"], live_players)
            if live_player is None:
                leg_analyses.append({
                    "name": leg_name,
                    "type": "disposal",
                    "player": parsed["player"],
                    "line": parsed["line"],
                    "hitting": None,
                    "current": None,
                    "player_found": False,
                })
                continue

            current = live_player.get("disposals", 0)
            hitting = current >= parsed["line"]
            leg_analyses.append({
                "name": leg_name,
                "type": "disposal",
                "player": parsed["player"],
                "player_name_live": live_player.get("name"),
                "line": parsed["line"],
                "current": current,
                "hitting": hitting,
                "player_found": True,
            })

        # Determine overall bet status from disposal legs only
        disposal_legs = [l for l in leg_analyses if l["type"] == "disposal" and l["player_found"]]
        unknown_legs = [l for l in leg_analyses if l["type"] == "disposal" and not l["player_found"]]

        if not has_disposal_leg:
            status = "unknown"
        elif not disposal_legs and unknown_legs:
            status = "unknown"
        else:
            hitting_count = sum(1 for l in disposal_legs if l["hitting"])
            total_known = len(disposal_legs)
            if total_known == 0:
                status = "unknown"
            elif hitting_count == total_known:
                status = "winning"
            elif hitting_count == 0:
                status = "losing"
            else:
                status = "partial"

        try:
            combined_odds = float(bet.get("combined_odds", 0))
            stake = float(bet.get("stake", 0))
        except (TypeError, ValueError):
            combined_odds = 0.0
            stake = 0.0

        potential_return = round(stake * combined_odds, 2)

        bet_results.append({
            "id": bet["id"],
            "bet_type": bet.get("bet_type"),
            "account_label": bet.get("account_label"),
            "combined_odds": combined_odds,
            "stake": stake,
            "potential_return": potential_return,
            "placed_at": bet.get("placed_at"),
            "status": status,
            "legs": leg_analyses,
        })

    # Summary totals
    projected_win = sum(b["potential_return"] for b in bet_results if b["status"] == "winning")
    projected_loss_stake = sum(b["stake"] for b in bet_results if b["status"] == "losing")
    total_stake = sum(b["stake"] for b in bet_results)
    # Floor = if all partial/winning bets lose, ceiling = if all partial bets also win
    partial_bets = [b for b in bet_results if b["status"] == "partial"]
    ceiling = projected_win + sum(b["potential_return"] for b in partial_bets)
    floor_loss = projected_loss_stake + sum(b["stake"] for b in partial_bets)

    return {
        "match_id": match_id,
        "match_title": stats_data.get("title", ""),
        "live_players_count": len(live_players),
        "bets": bet_results,
        "summary": {
            "total_bets": len(bet_results),
            "winning": sum(1 for b in bet_results if b["status"] == "winning"),
            "losing": sum(1 for b in bet_results if b["status"] == "losing"),
            "partial": sum(1 for b in bet_results if b["status"] == "partial"),
            "unknown": sum(1 for b in bet_results if b["status"] == "unknown"),
            "total_stake": round(total_stake, 2),
            "projected_win": round(projected_win, 2),
            "projected_loss": round(projected_loss_stake, 2),
            "ceiling": round(ceiling, 2),
            "floor_loss": round(floor_loss, 2),
            "live_pnl": round(projected_win - projected_loss_stake, 2),
        },
    }

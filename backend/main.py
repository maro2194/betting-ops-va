"""
TAB Betting Web App - FastAPI Backend.
Manages browser sessions, token caching, and proxied API calls.
"""
import asyncio
import hashlib
import logging
import os
import re
import secrets
import time
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware

from login import browser_login, decode_token_claims
from betting import (
    get_balance, get_account_info, legacy_authenticate, get_matches,
    get_match_markets, get_sgm_propositions, price_check, place_sgm_bet,
    place_multi_bet, get_my_bets,
)
from resolver import resolve_and_price
from database import (
    init_db, close_db, get_accounts, upsert_account, delete_account, sync_accounts,
    save_app_session, get_app_session, delete_app_session, load_all_app_sessions,
    save_tab_session, load_all_tab_sessions, delete_tab_session,
    save_bet, get_bets, update_bet_status, get_pending_bets,
)

from models import (
    LoginRequest, LoginResponse, BalanceResponse,
    PriceCheckRequest, PriceCheckResponse,
    PlaceBetRequest, PlaceMultiRequest, PlaceBetResponse,
    BetHistoryResponse, SGMMarketResponse,
)

# Load .env from parent dir
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="TAB Betting API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await init_db()
    # Restore sessions from DB
    global app_tokens, sessions
    app_tokens.update(await load_all_app_sessions())
    sessions.update(await load_all_tab_sessions())
    logger.info(f"Restored {len(app_tokens)} app sessions and {len(sessions)} TAB sessions from DB")


@app.on_event("shutdown")
async def shutdown():
    await close_db()

# ─── App Auth (user accounts for accessing this app) ────────────────────────

APP_USERS = {
    "maro": {"password_hash": "aeb2f863f78934afccf548e82d786e0ed39fb52c2dfb23663d8333dd6b608ca5", "name": "Maro"},
    "diji": {"password_hash": "0489c36a7155b8b671acbab078697dd365e1da635343fa63fee1415b7af516e8", "name": "Diji"},
    "shadow": {"password_hash": "224e690ddf7786edfd76b8cc372fffad88be3c5694268e1e439fa9740080bd18", "name": "Shadow"},
}

# Active app auth tokens: token -> {username, name, created_at}
app_tokens: dict[str, dict] = {}


class AppAuthRequest(BaseModel if 'BaseModel' in dir() else object):
    username: str
    password: str

from pydantic import BaseModel as _BaseModel

class AppAuthRequest(_BaseModel):
    username: str
    password: str


async def _verify_app_token(authorization: str = Header(None)) -> dict:
    """Verify app auth token from Authorization header."""
    if not authorization:
        raise HTTPException(401, "Missing authorization. Please login to the app first.")
    token = authorization.replace("Bearer ", "").replace("bearer ", "")
    if token in app_tokens:
        return app_tokens[token]
    # Check DB (might have been loaded by another process)
    session = await get_app_session(token)
    if session:
        app_tokens[token] = {"username": session["username"], "name": session["name"], "created_at": session["created_at"]}
        return app_tokens[token]
    raise HTTPException(401, "Invalid or expired app token. Please login again.")


@app.post("/api/auth/login")
async def app_auth_login(req: AppAuthRequest):
    """Login to the app itself (not TAB)."""
    user = APP_USERS.get(req.username.lower())
    if not user:
        raise HTTPException(401, "Invalid username")
    pw_hash = hashlib.sha256(req.password.encode()).hexdigest()
    if pw_hash != user["password_hash"]:
        raise HTTPException(401, "Invalid password")
    token = secrets.token_hex(32)
    created_at = time.time()
    app_tokens[token] = {
        "username": req.username.lower(),
        "name": user["name"],
        "created_at": created_at,
    }
    await save_app_session(token, req.username.lower(), user["name"], created_at)
    return {"token": token, "username": req.username.lower(), "name": user["name"]}


@app.get("/api/auth/me")
async def app_auth_me(user: dict = Depends(_verify_app_token)):
    """Check current app auth."""
    return {"username": user["username"], "name": user["name"]}


# ─── TAB Session Store ───────────────────────────────────────────────────────

# In-memory session store: session_id -> session_data
# session_data: {token, legacy_token, account_number, customer_id, email, password, proxy_url, profile_dir, logged_in_at}
sessions: dict[str, dict] = {}

# Lock to prevent concurrent logins for the same email
login_locks: dict[str, asyncio.Lock] = {}

PROFILE_BASE_DIR = os.path.join(os.path.dirname(__file__), "profiles")


def _get_session(session_id: str) -> dict:
    """Get session or raise 401."""
    if session_id not in sessions:
        raise HTTPException(status_code=401, detail="Invalid session. Please login again.")
    s = sessions[session_id]
    # Check token expiry
    claims = decode_token_claims(s["token"])
    exp = claims.get("exp", 0)
    if exp and time.time() > exp - 300:  # 5 min buffer
        raise HTTPException(status_code=401, detail="Token expired. Please login again.")
    return s


# ─── Login ───────────────────────────────────────────────────────────────────

@app.post("/api/login", response_model=LoginResponse)
async def api_login(req: LoginRequest, _user: dict = Depends(_verify_app_token)):
    """Login to TAB via browser automation. Returns session_id for subsequent calls."""
    email = req.email.strip()
    password = req.password
    proxy_url = req.proxy_url.strip()

    # Validate proxy format
    if not re.match(r'https?://[^:]+:[^@]+@[^:]+:\d+', proxy_url):
        raise HTTPException(400, "Invalid proxy format. Expected: http://user:pass@host:port")

    # Prevent concurrent logins for same email
    if email not in login_locks:
        login_locks[email] = asyncio.Lock()

    async with login_locks[email]:
        # Check if we already have a valid session for this email
        for sid, s in sessions.items():
            if s["email"] == email:
                claims = decode_token_claims(s["token"])
                exp = claims.get("exp", 0)
                if exp and time.time() < exp - 600:  # Still valid for >10 min
                    logger.info(f"Reusing existing session for {email}")
                    try:
                        bal = get_balance(s["token"], s["customer_id"], s["proxy_url"])
                        return LoginResponse(
                            session_id=sid,
                            account_number=s["account_number"],
                            customer_id=s["customer_id"],
                            balance=bal["account_balance"],
                            email=email,
                        )
                    except Exception:
                        # Token might be dead, re-login
                        del sessions[sid]
                        break

        try:
            logger.info(f"Starting browser login for {email}...")
            result = await browser_login(email, password, proxy_url)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"Login failed: {e}\n{tb}")
            raise HTTPException(500, f"Login failed: {type(e).__name__}: {str(e) or tb.split(chr(10))[-2]}")

        # Resolve real account number and balance via account-list endpoint
        customer_id = result["customer_id"]
        try:
            acct_info = get_account_info(result["token"], customer_id, proxy_url)
            real_account_number = acct_info["account_number"]
            balance_str = acct_info["account_balance"]
            logger.info(f"Resolved account: customer_id={customer_id} → account_number={real_account_number}, balance={balance_str}")
        except Exception as e:
            logger.warning(f"Account info lookup failed: {e}")
            real_account_number = req.account_number or customer_id
            balance_str = "N/A"

        # Get legacy TabcorpAuth token for betting + history
        account_number = req.account_number or real_account_number
        legacy_token = ""
        try:
            legacy_result = legacy_authenticate(account_number, password, proxy_url)
            legacy_token = legacy_result["legacy_token"]
            logger.info(f"Legacy auth successful for account {account_number}")
        except Exception as e:
            logger.warning(f"Legacy auth failed (betting/history will not work): {e}")

        # Create session
        session_id = str(uuid.uuid4())
        profile_dir = os.path.join(PROFILE_BASE_DIR, re.sub(r'[^a-zA-Z0-9]', '_', email))

        session_data = {
            "token": result["token"],
            "legacy_token": legacy_token,
            "account_number": account_number,
            "customer_id": customer_id,
            "email": email,
            "password": password,
            "proxy_url": proxy_url,
            "profile_dir": profile_dir,
            "logged_in_at": time.time(),
        }
        sessions[session_id] = session_data
        # Persist to DB
        claims = decode_token_claims(result["token"])
        session_data["token_exp"] = claims.get("exp")
        await save_tab_session(session_id, session_data)

        return LoginResponse(
            session_id=session_id,
            account_number=account_number,
            customer_id=customer_id,
            balance=balance_str,
            email=email,
        )


# ─── Balance ─────────────────────────────────────────────────────────────────

@app.get("/api/balance")
async def api_balance(session_id: str, _user: dict = Depends(_verify_app_token)) -> BalanceResponse:
    """Get current account balance."""
    s = _get_session(session_id)
    try:
        bal = get_balance(s["token"], s["customer_id"], s["proxy_url"])
        return BalanceResponse(
            account_balance=bal["account_balance"],
            withdrawal_balance=bal["withdrawal_balance"],
        )
    except Exception as e:
        raise HTTPException(500, f"Balance check failed: {str(e)}")


# ─── Matches ─────────────────────────────────────────────────────────────────

@app.get("/api/matches")
async def api_matches(session_id: str, sport: str = "AFL Football", competition: str = "AFL", _user: dict = Depends(_verify_app_token)):
    """Get upcoming matches."""
    s = _get_session(session_id)
    try:
        matches = get_matches(s["token"], s["proxy_url"], sport, competition)
        return {"matches": matches}
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch matches: {str(e)}")


# ─── SGM Propositions ───────────────────────────────────────────────────────

@app.get("/api/sgm-markets/{match_id}")
async def api_sgm_markets(match_id: str, session_id: str,
                          sport: str = "AFL Football", competition: str = "AFL", _user: dict = Depends(_verify_app_token)):
    """Get SGM propositions for a match. Falls back to regular markets if SGM is empty."""
    s = _get_session(session_id)
    try:
        data = get_sgm_propositions(s["token"], match_id, s["proxy_url"], sport, competition)
        # Check if SGM has actual propositions
        has_sgm = False
        if data.get("data"):
            for section in data["data"]:
                for market in section.get("data", []):
                    for group in market.get("data", []):
                        if group.get("propositions"):
                            has_sgm = True
                            break

        if has_sgm:
            return data

        # SGM empty — fetch regular match markets and convert to same format
        logger.info(f"SGM empty for {match_id}, falling back to regular markets")
        match_data = get_match_markets(s["token"], match_id, s["proxy_url"], sport, competition)
        regular_markets = match_data.get("markets", [])
        if not regular_markets:
            return data  # Return empty SGM response

        # Convert regular markets to SGM-like format for the frontend
        converted = []
        for m in regular_markets:
            props = []
            for p in m.get("propositions", []):
                if p.get("returnWin") and p.get("returnWin") > 0:
                    props.append({
                        "id": str(p.get("id", "")),
                        "numberId": p.get("id"),
                        "number": p.get("id"),
                        "name": p.get("name", ""),
                        "returnWin": p.get("returnWin", 0),
                        "bettingStatus": p.get("bettingStatus", "Open"),
                        "isOpen": p.get("bettingStatus") == "Open",
                    })
            if props:
                converted.append({
                    "betOption": m.get("betOption", "Unknown"),
                    "type": "sports.propositions.horizontal",
                    "data": [{"propositions": props, "sameGameMultipleSelections": False}],
                })

        return {
            "type": "sports.match.markets",
            "data": [{"title": "Match Markets", "data": converted}],
            "_source": "regular_markets",
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch markets: {str(e)}")


# ─── Price Check ─────────────────────────────────────────────────────────────

@app.post("/api/price-check")
async def api_price_check(req: PriceCheckRequest, _user: dict = Depends(_verify_app_token)) -> PriceCheckResponse:
    """Get combined odds for a set of propositions."""
    s = _get_session(req.session_id)
    props = [{"propositionId": p.proposition_id, "odds": p.odds} for p in req.propositions]
    try:
        result = price_check(s["token"], props, req.stake, req.bet_type, s["proxy_url"])
        return PriceCheckResponse(
            combined_odds=result.get("combined_odds", "0"),
            propositions=props,
        )
    except Exception as e:
        raise HTTPException(500, f"Price check failed: {str(e)}")


# ─── Place SGM Bet ───────────────────────────────────────────────────────────

@app.post("/api/place-sgm", response_model=PlaceBetResponse)
async def api_place_sgm(req: PlaceBetRequest, _user: dict = Depends(_verify_app_token)):
    """Place an SGM bet."""
    s = _get_session(req.session_id)
    if not s.get("legacy_token"):
        raise HTTPException(400, "Legacy token not available. Re-login to enable betting.")
    props = [{"propositionId": p.proposition_id, "odds": p.odds} for p in req.propositions]
    try:
        result = place_sgm_bet(
            s["legacy_token"], s["account_number"], props,
            req.combined_odds, req.stake, s["proxy_url"],
        )
        # Save to our DB if successful
        if result.get("success"):
            acct_label = s.get("email", "")
            try:
                accounts = await get_accounts(_user["username"])
                for a in accounts:
                    if a["account_number"] == s["account_number"] or a["email"] == s["email"]:
                        acct_label = a["label"]
                        break
            except Exception:
                pass

            # Get leg names from TAB my-bets (has descriptive names like "NBA Den-GSW 10+Pts Player")
            legs_data = [{"propositionId": p.proposition_id, "odds": p.odds, "name": ""}
                         for p in req.propositions]
            tsn = result.get("ticket_number")
            if tsn and s.get("legacy_token"):
                try:
                    import time as _time
                    _time.sleep(1)  # Give TAB a moment to register the bet
                    tab_history = get_my_bets(s["legacy_token"], s["account_number"], s["proxy_url"], count=5)
                    for tb in tab_history.get("bets", []):
                        if tb.get("tsn") == tsn:
                            tab_legs = tb.get("legs", [])
                            if tab_legs:
                                legs_data = [{"name": leg if isinstance(leg, str) else str(leg)} for leg in tab_legs]
                            break
                except Exception as e:
                    logger.warning(f"Failed to fetch leg names from TAB: {e}")

            await save_bet(_user["username"], {
                "account_number": s["account_number"],
                "account_label": acct_label,
                "tsn": tsn,
                "bet_type": "SGM",
                "legs": legs_data,
                "combined_odds": req.combined_odds,
                "stake": req.stake,
                "status": "Pending",
                "raw_response": result.get("details"),
            })
        return PlaceBetResponse(**result)
    except Exception as e:
        raise HTTPException(500, f"Bet placement failed: {str(e)}")


# ─── Place Multi Bet ─────────────────────────────────────────────────────────

@app.post("/api/place-multi", response_model=PlaceBetResponse)
async def api_place_multi(req: PlaceMultiRequest, _user: dict = Depends(_verify_app_token)):
    """Place a cross-game multi bet."""
    s = _get_session(req.session_id)
    if not s.get("legacy_token"):
        raise HTTPException(400, "Legacy token not available. Re-login to enable betting.")
    try:
        result = place_multi_bet(
            s["legacy_token"], s["account_number"], req.legs, req.stake, s["proxy_url"],
        )
        if result.get("success"):
            acct_label = s.get("email", "")
            try:
                accounts = await get_accounts(_user["username"])
                for a in accounts:
                    if a["account_number"] == s["account_number"] or a["email"] == s["email"]:
                        acct_label = a["label"]
                        break
            except Exception:
                pass

            tsn = result.get("ticket_number")
            # Get combined odds from TAB response
            details = result.get("details", {})
            combined_odds = "0"
            try:
                combined_odds = details.get("bets", [{}])[0].get("combinedPrice", "0")
            except (IndexError, AttributeError):
                pass

            # Get leg names from TAB my-bets
            legs_data = [{"propositionId": l.get("propositionId"), "odds": l.get("odds")} for l in req.legs]
            if tsn and s.get("legacy_token"):
                try:
                    import time as _time
                    _time.sleep(1)  # Give TAB a moment to register the bet
                    tab_history = get_my_bets(s["legacy_token"], s["account_number"], s["proxy_url"], count=5)
                    for tb in tab_history.get("bets", []):
                        if tb.get("tsn") == tsn:
                            tab_legs = tb.get("legs", [])
                            if tab_legs:
                                legs_data = [{"name": leg if isinstance(leg, str) else str(leg)} for leg in tab_legs]
                            break
                except Exception:
                    pass

            await save_bet(_user["username"], {
                "account_number": s["account_number"],
                "account_label": acct_label,
                "tsn": tsn,
                "bet_type": "Multi",
                "legs": legs_data,
                "combined_odds": combined_odds,
                "stake": req.stake,
                "status": "Pending",
                "raw_response": details,
            })
        return PlaceBetResponse(**result)
    except Exception as e:
        raise HTTPException(500, f"Multi bet placement failed: {str(e)}")


# ─── Bet History ─────────────────────────────────────────────────────────────

@app.get("/api/bet-history")
async def api_bet_history(status: str = None, account_number: str = None,
                          limit: int = 50, _user: dict = Depends(_verify_app_token)):
    """Get OUR bet history from PostgreSQL — only bets placed through this app."""
    try:
        bets = await get_bets(
            _user["username"],
            status=status if status and status != "ALL" else None,
            account_number=account_number,
            limit=limit,
        )
        return {"bets": bets}
    except Exception as e:
        raise HTTPException(500, f"Bet history failed: {str(e)}")


@app.post("/api/bets/check-results")
async def api_check_results(session_id: str = None, _user: dict = Depends(_verify_app_token)):
    """Check results for all Pending bets across all logged-in accounts."""
    pending = await get_pending_bets(_user["username"])
    if not pending:
        return {"updated": 0, "bets": [], "accounts_checked": []}

    # Group pending bets by account number
    pending_by_account = {}
    for bet in pending:
        acct = bet.get("account_number", "")
        if acct not in pending_by_account:
            pending_by_account[acct] = []
        pending_by_account[acct].append(bet)

    # Find sessions for each account that has pending bets
    updated_count = 0
    updated_bets = []
    accounts_checked = []
    accounts_failed = []

    for acct_num, acct_bets in pending_by_account.items():
        # Find a session for this account
        session_found = None
        for sid, s in sessions.items():
            if s.get("account_number") == acct_num and s.get("legacy_token"):
                session_found = s
                break

        if not session_found:
            accounts_failed.append({"account": acct_num, "error": "No active session — login required"})
            continue

        # Fetch TAB bets for this account
        try:
            tab_bets = get_my_bets(
                session_found["legacy_token"], acct_num,
                session_found["proxy_url"], count=50, status="ALL"
            )
            accounts_checked.append(acct_num)
        except Exception as e:
            accounts_failed.append({"account": acct_num, "error": str(e)})
            continue

        # Build TSN lookup
        tab_by_tsn = {}
        for tb in tab_bets.get("bets", []):
            if tb.get("tsn"):
                tab_by_tsn[tb["tsn"]] = tb

        # Match and update
        for bet in acct_bets:
            tsn = bet.get("tsn")
            if not tsn or tsn not in tab_by_tsn:
                continue

            tab_bet = tab_by_tsn[tsn]
            tab_status = tab_bet.get("status", "")

            if tab_status in ("Won", "Lost"):
                payout = tab_bet.get("payout", "$0.00") if tab_status == "Won" else "$0.00"
                await update_bet_status(bet["id"], tab_status, payout)
                updated_count += 1
                updated_bets.append({
                    "id": bet["id"], "tsn": tsn, "status": tab_status,
                    "payout": payout, "account": acct_num,
                })

    return {
        "updated": updated_count,
        "bets": updated_bets,
        "accounts_checked": accounts_checked,
        "accounts_failed": accounts_failed,
        "pending_remaining": len(pending) - updated_count,
    }


# ─── Session Info ────────────────────────────────────────────────────────────

@app.get("/api/active-sessions")
async def api_active_sessions(_user: dict = Depends(_verify_app_token)):
    """Get all active TAB sessions for the current user's accounts."""
    user_accounts = await get_accounts(_user["username"])
    active = []
    for s_id, s in sessions.items():
        # Check token not expired
        claims = decode_token_claims(s["token"])
        exp = claims.get("exp", 0)
        if exp and time.time() > exp - 300:
            continue
        # Match to user's accounts by email
        matched_account_id = None
        for a in user_accounts:
            if a["email"] == s["email"] or a.get("account_number") == s["account_number"]:
                matched_account_id = a["id"]
                break
        if matched_account_id:
            active.append({
                "account_id": matched_account_id,
                "session_id": s_id,
                "email": s["email"],
                "account_number": s["account_number"],
                "customer_id": s["customer_id"],
            })
    return {"sessions": active}


@app.get("/api/session")
async def api_session(session_id: str, _user: dict = Depends(_verify_app_token)):
    """Get current session info (no sensitive data)."""
    s = _get_session(session_id)
    claims = decode_token_claims(s["token"])
    exp = claims.get("exp", 0)
    return {
        "email": s["email"],
        "account_number": s["account_number"],
        "customer_id": s["customer_id"],
        "token_expires_at": exp,
        "token_valid": time.time() < exp if exp else False,
    }


@app.delete("/api/session")
async def api_logout(session_id: str, _user: dict = Depends(_verify_app_token)):
    """Logout / destroy session."""
    if session_id in sessions:
        del sessions[session_id]
    await delete_tab_session(session_id)
    return {"ok": True}


# ─── Quick Bet (CSV Paste) ───────────────────────────────────────────────────

from pydantic import BaseModel as _BM

class QuickBetRow(_BM):
    bet_type: str  # SGM or Multi
    game_id: str
    bet: str  # "Player1 15+ Disposals/Player2 15+ Disposals"
    odds: float  # Expected combined odds
    min_odds: float  # Minimum acceptable combined odds
    ev_pct: float = 0
    units: float = 1
    leg_odds: list[float] = []  # Individual leg odds from CSV

class QuickBetRequest(_BM):
    session_id: str
    bets: list[QuickBetRow]
    unit_size: float = 10.0  # Dollars per unit
    auto_place: bool = False  # If True, place bets that meet minimum

@app.post("/api/quick-resolve")
async def api_quick_resolve(req: QuickBetRequest, _user: dict = Depends(_verify_app_token)):
    """Resolve CSV bet rows to TAB propositions and price check."""
    s = _get_session(req.session_id)
    results = []

    for row in req.bets:
        stake = f"{row.units * req.unit_size:.2f}"
        resolved = resolve_and_price(
            token=s["token"],
            proxy_url=s["proxy_url"],
            game_id=row.game_id,
            bet_description=row.bet,
            stake=stake,
            min_odds=row.min_odds,
        )
        resolved["csv_odds"] = row.odds
        resolved["min_odds"] = row.min_odds
        resolved["ev_pct"] = row.ev_pct
        resolved["units"] = row.units
        resolved["stake"] = stake
        results.append(resolved)

    return {"results": results}


@app.post("/api/quick-place")
async def api_quick_place(req: QuickBetRequest, _user: dict = Depends(_verify_app_token)):
    """Resolve, validate, and place all qualifying bets with human-like delays."""
    import random as _random

    s = _get_session(req.session_id)
    results = []

    for i, row in enumerate(req.bets):
        # Human-like delays between bets
        if i > 0:
            # Every 15-25 bets, take a longer break (60-120 seconds) like a real person
            if not hasattr(api_quick_place, '_next_break'):
                api_quick_place._next_break = _random.randint(15, 25)
            if i % api_quick_place._next_break == 0:
                long_delay = _random.uniform(60, 120)
                logger.info(f"Quick-place: long break {long_delay:.0f}s after {i} bets (next break in {api_quick_place._next_break} bets)")
                await asyncio.sleep(long_delay)
                api_quick_place._next_break = _random.randint(15, 25)
            else:
                # Normal delay 2-6 seconds between bets
                delay = _random.uniform(2, 6)
                logger.info(f"Quick-place: waiting {delay:.1f}s before bet {i+1}/{len(req.bets)}")
                await asyncio.sleep(delay)
        stake = f"{row.units * req.unit_size:.2f}"

        # Resolve
        resolved = resolve_and_price(
            token=s["token"],
            proxy_url=s["proxy_url"],
            game_id=row.game_id,
            bet_description=row.bet,
            stake=stake,
            min_odds=row.min_odds,
        )
        resolved["csv_odds"] = row.odds
        resolved["min_odds"] = row.min_odds
        resolved["ev_pct"] = row.ev_pct
        resolved["units"] = row.units
        resolved["stake"] = stake

        # Place if resolved and meets minimum
        if resolved["resolved"] and resolved["meets_minimum"] and resolved["combined_odds"]:
            try:
                place_result = place_sgm_bet(
                    legacy_token=s["legacy_token"],
                    account_number=s["account_number"],
                    propositions=resolved["propositions"],
                    combined_odds=resolved["combined_odds"],
                    stake=stake,
                    proxy_url=s["proxy_url"],
                )
                resolved["placed"] = place_result.get("success", False)
                resolved["ticket_number"] = place_result.get("ticket_number")
                resolved["place_error"] = place_result.get("error")
                resolved["tab_response"] = place_result.get("details")
                # Save to our DB
                if place_result.get("success"):
                    acct_label = s.get("email", "")
                    try:
                        accounts = await get_accounts(_user["username"])
                        for a in accounts:
                            if a["account_number"] == s["account_number"]:
                                acct_label = a["label"]
                                break
                    except Exception:
                        pass
                    await save_bet(_user["username"], {
                        "account_number": s["account_number"],
                        "account_label": acct_label,
                        "tsn": place_result.get("ticket_number"),
                        "bet_type": "SGM",
                        "legs": resolved.get("propositions", []),
                        "combined_odds": resolved.get("combined_odds", "0"),
                        "stake": stake,
                        "status": "Pending",
                        "raw_response": place_result.get("details"),
                    })
            except Exception as e:
                resolved["placed"] = False
                resolved["place_error"] = str(e)
        else:
            resolved["placed"] = False
            resolved["place_error"] = resolved.get("error", "Did not meet minimum odds")

        resolved["account_number"] = s["account_number"]

        results.append(resolved)

    return {"results": results}


# ─── Account Persistence ─────────────────────────────────────────────────────

from pydantic import BaseModel as _BM2

class AccountData(_BM2):
    id: Optional[str] = None
    label: str
    email: str
    password: str
    proxy_url: str
    account_number: Optional[str] = None
    customer_id: Optional[str] = None

class SyncRequest(_BM2):
    accounts: list[AccountData]

@app.get("/api/accounts")
async def api_get_accounts(user: dict = Depends(_verify_app_token)):
    """Get saved accounts for current user."""
    accounts = await get_accounts(user["username"])
    return {"accounts": [{
        "id": a["id"],
        "label": a["label"],
        "email": a["email"],
        "password": a["password"],
        "proxyUrl": a["proxy_url"],
        "accountNumber": a.get("account_number"),
        "customerId": a.get("customer_id"),
    } for a in accounts]}

@app.post("/api/accounts")
async def api_save_account(account: AccountData, user: dict = Depends(_verify_app_token)):
    """Save or update an account."""
    import uuid
    data = account.dict()
    if not data.get("id"):
        data["id"] = str(uuid.uuid4())
    await upsert_account(user["username"], data)
    return {"ok": True, "id": data["id"]}

@app.delete("/api/accounts/{account_id}")
async def api_delete_account(account_id: str, user: dict = Depends(_verify_app_token)):
    """Delete an account."""
    await delete_account(user["username"], account_id)
    return {"ok": True}

@app.post("/api/accounts/sync")
async def api_sync_accounts(req: SyncRequest, user: dict = Depends(_verify_app_token)):
    """Sync all accounts from frontend to database."""
    accounts_data = []
    for a in req.accounts:
        d = a.dict()
        accounts_data.append(d)
    await sync_accounts(user["username"], accounts_data)
    return {"ok": True}




# ─── Paste Token (browser token for balance) ────────────────────────────────

class PasteTokenRequest(_BM):
    token: str
    account_number: str = ""
    proxy_url: str = ""

@app.post("/api/paste-token")
async def api_paste_token(req: PasteTokenRequest, user: dict = Depends(_verify_app_token)):
    """Accept a browser-captured token and check balance with it."""
    from login import decode_token_claims

    token = req.token.strip()
    claims = decode_token_claims(token)
    customer_id = str(claims.get("https://tab.com.au/customerId", ""))

    # Resolve real account number and balance
    proxy = req.proxy_url if req.proxy_url else None
    balance = None
    account_number = req.account_number or customer_id
    try:
        acct_info = get_account_info(token, customer_id, proxy)
        account_number = acct_info["account_number"]
        balance = acct_info["account_balance"]
    except Exception as e:
        logger.warning(f"Account info with pasted token failed: {e}")
        # Try without proxy
        if proxy:
            try:
                acct_info = get_account_info(token, customer_id, None)
                account_number = acct_info["account_number"]
                balance = acct_info["account_balance"]
            except Exception as e2:
                logger.warning(f"Account info without proxy also failed: {e2}")

    # Create a session with this token
    session_id = str(uuid.uuid4())
    session_data = {
        "token": token,
        "legacy_token": "",  # Paste-token doesn't have TAB password for legacy auth
        "account_number": account_number,
        "customer_id": customer_id,
        "email": claims.get("https://tab.com.au/hashedEmail", "browser-token"),
        "password": "",
        "proxy_url": req.proxy_url,
        "profile_dir": "",
        "logged_in_at": time.time(),
    }
    sessions[session_id] = session_data
    await save_tab_session(session_id, session_data)

    if balance:
        return {
            "balance": balance,
            "session_id": session_id,
            "account_number": account_number,
            "customer_id": customer_id,
        }
    else:
        return {
            "error": "Balance check failed but token saved",
            "session_id": session_id,
            "account_number": account_number,
            "customer_id": customer_id,
        }


# ─── JSON Bet Upload ─────────────────────────────────────────────────────────

class JsonBetLeg(_BM):
    model_config = {"extra": "allow"}
    market: str = ""           # e.g. "player_prop" or "disposals"
    player: str = ""           # e.g. "Andrew Brayshaw"
    stat: str = ""             # e.g. "disposals" (alternative to market)
    line: float = 0            # e.g. 20
    selection: str = ""        # "over" or "under"
    type: str = ""             # alternative to selection (Diji format)

class JsonBet(_BM):
    model_config = {"extra": "allow"}  # Allow _meta and other extra fields
    session_id: str = ""
    category: str = "sports"
    is_same_event_multi: bool = True
    stake: float = 10.0
    sport: str = "afl"
    event: str = ""            # e.g. "Fremantle v Melbourne"
    legs: list[JsonBetLeg]

class JsonBetBatch(_BM):
    session_id: str
    bets: list[JsonBet]


# Sport name mapping: JSON format -> TAB format
SPORT_MAP = {
    "afl": ("AFL Football", "AFL"),
    "nrl": ("Rugby League", "NRL"),
    "nba": ("Basketball", "NBA"),
    "soccer": ("Soccer", "A-League Men"),
    "basketball": ("Basketball", "NBA"),
}


def _resolve_json_bet(token: str, legacy_token: str, proxy_url: str, bet: JsonBet) -> dict:
    """Resolve a JSON bet to TAB propositions and return placement-ready data."""
    from betting import get_matches, get_sgm_propositions, get_match_markets, price_check

    sport_key = bet.sport.lower()
    tab_sport, tab_comp = SPORT_MAP.get(sport_key, ("AFL Football", "AFL"))

    result = {
        "event": bet.event,
        "sport": bet.sport,
        "stake": bet.stake,
        "legs_input": [l.dict() for l in bet.legs],
        "resolved": False,
        "match_name": None,
        "propositions": [],
        "combined_odds": None,
        "error": None,
    }

    try:
        # Find match
        matches = get_matches(token, proxy_url, tab_sport, tab_comp)
        matched_match = None
        event_lower = bet.event.lower()
        for m in matches:
            if event_lower in m["match_name"].lower() or m["match_name"].lower() in event_lower:
                matched_match = m
                break
        # Fuzzy: try matching individual team names
        if not matched_match:
            event_parts = [p.strip().lower() for p in bet.event.replace(" v ", "/").replace(" vs ", "/").split("/")]
            for m in matches:
                name_lower = m["match_name"].lower()
                if all(part in name_lower for part in event_parts):
                    matched_match = m
                    break
                # Try partial
                if any(part in name_lower for part in event_parts):
                    matched_match = m

        if not matched_match:
            result["error"] = f"Could not find match: {bet.event}"
            return result

        result["match_name"] = matched_match["match_name"]

        # Get SGM markets (falls back to regular markets)
        sgm_id = matched_match.get("match_name_url", matched_match["match_id"])
        sgm_data = get_sgm_propositions(token, sgm_id, proxy_url, tab_sport, tab_comp)

        # If SGM empty, try regular markets
        has_sgm = False
        if sgm_data.get("data"):
            for section in sgm_data["data"]:
                for market in section.get("data", []):
                    for group in market.get("data", []):
                        if group.get("propositions"):
                            has_sgm = True
                            break

        if not has_sgm:
            match_data = get_match_markets(token, matched_match["match_id"], proxy_url, tab_sport, tab_comp)
            # Convert to flat prop list
            all_props = []
            for mkt in match_data.get("markets", []):
                bet_option = mkt.get("betOption", "")
                for p in mkt.get("propositions", []):
                    all_props.append({
                        "id": p.get("id"),
                        "numberId": p.get("id"),
                        "name": p.get("name", ""),
                        "returnWin": p.get("returnWin", 0),
                        "betOption": bet_option,
                        "isOpen": p.get("bettingStatus") == "Open",
                    })
        else:
            # Flatten SGM data
            all_props = []
            for section in sgm_data.get("data", []):
                for market in section.get("data", []):
                    bet_option = market.get("betOption", "")
                    for group in market.get("data", []):
                        for p in group.get("propositions", []):
                            all_props.append({
                                "id": str(p.get("id", p.get("numberId", ""))),
                                "numberId": p.get("numberId", p.get("id")),
                                "name": p.get("name", ""),
                                "returnWin": p.get("returnWin", 0),
                                "betOption": bet_option,
                                "isOpen": p.get("isOpen", True),
                            })

        if not all_props:
            result["error"] = f"No propositions found for {result['match_name']}"
            return result

        # Match each leg
        resolved_legs = []
        for leg in bet.legs:
            player_lower = leg.player.lower()
            # Handle both field names: 'stat' or 'market' for the stat type
            stat_name = leg.stat or leg.market or ""
            stat_lower = stat_name.lower()
            line_str = f"{int(leg.line)}+"

            # Build target market name: "20+ Disposals"
            target_market = f"{int(leg.line)}+ {stat_name.title()}"

            best_prop = None
            best_score = 0

            for p in all_props:
                if not p.get("isOpen", True):
                    continue

                prop_name = p.get("name", "").lower()
                prop_market = p.get("betOption", "").lower()

                score = 0

                # Player name match
                if player_lower in prop_name:
                    score += 10
                else:
                    # Last name match
                    last_name = player_lower.split()[-1] if player_lower else ""
                    if last_name and len(last_name) > 2 and last_name in prop_name:
                        score += 7

                if score == 0:
                    continue

                # Market match: "20+ disposals" in betOption
                if target_market.lower() in prop_market:
                    score += 8
                elif line_str in prop_market and stat_lower in prop_market:
                    score += 7
                elif stat_lower in prop_market:
                    score += 4

                if score > best_score:
                    best_score = score
                    best_prop = p

            if best_prop:
                resolved_legs.append({
                    "propositionId": int(best_prop["numberId"]),
                    "odds": str(best_prop["returnWin"]),
                    "name": f"{best_prop['name']} — {best_prop['betOption']}",
                    "matched_player": leg.player,
                    "matched_market": best_prop["betOption"],
                })
            else:
                result["error"] = f"Could not find: {leg.player} {int(leg.line)}+ {stat_name}"
                result["legs_resolved"] = resolved_legs
                return result

        result["propositions"] = resolved_legs
        result["resolved"] = True

        # Price check
        props_for_price = [{"propositionId": l["propositionId"], "odds": l["odds"]} for l in resolved_legs]
        bet_type = "SAME_GAME_MULTI" if bet.is_same_event_multi else "WIN"
        price_result = price_check(token, props_for_price, str(bet.stake), bet_type, proxy_url)
        result["combined_odds"] = price_result.get("combined_odds")

        return result

    except Exception as e:
        result["error"] = str(e)
        return result


@app.post("/api/place-json")
async def api_place_json(req: JsonBet, _user: dict = Depends(_verify_app_token)):
    """Resolve and place a single bet from JSON format."""
    import random as _random

    s = _get_session(req.session_id)
    if not s.get("legacy_token"):
        raise HTTPException(400, "Legacy token not available. Re-login to enable betting.")

    resolved = _resolve_json_bet(s["token"], s["legacy_token"], s["proxy_url"], req)

    if not resolved["resolved"]:
        return {"success": False, "resolved": resolved}

    # Place the bet
    try:
        if req.is_same_event_multi:
            place_result = place_sgm_bet(
                legacy_token=s["legacy_token"],
                account_number=s["account_number"],
                propositions=resolved["propositions"],
                combined_odds=resolved["combined_odds"],
                stake=str(req.stake),
                proxy_url=s["proxy_url"],
            )
        else:
            place_result = place_multi_bet(
                legacy_token=s["legacy_token"],
                account_number=s["account_number"],
                legs=[{"propositionId": l["propositionId"], "odds": l["odds"]} for l in resolved["propositions"]],
                stake=str(req.stake),
                proxy_url=s["proxy_url"],
            )

        # Save to DB
        if place_result.get("success"):
            acct_label = ""
            try:
                accounts = await get_accounts(_user["username"])
                for a in accounts:
                    if a["account_number"] == s["account_number"] or a["email"] == s["email"]:
                        acct_label = a["label"]
                        break
            except Exception:
                pass

            tsn = place_result.get("ticket_number")
            legs_data = [{"name": l.get("name", "")} for l in resolved["propositions"]]
            if tsn and s.get("legacy_token"):
                try:
                    import time as _time
                    _time.sleep(1)
                    tab_history = get_my_bets(s["legacy_token"], s["account_number"], s["proxy_url"], count=5)
                    for tb in tab_history.get("bets", []):
                        if tb.get("tsn") == tsn:
                            tab_legs = tb.get("legs", [])
                            if tab_legs:
                                legs_data = [{"name": leg if isinstance(leg, str) else str(leg)} for leg in tab_legs]
                            break
                except Exception:
                    pass

            details = place_result.get("details", {})
            combined = resolved.get("combined_odds", "0")
            try:
                combined = details.get("bets", [{}])[0].get("combinedPrice", combined)
            except (IndexError, AttributeError):
                pass

            await save_bet(_user["username"], {
                "account_number": s["account_number"],
                "account_label": acct_label,
                "tsn": tsn,
                "bet_type": "SGM" if req.is_same_event_multi else "Multi",
                "legs": legs_data,
                "combined_odds": str(combined),
                "stake": str(req.stake),
                "status": "Pending",
                "raw_response": details,
            })

        return {
            "success": place_result.get("success", False),
            "ticket_number": place_result.get("ticket_number"),
            "combined_odds": resolved.get("combined_odds"),
            "stake": req.stake,
            "account_balance": place_result.get("account_balance"),
            "error": place_result.get("error"),
            "resolved": resolved,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "resolved": resolved}


@app.post("/api/place-json-batch")
async def api_place_json_batch(req: JsonBetBatch, _user: dict = Depends(_verify_app_token)):
    """Resolve and place multiple bets from JSON with human-like delays."""
    import random as _random

    s = _get_session(req.session_id)
    if not s.get("legacy_token"):
        raise HTTPException(400, "Legacy token not available. Re-login to enable betting.")

    results = []
    for i, bet in enumerate(req.bets):
        # Human-like delays
        if i > 0:
            if i % _random.randint(15, 25) == 0:
                delay = _random.uniform(60, 120)
                logger.info(f"JSON batch: long break {delay:.0f}s after {i} bets")
                await asyncio.sleep(delay)
            else:
                delay = _random.uniform(2, 6)
                logger.info(f"JSON batch: {delay:.1f}s delay before bet {i+1}/{len(req.bets)}")
                await asyncio.sleep(delay)

        bet.session_id = req.session_id
        resolved = _resolve_json_bet(s["token"], s["legacy_token"], s["proxy_url"], bet)

        if not resolved["resolved"]:
            results.append({"index": i, "success": False, "resolved": resolved})
            continue

        try:
            if bet.is_same_event_multi:
                place_result = place_sgm_bet(
                    legacy_token=s["legacy_token"],
                    account_number=s["account_number"],
                    propositions=resolved["propositions"],
                    combined_odds=resolved["combined_odds"],
                    stake=str(bet.stake),
                    proxy_url=s["proxy_url"],
                )
            else:
                place_result = place_multi_bet(
                    legacy_token=s["legacy_token"],
                    account_number=s["account_number"],
                    legs=[{"propositionId": l["propositionId"], "odds": l["odds"]} for l in resolved["propositions"]],
                    stake=str(bet.stake),
                    proxy_url=s["proxy_url"],
                )

            # Save to DB
            if place_result.get("success"):
                acct_label = ""
                try:
                    accounts = await get_accounts(_user["username"])
                    for a in accounts:
                        if a["account_number"] == s["account_number"] or a["email"] == s["email"]:
                            acct_label = a["label"]
                            break
                except Exception:
                    pass

                tsn = place_result.get("ticket_number")
                legs_data = [{"name": l.get("name", "")} for l in resolved["propositions"]]
                if tsn and s.get("legacy_token"):
                    try:
                        import time as _time
                        _time.sleep(1)
                        tab_history = get_my_bets(s["legacy_token"], s["account_number"], s["proxy_url"], count=5)
                        for tb in tab_history.get("bets", []):
                            if tb.get("tsn") == tsn:
                                tab_legs = tb.get("legs", [])
                                if tab_legs:
                                    legs_data = [{"name": leg if isinstance(leg, str) else str(leg)} for leg in tab_legs]
                                break
                    except Exception:
                        pass

                details = place_result.get("details", {})
                combined = resolved.get("combined_odds", "0")
                try:
                    combined = details.get("bets", [{}])[0].get("combinedPrice", combined)
                except (IndexError, AttributeError):
                    pass

                await save_bet(_user["username"], {
                    "account_number": s["account_number"],
                    "account_label": acct_label,
                    "tsn": tsn,
                    "bet_type": "SGM" if bet.is_same_event_multi else "Multi",
                    "legs": legs_data,
                    "combined_odds": str(combined),
                    "stake": str(bet.stake),
                    "status": "Pending",
                    "raw_response": details,
                })

            results.append({
                "index": i,
                "success": place_result.get("success", False),
                "ticket_number": place_result.get("ticket_number"),
                "combined_odds": resolved.get("combined_odds"),
                "stake": bet.stake,
                "event": bet.event,
                "error": place_result.get("error"),
            })
        except Exception as e:
            results.append({"index": i, "success": False, "error": str(e), "event": bet.event})

    placed = sum(1 for r in results if r.get("success"))
    failed = len(results) - placed
    return {"total": len(results), "placed": placed, "failed": failed, "results": results}


# ─── Health ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "active_sessions": len(sessions)}

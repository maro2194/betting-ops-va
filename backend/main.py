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
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from login import browser_login, complete_mfa_login, decode_token_claims, MFARequiredException
from betting import (
    get_balance, get_account_info, legacy_authenticate, get_matches,
    get_match_markets, get_sgm_propositions, price_check, place_sgm_bet,
    place_multi_bet, get_my_bets,
)
from resolver import resolve_and_price, resolve_csb_bet, _get_all_sport_props, _sport_props_cache
import database
from database import (
    init_db, close_db, get_accounts, upsert_account, delete_account, sync_accounts,
    save_app_session, get_app_session, delete_app_session, load_all_app_sessions,
    save_tab_session, load_all_tab_sessions, delete_tab_session,
    save_tab_groups, load_tab_groups,
    save_bet, get_bets, update_bet_status, get_pending_bets, get_all_tsns,
)

from models import (
    LoginRequest, LoginResponse, BalanceResponse,
    PriceCheckRequest, PriceCheckResponse,
    PlaceBetRequest, PlaceMultiRequest, PlaceBetResponse,
    BetHistoryResponse, SGMMarketResponse,
)
from bet365_routes import _save_job, _get_job

# Load .env — try same directory first, then parent
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
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
    from multi_database import init_multi_db
    await init_multi_db()
    # Init bet365 tables
    from bet365_routes import init_bet365_tables, save_pick_to_db, _save_job, _get_job
    await init_bet365_tables()
    # Prune old background jobs (>7 days)
    try:
        async with database.pool.acquire() as conn:
            pruned = await conn.execute("DELETE FROM bg_jobs WHERE created_at < NOW() - INTERVAL '7 days'")
            if "DELETE 0" not in pruned:
                logger.info("Pruned old bg_jobs: %s", pruned)
    except Exception:
        pass
    # Init BetOps outbox table + start the background retry loop. save_bet fires
    # emit_to_betops on every confirmed placement; anything that fails to reach
    # www.betops.sh falls into betops_outbox and this loop flushes it.
    from betops_sync import init_outbox as _betops_init_outbox, flush_outbox_loop as _betops_flush_loop
    await _betops_init_outbox(database.pool)
    asyncio.create_task(_betops_flush_loop(database.pool))
    # Wire DB save into pipeline
    from pick_pipeline import pick_pipeline
    pick_pipeline._save_pick_fn = save_pick_to_db
    # Restore sessions from DB, purge expired ones
    global app_tokens, sessions
    app_tokens.update(await load_all_app_sessions())
    all_sessions = await load_all_tab_sessions()
    purged = 0
    for sid, sdata in list(all_sessions.items()):
        claims = decode_token_claims(sdata.get("token", ""))
        exp = claims.get("exp", 0)
        if exp and time.time() > exp:
            await delete_tab_session(sid)
            purged += 1
        else:
            sessions[sid] = sdata
    logger.info(f"Restored {len(sessions)} TAB sessions ({purged} expired purged) and {len(app_tokens)} app sessions from DB")

    # Auto-backfill: sync account_number from sessions into tab_accounts where missing
    backfilled = 0
    for sid, sdata in sessions.items():
        acct_num = sdata.get("account_number")
        cust_id = sdata.get("customer_id")
        email = sdata.get("email")
        if acct_num and email:
            try:
                async with database.pool.acquire() as conn:
                    result = await conn.execute(
                        """UPDATE tab_accounts SET account_number = $1, customer_id = COALESCE($2, customer_id)
                           WHERE email = $3 AND (account_number IS NULL OR account_number = '' OR account_number != $1)""",
                        acct_num, cust_id, email,
                    )
                    if "UPDATE 1" in result:
                        backfilled += 1
            except Exception:
                pass
    if backfilled:
        logger.info(f"Backfilled account_number for {backfilled} account(s) from restored sessions")


@app.on_event("shutdown")
async def shutdown():
    from bet365_service import bet365_service
    from telegram_monitor import telegram_monitor
    await telegram_monitor.stop()
    await bet365_service.stop()
    await close_db()

# ─── App Auth (user accounts for accessing this app) ────────────────────────

APP_USERS = {
    "maro": {"password_hash": "aeb2f863f78934afccf548e82d786e0ed39fb52c2dfb23663d8333dd6b608ca5", "name": "Maro"},
    "maro_auto": {"password_hash": "aeb2f863f78934afccf548e82d786e0ed39fb52c2dfb23663d8333dd6b608ca5", "name": "Maro Auto"},
    "diji": {"password_hash": "0489c36a7155b8b671acbab078697dd365e1da635343fa63fee1415b7af516e8", "name": "Diji"},
    "shadow": {"password_hash": "224e690ddf7786edfd76b8cc372fffad88be3c5694268e1e439fa9740080bd18", "name": "Shadow"},
    "smd": {"password_hash": "758dcca888a1f019019cde2f3f127107590de40e344a1b2dd0dc566baad9d009", "name": "SMD"},
    "shadowxmaro": {"password_hash": "224e690ddf7786edfd76b8cc372fffad88be3c5694268e1e439fa9740080bd18", "name": "ShadowXMaro"},
    "csb": {"password_hash": "b75bdd4bf010afa5c0448eec8c6ed8fc899413c9391d46c1d6b1846839b4e03a", "name": "CSB"},
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
mfa_pending: dict[str, dict] = {}  # email -> {mfa_token, oob_code, login_instance, ...}

PROFILE_BASE_DIR = os.path.join(os.path.dirname(__file__), "profiles")


async def _get_session_async(session_id: str) -> dict:
    """Get session from memory or DB fallback (multi-worker safe)."""
    if session_id not in sessions:
        from database import load_all_tab_sessions
        db_sessions = await load_all_tab_sessions()
        if session_id in db_sessions:
            sessions[session_id] = db_sessions[session_id]
            claims = decode_token_claims(db_sessions[session_id].get("token", ""))
            sessions[session_id]["token_exp"] = claims.get("exp", 0)
        else:
            raise HTTPException(status_code=404, detail="Invalid session. Please login again.")
    s = sessions[session_id]
    exp = s.get("token_exp", 0)
    if exp and time.time() > exp - 300:
        raise HTTPException(status_code=404, detail="Token expired. Please login again.")
    return s


def _get_session(session_id: str) -> dict:
    """Get session or raise 401 (sync version, no DB fallback)."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Invalid session. Please login again.")
    s = sessions[session_id]
    exp = s.get("token_exp", 0)
    if exp and time.time() > exp - 300:
        raise HTTPException(status_code=404, detail="Token expired. Please login again.")
    return s


_PROXY_ERROR_MARKERS = (
    "tunnel failed",
    "curl: (56)",
    "curl: (7)",
    "could not resolve proxy",
    "proxy connect",
    "connection reset by peer",
    "remote end closed connection",
)


def _is_proxy_error(msg: object) -> bool:
    """Detect proxy-level transport failures (dead Oxylabs sticky IP, CONNECT
    tunnel 502, etc.) — distinct from TAB API errors. These are the cases
    where rotating to a fresh sessid is the correct recovery."""
    if not msg:
        return False
    m = str(msg).lower()
    return any(p in m for p in _PROXY_ERROR_MARKERS)


def _rotate_oxylabs_sessid(proxy_url: str) -> str | None:
    """Replace the Oxylabs sessid segment with a fresh 12-char hex value.
    Returns None if the URL doesn't match Oxylabs's pattern (so we don't
    rotate non-Oxylabs proxies)."""
    if not proxy_url or "sessid-" not in proxy_url or "oxylabs" not in proxy_url:
        return None
    m = re.search(r'(sessid-)([^-]+)(-)', proxy_url)
    if not m:
        return None
    new_sess = secrets.token_hex(6)
    return proxy_url[:m.start(2)] + new_sess + proxy_url[m.end(2):]


async def _rotate_account_proxy(s: dict, session_id: str | None = None) -> str | None:
    """Generate a fresh Oxylabs sessid, persist to tab_accounts and
    tab_sessions, and mutate the in-memory session dict so the rotated
    proxy is used immediately. Returns the new URL on success, None if the
    rotation couldn't apply (non-Oxylabs proxy etc) so the caller falls
    through to the original error."""
    old_proxy = s.get("proxy_url") or ""
    new_proxy = _rotate_oxylabs_sessid(old_proxy)
    if not new_proxy:
        return None
    s["proxy_url"] = new_proxy
    email = s.get("email")
    if email:
        try:
            async with database.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE tab_accounts SET proxy_url = $1, updated_at = NOW() WHERE email = $2",
                    new_proxy, email,
                )
        except Exception as e:
            logger.warning(f"Auto-rotate: failed to persist proxy to tab_accounts for {email}: {e}")
    if session_id:
        try:
            await save_tab_session(session_id, s)
        except Exception as e:
            logger.warning(f"Auto-rotate: failed to persist proxy to tab_sessions: {e}")
    logger.info(f"Auto-rotated proxy for {email or session_id} after tunnel error")
    return new_proxy


async def _ensure_legacy_token(s: dict, session_id: str | None = None) -> None:
    """Lazy-fetch the TabcorpAuth legacy token if missing.

    Login can complete with an empty legacy_token when both initial
    legacy_authenticate attempts fail (e.g. transient proxy/Akamai blip).
    Without this helper, every betting/history endpoint then bails with
    "Legacy token not available" until the user manually re-logs in.

    Tries to fetch a fresh token using the cached account+password. On
    success, mutates s in place and persists when session_id is given.
    Raises HTTPException only if recovery fails — the user gets one
    auto-recovery shot before being told to re-login.
    """
    if s.get("legacy_token"):
        return
    pwd = s.get("password")
    acct = s.get("account_number")
    if not pwd or not acct:
        raise HTTPException(400, "Legacy token not available and credentials missing. Re-login to enable betting.")
    try:
        legacy_result = await asyncio.to_thread(
            legacy_authenticate, acct, pwd, s.get("proxy_url"),
        )
        token = legacy_result.get("legacy_token", "") if legacy_result else ""
        if not token:
            raise HTTPException(400, "Legacy token not available — auth returned empty token. Re-login to enable betting.")
        s["legacy_token"] = token
        if session_id:
            try:
                await save_tab_session(session_id, s)
            except Exception as persist_err:
                logger.warning(f"Failed to persist refreshed legacy token for {acct}: {persist_err}")
        logger.info(f"Lazy-fetched legacy token for account {acct}")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Lazy legacy_authenticate failed for {acct}: {e}")
        raise HTTPException(400, f"Legacy token not available: {type(e).__name__}: {e}. Re-login to enable betting.")


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
                if exp and time.time() < exp - 600 and s.get("legacy_token"):  # Still valid for >10 min and has legacy token
                    logger.info(f"Reusing existing session for {email}")
                    try:
                        bal = await asyncio.to_thread(get_balance, s["token"], s["customer_id"], s["proxy_url"])
                        return LoginResponse(
                            session_id=sid,
                            account_number=s["account_number"],
                            customer_id=s["customer_id"],
                            balance=bal["account_balance"],
                            email=email,
                            token_exp=exp,
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

        if result.get("mfa_required"):
            mfa_pending[email] = {
                "mfa_token": result["mfa_token"],
                "oob_code": result["oob_code"],
                "login_instance": result["_login_instance"],
                "password": password,
                "proxy_url": proxy_url,
                "account_number": req.account_number,
                "created_at": time.time(),
            }
            logger.info(f"MFA required for {email} — SMS sent, awaiting OTP")
            return JSONResponse(status_code=202, content={
                "mfa_required": True,
                "email": email,
                "message": "SMS code sent. Enter the OTP to complete login.",
            })

        # Resolve real account number and balance via account-list endpoint
        customer_id = result["customer_id"]
        try:
            acct_info = await asyncio.to_thread(get_account_info, result["token"], customer_id, proxy_url)
            real_account_number = acct_info["account_number"]
            balance_str = acct_info["account_balance"]
            logger.info(f"Resolved account: customer_id={customer_id} → account_number={real_account_number}, balance={balance_str}")
        except Exception as e:
            logger.warning(f"Account info lookup failed: {e}")
            real_account_number = req.account_number or customer_id
            balance_str = "N/A"

        # Get legacy TabcorpAuth token for betting + history (retry once on failure)
        account_number = req.account_number or real_account_number
        legacy_token = ""
        for _attempt in range(2):
            try:
                legacy_result = await asyncio.to_thread(legacy_authenticate, account_number, password, proxy_url)
                legacy_token = legacy_result["legacy_token"]
                logger.info(f"Legacy auth successful for account {account_number}")
                break
            except Exception as e:
                if _attempt == 0:
                    logger.warning(f"Legacy auth attempt 1 failed, retrying: {e}")
                    await asyncio.sleep(1)
                else:
                    logger.warning(f"Legacy auth failed after retry (betting/history will not work): {e}")

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

        # Auto-backfill account_number + customer_id on tab_accounts
        try:
            async with database.pool.acquire() as conn:
                await conn.execute(
                    """UPDATE tab_accounts SET account_number = $1, customer_id = $2
                       WHERE email = $3 AND username = $4
                       AND (account_number IS NULL OR account_number = '' OR account_number != $1)""",
                    account_number, customer_id, email, _user["username"],
                )
        except Exception:
            pass

        return LoginResponse(
            session_id=session_id,
            account_number=account_number,
            customer_id=customer_id,
            balance=balance_str,
            email=email,
            token_exp=session_data.get("token_exp"),
        )


# ─── MFA Verify ──────────────────────────────────────────────────────────────

class MFAVerifyRequest(_BaseModel):
    email: str
    otp_code: str

@app.post("/api/mfa-verify", response_model=LoginResponse)
async def api_mfa_verify(req: MFAVerifyRequest, _user: dict = Depends(_verify_app_token)):
    """Complete MFA login with the SMS OTP code."""
    email = req.email.strip().lower()
    pending = mfa_pending.pop(email, None)
    if not pending:
        # Also try without lowercasing
        pending = mfa_pending.pop(req.email.strip(), None)
    if not pending:
        raise HTTPException(400, "No pending MFA for this email. Try logging in again.")

    login_instance = pending["login_instance"]
    try:
        result = await complete_mfa_login(
            login_instance, pending["mfa_token"], pending["oob_code"], req.otp_code
        )
    except Exception as e:
        logger.error(f"MFA verify failed for {email}: {e}")
        raise HTTPException(400, f"MFA verification failed: {e}")

    proxy_url = pending["proxy_url"]
    password = pending["password"]
    customer_id = result["customer_id"]
    try:
        acct_info = await asyncio.to_thread(get_account_info, result["token"], customer_id, proxy_url)
        real_account_number = acct_info["account_number"]
        balance_str = acct_info["account_balance"]
        logger.info(f"MFA login resolved: customer_id={customer_id} → account_number={real_account_number}, balance={balance_str}")
    except Exception as e:
        logger.warning(f"Account info lookup failed after MFA: {e}")
        real_account_number = pending.get("account_number") or customer_id
        balance_str = "N/A"

    account_number = pending.get("account_number") or real_account_number
    legacy_token = ""
    for _attempt in range(2):
        try:
            legacy_result = await asyncio.to_thread(legacy_authenticate, account_number, password, proxy_url)
            legacy_token = legacy_result["legacy_token"]
            logger.info(f"Legacy auth successful for account {account_number} (post-MFA)")
            break
        except Exception as e:
            if _attempt == 0:
                await asyncio.sleep(1)
            else:
                logger.warning(f"Legacy auth failed after MFA: {e}")

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
    claims = decode_token_claims(result["token"])
    session_data["token_exp"] = claims.get("exp")
    await save_tab_session(session_id, session_data)

    return LoginResponse(
        session_id=session_id,
        account_number=account_number,
        customer_id=customer_id,
        balance=balance_str,
        email=email,
        token_exp=session_data.get("token_exp"),
    )


# ─── Balance ─────────────────────────────────────────────────────────────────

@app.get("/api/balance")
async def api_balance(session_id: str, _user: dict = Depends(_verify_app_token)) -> BalanceResponse:
    """Get current account balance.

    Auto-rotates the Oxylabs sessid once on tunnel-level failures (curl 56,
    502 from CONNECT, etc) — those indicate a dead sticky IP, and a fresh
    sessid pulls a new IP from the pool. TAB API errors (401/4xx) are not
    rotated since they aren't the proxy's fault."""
    s = await _get_session_async(session_id)
    last_err = None
    for attempt in (1, 2):
        try:
            bal = await asyncio.to_thread(get_balance, s["token"], s["customer_id"], s["proxy_url"])
            return BalanceResponse(
                account_balance=bal["account_balance"],
                withdrawal_balance=bal["withdrawal_balance"],
            )
        except Exception as e:
            last_err = str(e)
            if attempt == 1 and _is_proxy_error(last_err):
                if await _rotate_account_proxy(s, session_id):
                    logger.info(f"Balance check: rotated proxy after tunnel error ({last_err[:120]}); retrying")
                    continue
            break
    raise HTTPException(500, f"Balance check failed: {last_err}")


# ─── Matches ─────────────────────────────────────────────────────────────────

@app.get("/api/matches")
async def api_matches(session_id: str, sport: str = "AFL Football", competition: str = "AFL", _user: dict = Depends(_verify_app_token)):
    """Get upcoming matches."""
    s = await _get_session_async(session_id)
    try:
        matches = await asyncio.to_thread(get_matches, s["token"], s["proxy_url"], sport, competition)
        return {"matches": matches}
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch matches: {str(e)}")


# ─── SGM Propositions ───────────────────────────────────────────────────────

@app.get("/api/sgm-markets/{match_id}")
async def api_sgm_markets(match_id: str, session_id: str,
                          sport: str = "AFL Football", competition: str = "AFL", _user: dict = Depends(_verify_app_token)):
    """Get SGM propositions for a match. Falls back to regular markets if SGM is empty."""
    s = await _get_session_async(session_id)
    try:
        data = await asyncio.to_thread(get_sgm_propositions, s["token"], match_id, s["proxy_url"], sport, competition)
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
        match_data = await asyncio.to_thread(get_match_markets, s["token"], match_id, s["proxy_url"], sport, competition)
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
    s = await _get_session_async(req.session_id)
    props = [{"propositionId": p.proposition_id, "odds": p.odds} for p in req.propositions]
    try:
        result = await asyncio.to_thread(lambda: price_check(s["token"], props, req.stake, req.bet_type, s["proxy_url"]))
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
    s = await _get_session_async(req.session_id)
    await _ensure_legacy_token(s, req.session_id)
    props = [{"propositionId": p.proposition_id, "odds": p.odds} for p in req.propositions]
    try:
        result = await asyncio.to_thread(
            lambda: place_sgm_bet(
                s["legacy_token"], s["account_number"], props,
                req.combined_odds, req.stake, s["proxy_url"],
            )
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
                    tab_history = await asyncio.to_thread(get_my_bets, s["legacy_token"], s["account_number"], s["proxy_url"], 5)
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
                "source": "sgm_builder",
            })
        return PlaceBetResponse(**result)
    except Exception as e:
        raise HTTPException(500, f"Bet placement failed: {str(e)}")


# ─── Place Multi Bet ─────────────────────────────────────────────────────────

@app.post("/api/place-multi", response_model=PlaceBetResponse)
async def api_place_multi(req: PlaceMultiRequest, _user: dict = Depends(_verify_app_token)):
    """Place a cross-game multi bet."""
    s = await _get_session_async(req.session_id)
    await _ensure_legacy_token(s, req.session_id)
    try:
        result = await asyncio.to_thread(
            lambda: place_multi_bet(
                s["legacy_token"], s["account_number"], req.legs, req.stake, s["proxy_url"],
            )
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
                    tab_history = await asyncio.to_thread(get_my_bets, s["legacy_token"], s["account_number"], s["proxy_url"], 5)
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
                "source": "multi_builder",
            })
        return PlaceBetResponse(**result)
    except Exception as e:
        raise HTTPException(500, f"Multi bet placement failed: {str(e)}")


# ─── Leg Results ─────────────────────────────────────────────────────────────

@app.get("/api/leg-results")
async def api_leg_results(bet_ids: str, _user: dict = Depends(_verify_app_token)):
    """
    Check per-leg results for one or more bets.
    Query param: bet_ids=id1,id2,id3
    Returns: {bet_id: [leg_result, ...], ...}
    """
    from leg_results import check_leg_results

    ids = [i.strip() for i in bet_ids.split(",") if i.strip()]
    if not ids:
        raise HTTPException(400, "bet_ids required")

    # Fetch the bets
    try:
        bets = await get_bets(_user["username"], limit=500)
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")

    bets_by_id = {b["id"]: b for b in bets}

    # Collect all legs from requested bets
    response = {}
    for bet_id in ids:
        bet = bets_by_id.get(bet_id)
        if not bet:
            response[bet_id] = []
            continue

        legs = bet.get("legs", [])
        if not legs:
            response[bet_id] = []
            continue

        try:
            leg_results_list = await check_leg_results(legs)
            response[bet_id] = leg_results_list
        except Exception as e:
            logger.error(f"leg_results error for bet {bet_id}: {e}")
            response[bet_id] = [
                {"name": (l.get("name", "") if isinstance(l, dict) else str(l)),
                 "result": "pending", "note": str(e)}
                for l in legs
            ]

    return response


# ─── Bet History ─────────────────────────────────────────────────────────────

@app.get("/api/bet-history")
async def api_bet_history(status: str = None, account_number: str = None,
                          account_label: str = None, date_from: str = None,
                          date_to: str = None,
                          limit: int = 2000, _user: dict = Depends(_verify_app_token)):
    """Get OUR bet history from PostgreSQL — only bets placed through this app."""
    try:
        bets = await get_bets(
            _user["username"],
            status=status if status and status != "ALL" else None,
            account_number=account_number,
            account_label=account_label,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
        return {"bets": bets}
    except Exception as e:
        raise HTTPException(500, f"Bet history failed: {str(e)}")


@app.get("/api/unified-history")
async def api_unified_history(
    status: str = None, bookie: str = None,
    date_from: str = None, date_to: str = None,
    limit: int = 500, _user: dict = Depends(_verify_app_token),
):
    """Unified bet history across ALL bookies (TAB bets + multi_bets)."""
    import database as _db
    try:
        async with _db.pool.acquire() as conn:
            # Query TAB bets
            tab_rows = await conn.fetch(
                """SELECT id, 'tab' as bookie, account_label as label,
                          bet_type as type, legs, combined_odds as odds,
                          stake, status, payout,
                          placed_at, settled_at, tsn as reference, source as method
                   FROM bets WHERE username = $1
                   ORDER BY placed_at DESC NULLS LAST
                   LIMIT $2""",
                _user["username"], limit,
            )

            # Query multi_bets
            multi_rows = await conn.fetch(
                """SELECT id, brand as bookie, initials as label,
                          method as type, NULL as legs,
                          odds::text as odds, stake, status,
                          payout::double precision as payout,
                          placed_at, settled_at, bet_reference as reference,
                          method
                   FROM multi_bets WHERE username = $1
                   ORDER BY placed_at DESC NULLS LAST
                   LIMIT $2""",
                _user["username"], limit,
            )

        # Merge and normalise
        all_bets = []
        for r in tab_rows:
            d = dict(r)
            d["source"] = "tab"
            if d.get("placed_at"):
                d["placed_at"] = d["placed_at"].isoformat()
            if d.get("settled_at"):
                d["settled_at"] = d["settled_at"].isoformat()
            # Normalise payout to float
            if d.get("payout"):
                try:
                    d["payout"] = float(str(d["payout"]).replace("$", "").replace(",", ""))
                except (ValueError, TypeError):
                    d["payout"] = 0.0
            # Normalise stake
            if d.get("stake") is not None:
                try:
                    d["stake"] = float(d["stake"])
                except (ValueError, TypeError):
                    pass
            all_bets.append(d)

        for r in multi_rows:
            d = dict(r)
            d["source"] = "multi"
            if d.get("placed_at"):
                d["placed_at"] = d["placed_at"].isoformat()
            if d.get("settled_at"):
                d["settled_at"] = d["settled_at"].isoformat()
            all_bets.append(d)

        # Apply filters
        if status and status != "ALL":
            sl = status.lower()
            all_bets = [b for b in all_bets if b.get("status", "").lower() == sl]
        if bookie and bookie != "ALL":
            bl = bookie.lower()
            all_bets = [b for b in all_bets if b.get("bookie", "").lower() == bl]
        if date_from:
            all_bets = [b for b in all_bets if b.get("placed_at", "") >= date_from]
        if date_to:
            all_bets = [b for b in all_bets if b.get("placed_at", "") <= date_to + "T23:59:59"]

        # Sort by placed_at descending
        all_bets.sort(key=lambda b: b.get("placed_at") or "", reverse=True)
        all_bets = all_bets[:limit]

        # Compute stats
        total_staked = sum(float(b.get("stake") or 0) for b in all_bets)
        total_returned = sum(float(b.get("payout") or 0) for b in all_bets)
        won = sum(1 for b in all_bets if b.get("status", "").lower() in ("won", "w"))
        lost = sum(1 for b in all_bets if b.get("status", "").lower() in ("lost", "l"))
        pending = sum(1 for b in all_bets if b.get("status", "").lower() in ("pending", "placed", "open"))

        # Breakdown by bookie
        bookie_stats = {}
        for b in all_bets:
            bk = b.get("bookie", "unknown")
            if bk not in bookie_stats:
                bookie_stats[bk] = {"bets": 0, "staked": 0.0, "returned": 0.0}
            bookie_stats[bk]["bets"] += 1
            bookie_stats[bk]["staked"] += float(b.get("stake") or 0)
            bookie_stats[bk]["returned"] += float(b.get("payout") or 0)

        return {
            "bets": all_bets,
            "stats": {
                "total": len(all_bets),
                "won": won,
                "lost": lost,
                "pending": pending,
                "total_staked": round(total_staked, 2),
                "total_returned": round(total_returned, 2),
                "pl": round(total_returned - total_staked, 2),
                "win_rate": round(won / (won + lost) * 100, 1) if (won + lost) > 0 else 0,
            },
            "by_bookie": bookie_stats,
        }
    except Exception as e:
        logger.error(f"Unified history error: {e}")
        raise HTTPException(500, f"Unified history failed: {str(e)}")


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

    # Pre-load tab_accounts for this user so we can fall back to DB auth
    db_accounts_by_num = {}
    try:
        db_accounts = await get_accounts(_user["username"])
        for a in db_accounts:
            if a.get("account_number"):
                db_accounts_by_num[a["account_number"]] = a
    except Exception:
        pass

    for acct_num, acct_bets in pending_by_account.items():
        # Find a session for this account
        session_found = None
        session_sid = None
        for sid, s in sessions.items():
            if s.get("account_number") == acct_num and s.get("legacy_token"):
                session_found = s
                session_sid = sid
                break

        # Fallback: no in-memory session (multi-worker issue) — auth fresh from DB credentials
        if not session_found and acct_num in db_accounts_by_num:
            db_acct = db_accounts_by_num[acct_num]
            try:
                logger.info(f"check-results: no in-memory session for {acct_num}, doing fresh legacy auth")
                legacy_result = await asyncio.to_thread(
                    lambda acct=db_acct: legacy_authenticate(
                        acct["account_number"],
                        acct["password"],
                        acct.get("proxy_url"),
                    )
                )
                token = legacy_result.get("legacy_token", "")
                if token:
                    session_found = {
                        "legacy_token": token,
                        "account_number": acct_num,
                        "password": db_acct["password"],
                        "proxy_url": db_acct.get("proxy_url"),
                    }
                    logger.info(f"check-results: fresh legacy auth successful for {acct_num}")
                else:
                    accounts_failed.append({"account": acct_num, "error": "Fresh auth returned no token"})
                    continue
            except Exception as e:
                accounts_failed.append({"account": acct_num, "error": f"Fresh auth failed: {e}"})
                continue

        if not session_found:
            accounts_failed.append({"account": acct_num, "error": "No active session and no DB credentials"})
            continue

        # Fetch TAB bets for this account — paginate up to 5 pages (500 bets) to find older ones
        tab_by_tsn = {}
        try:
            tab_bets = await asyncio.to_thread(
                lambda: get_my_bets(
                    session_found["legacy_token"], acct_num,
                    session_found["proxy_url"], count=100, status="ALL", max_pages=5
                )
            )
            for tb in tab_bets.get("bets", []):
                if tb.get("tsn"):
                    tab_by_tsn[tb["tsn"]] = tb
            accounts_checked.append(acct_num)
        except Exception as e:
            error_msg = str(e)
            # Auto-re-auth: if legacy token expired, refresh it and retry
            if "401" in error_msg and session_found.get("password") and session_found.get("account_number"):
                try:
                    logger.info(f"Legacy token expired for {acct_num}, re-authenticating...")
                    legacy_result = await asyncio.to_thread(
                        lambda: legacy_authenticate(
                            session_found["account_number"],
                            session_found["password"],
                            session_found.get("proxy_url"),
                        )
                    )
                    new_token = legacy_result.get("legacy_token", "")
                    if new_token:
                        session_found["legacy_token"] = new_token
                        if session_sid:
                            sessions[session_sid]["legacy_token"] = new_token
                        logger.info(f"Re-authenticated legacy token for {acct_num}")
                        # Retry the fetch
                        tab_bets = await asyncio.to_thread(
                            lambda: get_my_bets(
                                new_token, acct_num,
                                session_found["proxy_url"], count=100, status="ALL", max_pages=5
                            )
                        )
                        for tb in tab_bets.get("bets", []):
                            if tb.get("tsn"):
                                tab_by_tsn[tb["tsn"]] = tb
                        accounts_checked.append(acct_num)
                    else:
                        accounts_failed.append({"account": acct_num, "error": "Re-auth returned no token"})
                        continue
                except Exception as reauth_err:
                    accounts_failed.append({"account": acct_num, "error": f"Re-auth failed: {reauth_err}"})
                    continue
            else:
                accounts_failed.append({"account": acct_num, "error": error_msg})
                continue

        # Match and update
        for bet in acct_bets:
            tsn = bet.get("tsn")
            if not tsn or tsn not in tab_by_tsn:
                continue

            tab_bet = tab_by_tsn[tsn]
            tab_status = tab_bet.get("status", "")

            # Normalize TAB's status vocabulary to the handful of local values
            # the rest of the system (frontend badges + BetOps grading map) knows.
            # TAB can return "Won", "Lost", "Refunded", "Void", "Cashed Out",
            # "Partially Refunded", "Settled - Winner/Loser", etc. — everything
            # settled needs to flow through update_bet_status so BetOps receives
            # the grade. Anything still open (Pending/Placed/empty) is skipped.
            _ts_lower = (tab_status or "").strip().lower()
            canonical = None
            if _ts_lower in ("won", "settled - winner", "winner"):
                canonical = "Won"
            elif _ts_lower in ("lost", "settled - loser", "loser"):
                canonical = "Lost"
            elif _ts_lower in ("refunded", "void", "voided",
                                "partially refunded", "part refund",
                                "cancelled", "canceled"):
                canonical = "Refunded"
            elif _ts_lower in ("cashed out", "cashedout", "cash out"):
                canonical = "Cashed Out"
            elif _ts_lower in ("", "pending", "placed", "open", "in play"):
                canonical = None  # still live — do nothing
            else:
                # Unknown settled-looking status. Log it so we can extend the map
                # rather than silently dropping. We deliberately do NOT update the
                # bet for unknown statuses — safer to require explicit opt-in.
                logger.warning(
                    "check-results: unknown TAB status %r for tsn=%s acct=%s — skipping",
                    tab_status, tsn, acct_num,
                )
                canonical = None

            if canonical is not None:
                # Payout trusts TAB's number verbatim — handles void-adjusted
                # reduced-odds wins correctly (TAB computes the real payout).
                payout = tab_bet.get("payout", "$0.00")
                if canonical == "Lost":
                    payout = "$0.00"
                elif canonical == "Refunded" and not payout:
                    payout = bet.get("stake", "$0.00")  # stake-back default
                await update_bet_status(bet["id"], canonical, payout)
                updated_count += 1
                updated_bets.append({
                    "id": bet["id"], "tsn": tsn, "status": canonical,
                    "tab_raw_status": tab_status, "payout": payout, "account": acct_num,
                })

    return {
        "updated": updated_count,
        "bets": updated_bets,
        "accounts_checked": accounts_checked,
        "accounts_failed": accounts_failed,
        "pending_remaining": len(pending) - updated_count,
    }


@app.post("/api/sync-manual-bets")
async def api_sync_manual_bets(_user: dict = Depends(_verify_app_token)):
    """Scan TAB bet history for all active sessions and import bets not already in DB as Manual entries."""
    username = _user["username"]

    # Fetch all TSNs already stored for this user
    known_tsns = await get_all_tsns(username)

    imported = []
    accounts_checked = []
    accounts_failed = []

    # Build account ownership: only sync accounts that belong to this user
    # Match by email since account_number may be null in tab_accounts
    user_accounts = await get_accounts(username)
    user_emails = {a["email"].lower() for a in user_accounts if a.get("email")}
    account_label_map = {}
    for a in user_accounts:
        if a.get("account_number"):
            account_label_map[a["account_number"]] = a["label"]
        if a.get("email"):
            account_label_map[a["email"].lower()] = a["label"]

    # Loop only sessions that belong to this user (matched by email)
    for sid, s in sessions.items():
        acct_num = s.get("account_number", "")
        legacy_token = s.get("legacy_token", "")
        email = s.get("email", "").lower()
        if not acct_num or not legacy_token:
            continue
        # Only sync if this session's email matches one of the user's accounts
        if email and email not in user_emails:
            continue

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda _s=s, _a=acct_num: get_my_bets(
                    _s["legacy_token"], _a, _s.get("proxy_url"), count=200, status="ALL"
                )
            )
            accounts_checked.append(acct_num)
        except Exception as e:
            accounts_failed.append({"account": acct_num, "error": str(e)})
            continue

        acct_label = account_label_map.get(acct_num, account_label_map.get(email, acct_num))

        for tb in result.get("bets", []):
            tsn = tb.get("tsn", "")
            if not tsn or tsn in known_tsns:
                continue

            # Normalise legs: TAB returns list of strings (eventName already extracted in get_my_bets)
            raw_legs = tb.get("legs", [])
            legs = []
            for leg in raw_legs:
                if isinstance(leg, dict):
                    legs.append({"name": leg.get("eventName") or leg.get("name") or str(leg)})
                else:
                    legs.append({"name": str(leg)})

            # Only import player prop bets (skip team handicaps, singles with no legs, racing)
            if not legs:
                continue
            legs_text = " ".join(l.get("name", "") for l in legs).lower()
            is_player_prop = any(kw in legs_text for kw in ["pts", "disp", "reb", "ast", "goal", "tackle", "mark", "hit", "point", "over", "under", "multi", "sgm", "+"])
            if not is_player_prop:
                continue

            # Parse money strings (strip $ and commas)
            def _money(v):
                return str(round(float(str(v or "0").replace("$", "").replace(",", "") or 0), 2))

            stake = _money(tb.get("stake", "0"))
            payout = tb.get("payout", "$0.00")

            # Map TAB status to our schema
            tab_status = tb.get("status", "Unknown")
            if tab_status.lower() in ("won", "win"):
                status = "Won"
            elif tab_status.lower() in ("lost", "lose", "loser"):
                status = "Lost"
            else:
                status = "Pending"

            bet = {
                "account_number": acct_num,
                "account_label": acct_label,
                "tsn": tsn,
                "bet_type": "Manual",
                "legs": legs,
                "combined_odds": str(tb.get("odds", "0")),
                "stake": stake,
                "status": status,
                "payout": payout if status == "Won" else None,
                "raw_response": tb,
                "source": "manual",
            }

            try:
                bet_id = await save_bet(username, bet)
                known_tsns.add(tsn)
                imported.append({"id": bet_id, "tsn": tsn, "account": acct_num})
            except Exception as e:
                logger.warning(f"Failed to import manual bet TSN={tsn}: {e}")

    return {
        "imported": len(imported),
        "bets": imported,
        "accounts_checked": accounts_checked,
        "accounts_failed": accounts_failed,
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
                "token_exp": exp,
            })
    return {"sessions": active}


@app.get("/api/session")
async def api_session(session_id: str, _user: dict = Depends(_verify_app_token)):
    """Get current session info (no sensitive data)."""
    s = await _get_session_async(session_id)
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
    s = await _get_session_async(req.session_id)
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


# ─── Execution Engine v2 ──────────────────────────────────────────────────────

class EngineRunRequest(_BaseModel):
    bets: list[dict]          # parsed CSV bets [{bet_type, game_id, bet, odds, min_odds, ev_pct, units, ...}]
    account_ids: list[str]    # account_numbers to use
    sport: str = "NBA"
    competition: str = "NBA"
    unit_size: float = 75


# In-memory job store for async engine runs


@app.post("/api/csb/execute-engine")
async def api_csb_execute_engine(req: EngineRunRequest, _user: dict = Depends(_verify_app_token)):
    """Start a CSB engine run as a background job. Returns job_id immediately.
    Poll /api/csb/engine-status/{job_id} for results.
    """
    from engine_integration import execute_csb_run

    # Get account infos from DB
    user_accounts = await get_accounts(_user["username"])
    req_ids_set = set(str(x) for x in req.account_ids if x)
    account_infos = [
        a for a in user_accounts
        if str(a.get("account_number", "")) in req_ids_set
        or str(a.get("id", "")) in req_ids_set
        or str(a.get("label", "")) in req_ids_set
        or str(a.get("email", "")) in req_ids_set
    ]

    if not account_infos:
        logger.warning("No matching accounts. req_ids=%s, db_accounts=%s",
                       req.account_ids,
                       [(a.get("label"), a.get("account_number"), a.get("id")) for a in user_accounts])
        raise HTTPException(400, f"No matching accounts found. Sent: {req.account_ids}. Available: {[a.get('account_number') or a.get('label') for a in user_accounts]}")

    job_id = str(uuid.uuid4())[:8]
    await _save_job(job_id, "csb_engine", "running")

    logger.info("Engine job %s: %d bets, %d accounts, sport=%s, unit=$%.0f",
                job_id, len(req.bets), len(account_infos), req.sport, req.unit_size)

    async def _run():
        try:
            results = await execute_csb_run(
                parsed_bets=req.bets,
                account_infos=account_infos,
                sessions=sessions,
                username=_user["username"],
                sport=req.sport,
                competition=req.competition,
                unit_size=req.unit_size,
            )

            all_entries = results.get("regular", []) + results.get("sgm", [])
            placed = sum(1 for e in all_entries if e.get("result") and e["result"].success)
            failed = sum(1 for e in all_entries if e.get("result") and not e["result"].success and not getattr(e["result"], "skipped", False))
            skipped = sum(1 for e in all_entries if e.get("result") is None or (e.get("result") and getattr(e["result"], "skipped", False)))
            total_staked = sum(e["result"].confirmed_amount for e in all_entries if e.get("result") and e["result"].success)

            await _save_job(job_id, "csb_engine", "done", {
                    "placed": placed,
                    "failed": failed,
                    "skipped": skipped,
                    "total_staked": total_staked,
                    "state": results.get("state", {}),
                    "results": [
                        {
                            "account": e.get("account"),
                            "account_label": e.get("account_label"),
                            "runner_id": e.get("runner_id"),
                            "bet_description": e.get("bet_description"),
                            "game_id": e.get("game_id"),
                            "is_retry": e.get("is_retry", False),
                            "success": e["result"].success if e.get("result") else False,
                            "confirmed_amount": e["result"].confirmed_amount if e.get("result") else 0,
                            "actual_odds": e["result"].actual_odds if e.get("result") else 0,
                            "error": e["result"].error if e.get("result") else "Skipped",
                            "bet_id": e["result"].bet_id if e.get("result") else "",
                        }
                        for e in all_entries
                    ],
            })
        except Exception as e:
            logger.error("Engine job %s error: %s", job_id, e, exc_info=True)
            await _save_job(job_id, "csb_engine", "error", {"error": str(e)})

    asyncio.create_task(_run())
    return {"job_id": job_id, "status": "running"}


@app.get("/api/csb/engine-status/{job_id}")
async def api_csb_engine_status(job_id: str, _user: dict = Depends(_verify_app_token)):
    """Poll engine job status."""
    job = await _get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


class CheckPlacedRequest(_BM):
    """Pre-retry idempotency check. Frontend sends the bet signatures it's about to
    retry; backend fetches recent TAB bet history for those accounts and reports which
    signatures already match a real placed bet. Defense against the 502-cascade scenario
    where TAB accepted the bet but the response was lost — retrying would double-place."""
    account_numbers: list[str]
    lookback_minutes: int = 30
    # Each signature: { "index": int, "legs": ["player name tokens, ..."], "combined_odds": str }
    signatures: list[dict]


@app.post("/api/csb/check-placed")
async def api_csb_check_placed(req: CheckPlacedRequest, _user: dict = Depends(_verify_app_token)):
    """For each signature, find any matching TAB bet in the last N minutes across the
    provided accounts. Matching requires all leg substrings present + combined_odds match
    (±0.05 tolerance). Returns { matches: { <index>: { account_number, tsn, stake } } }."""
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    import asyncio as _asyncio

    # Fetch TAB history per account in parallel (one real call each — TAB rate-limits)
    async def _fetch(acct_num: str) -> list[dict]:
        session_found = None
        for sid, s in sessions.items():
            if s.get("account_number") == acct_num and s.get("legacy_token"):
                session_found = s; break
        if not session_found:
            logger.warning(f"check-placed: no session for {acct_num}")
            return []
        try:
            tab_bets = await _asyncio.to_thread(
                get_my_bets,
                session_found["legacy_token"], acct_num,
                session_found.get("proxy_url"), 50, "ALL", 1,
            )
            out = tab_bets.get("bets", [])
            for b in out:
                b["_account_number"] = acct_num
            return out
        except Exception as e:
            logger.warning(f"check-placed: fetch failed for {acct_num}: {e}")
            return []

    lists = await _asyncio.gather(*(_fetch(a) for a in req.account_numbers))
    all_bets = [b for lst in lists for b in lst]

    # Filter to recent window
    cutoff = _dt.now(_tz.utc) - _td(minutes=max(1, req.lookback_minutes))
    def _parse_ts(s: str):
        if not s: return None
        try:
            return _dt.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    recent = [b for b in all_bets if (ts := _parse_ts(b.get("timestamp", ""))) is None or ts >= cutoff]

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    def _odds_match(a, b) -> bool:
        try:
            fa = float(str(a).replace("$", "").strip())
            fb = float(str(b).replace("$", "").strip())
            return abs(fa - fb) <= 0.05
        except Exception:
            return False

    matches: dict = {}
    for sig in req.signatures:
        idx = sig.get("index")
        sig_legs = [_norm(l) for l in sig.get("legs", []) if l]
        sig_odds = sig.get("combined_odds")
        if not sig_legs or not sig_odds:
            continue
        for bet in recent:
            bet_legs_text = _norm(" ".join(bet.get("legs", [])))
            # All signature leg tokens must appear in the bet's concatenated legs text
            if not all(leg in bet_legs_text for leg in sig_legs):
                continue
            if not _odds_match(bet.get("odds", "0"), sig_odds):
                continue
            matches[str(idx)] = {
                "account_number": bet.get("_account_number"),
                "tsn": bet.get("tsn"),
                "stake": bet.get("stake"),
                "odds": bet.get("odds"),
                "status": bet.get("status"),
            }
            break  # first match wins — stops rechecking this signature
    return {"matches": matches, "checked": len(recent)}


@app.post("/api/quick-place")
async def api_quick_place(req: QuickBetRequest, _user: dict = Depends(_verify_app_token)):
    """Resolve, validate, and place all qualifying bets with human-like delays."""
    import random as _random

    s = await _get_session_async(req.session_id)
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
                place_result = await asyncio.to_thread(
                    lambda: place_sgm_bet(
                        legacy_token=s["legacy_token"],
                        account_number=s["account_number"],
                        propositions=resolved["propositions"],
                        combined_odds=resolved["combined_odds"],
                        stake=stake,
                        proxy_url=s["proxy_url"],
                    )
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
                        "source": "csb",
                        "match_name": row.game_id,
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


# ─── CSB (Resolve + Place) ────────────────────────────────────────────────────

class CsbBet(_BM):
    session_id: str
    bet_type: str = "SGM"  # "SGM" or "Multi"
    game_id: str = ""
    bet: str = ""  # "Player1 N+ Stat/Player2 N+ Stat"
    odds: float = 0
    min_odds: float = 0
    ev_pct: float = 0
    units: float = 1
    stake: float = 10
    sport: str = "AFL Football"
    competition: str = "AFL"


class CsbWarmupRequest(_BM):
    session_id: str
    sports: list[dict] = []  # [{"sport": "AFL Football", "competition": "AFL"}, ...]


@app.post("/api/csb-warmup")
async def api_csb_warmup(req: CsbWarmupRequest, _user: dict = Depends(_verify_app_token)):
    """Pre-fetch all SGM propositions for given sports so resolution is instant."""
    import time as _t
    s = await _get_session_async(req.session_id)
    results = []
    for sp in req.sports:
        sport = sp.get("sport", "AFL Football")
        comp = sp.get("competition", "AFL")
        start = _t.time()
        try:
            props = _get_all_sport_props(s["token"], s["proxy_url"], sport, comp)
            elapsed = _t.time() - start
            # Count unique matches
            match_names = set(p.get("_match_name", "") for p in props if p.get("_match_name"))
            results.append({
                "sport": sport,
                "competition": comp,
                "props_count": len(props),
                "matches_count": len(match_names),
                "seconds": round(elapsed, 1),
                "cached": elapsed < 1,  # If it came back fast, it was cached
            })
        except Exception as e:
            results.append({
                "sport": sport,
                "competition": comp,
                "error": str(e),
            })
    return {"results": results}


@app.get("/api/csb-props")
async def api_csb_props(session_id: str, sport: str = "AFL Football", competition: str = "AFL",
                        _user: dict = Depends(_verify_app_token)):
    """Export all cached/fetched props for a sport — player names, markets, prop IDs, odds."""
    s = await _get_session_async(session_id)
    props = _get_all_sport_props(s["token"], s["proxy_url"], sport, competition, use_regular=True)
    export = []
    for p in props:
        export.append({
            "propositionId": p.get("propositionId"),
            "player": p.get("selectionName", p.get("name", "")),
            "market": p.get("marketName", ""),
            "odds": p.get("returnWin", 0),
            "match": p.get("_match_name", ""),
        })
    return {"props": export, "count": len(export), "sport": sport, "competition": competition}


@app.get("/api/disposals/{match_id}")
async def api_disposals(match_id: str, session_id: str = None, sport: str = "AFL Football",
                        competition: str = "AFL", _user: dict = Depends(_verify_app_token)):
    """Get disposal lines (15+, 20+, 25+, 30+) per player for a match with proposition IDs."""
    # Find any valid session (don't rely on the one passed — it might be expired)
    s = None
    if session_id:
        try:
            s = await _get_session_async(session_id)
        except Exception:
            pass
    if not s:
        for sid, sess in sessions.items():
            claims = decode_token_claims(sess.get("token", ""))
            exp = claims.get("exp", 0)
            if exp and time.time() < exp - 300 and sess.get("proxy_url"):
                s = sess
                break
    if not s:
        raise HTTPException(404, "No valid TAB session. Please login on the Dashboard.")

    # Try match_id as-is first; if it's a short ID, look up the full match name
    match_name_url = match_id.replace(" ", "%20")
    if not "%" in match_name_url and " " not in match_id:
        # Short ID like NMlvCrl0 — look up full name
        try:
            all_matches = await asyncio.to_thread(get_matches, s["token"], s["proxy_url"], sport, competition)
            for m in all_matches:
                if m["match_id"] == match_id:
                    match_name_url = m.get("match_name_url", match_id)
                    break
        except Exception:
            pass

    # Get regular match markets (has all player props)
    try:
        data = await asyncio.to_thread(get_match_markets, s["token"], match_name_url, s["proxy_url"], sport, competition)
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch match markets: {e}")

    markets = data.get("markets", [])
    LINES = [15, 20, 25, 30]

    # Build player -> {15+: odds, 15+_propId: id, 20+: odds, ...}
    player_map = {}
    for mkt in markets:
        bet_option = mkt.get("betOption", "")
        for line in LINES:
            if bet_option == f"{line}+ Disposals":
                for prop in mkt.get("propositions", []):
                    if not prop.get("isOpen", True) if "isOpen" in prop else prop.get("bettingStatus") != "Open":
                        continue
                    name = prop.get("name", "")
                    odds = prop.get("returnWin", 0)
                    prop_id = prop.get("number", prop.get("id"))
                    if name not in player_map:
                        player_map[name] = {"name": name}
                    player_map[name][f"{line}+"] = odds
                    player_map[name][f"{line}+_propId"] = prop_id

    players = sorted(player_map.values(), key=lambda p: p.get("name", ""))
    return {"players": players, "match_id": match_id, "count": len(players)}


@app.post("/api/csb-resolve-one")
async def api_csb_resolve_one(req: CsbBet, _user: dict = Depends(_verify_app_token)):
    """Resolve a single CSB bet (SGM or Multi) against TAB markets."""
    s = await _get_session_async(req.session_id)
    resolved = resolve_csb_bet(
        token=s["token"],
        proxy_url=s["proxy_url"],
        bet_type=req.bet_type,
        game_id=req.game_id,
        bet_description=req.bet,
        stake=str(req.stake),
        min_odds=req.min_odds,
        sport=req.sport,
        competition=req.competition,
    )
    return resolved


def _kelly_drift_stake(original_stake: float, units: float, tipped_odds: float,
                       actual_odds: float, min_odds: float) -> float:
    """Recalculate stake when odds drift using Shadow's Kelly ratio formula.
    Same logic as frontend applyOddsDrift + getStakeForBet."""
    if not actual_odds or not tipped_odds or actual_odds >= tipped_odds:
        return original_stake  # Odds same or better — keep full stake
    if not min_odds or min_odds <= 1:
        return original_stake
    if actual_odds <= min_odds:
        return 0  # Below min — no edge

    p = 1.0 / min_odds  # True probability from min odds
    ev_target = (p * tipped_odds) - 1
    ev_actual = (p * actual_odds) - 1
    if ev_target <= 0 or ev_actual <= 0:
        return 0

    kelly_target = ev_target / (tipped_odds - 1)
    kelly_actual = ev_actual / (actual_odds - 1)
    multiplier = kelly_actual / kelly_target

    # Derive unit_size from original stake/units, apply drift
    unit_size = original_stake / units if units > 0 else original_stake
    new_stake = units * multiplier * unit_size
    # Anti-detection: round to nearest $10
    new_stake = round(new_stake / 10) * 10
    return max(10, new_stake)


@app.post("/api/csb-place-one")
async def api_csb_place_one(req: CsbBet, _user: dict = Depends(_verify_app_token)):
    """Resolve and place a single CSB bet. Auto-reprices on 'Price has changed' (up to 3 attempts).
    If props are already cached from a recent warmup/resolve, uses cached data (fast path ~2s vs ~12s)."""
    s = await _get_session_async(req.session_id)
    await _ensure_legacy_token(s, req.session_id)

    MAX_PRICE_RETRIES = 3
    resolved = None
    place_result = None
    current_stake = req.stake

    for attempt in range(1, MAX_PRICE_RETRIES + 1):
        # Use cached props for fast resolution (skips full match/market fetch if cached)
        resolved = resolve_csb_bet(
            token=s["token"],
            proxy_url=s["proxy_url"],
            bet_type=req.bet_type,
            game_id=req.game_id,
            bet_description=req.bet,
            stake=str(req.stake),
            min_odds=req.min_odds,
            sport=req.sport,
            competition=req.competition,
        )

        if not resolved["resolved"] or not resolved["meets_minimum"]:
            # Retry on proxy/SSL errors during resolve
            resolve_err = (resolved.get("error") or "").lower()
            if any(k in resolve_err for k in ("ssl", "proxy", "boringssl", "connect", "tunnel", "curl")) and attempt < MAX_PRICE_RETRIES:
                logger.info(f"CSB resolve proxy error on attempt {attempt}, retrying... ({resolved.get('error', '')[:80]})")
                import time as _t, random as _rand
                _t.sleep(_rand.uniform(0.5, 1.5))
                continue
            return {"success": False, "resolved": resolved, "error": resolved.get("error")}

        # Recalculate stake on retry if odds drifted
        if attempt > 1 and req.units > 0 and req.odds > 0 and req.min_odds > 0:
            new_odds = float(resolved.get("combined_odds", 0))
            if new_odds > 0:
                current_stake = _kelly_drift_stake(req.stake, req.units, req.odds, new_odds, req.min_odds)
                if current_stake <= 0:
                    return {"success": False, "error": "Odds drifted below minimum on retry — skipped", "resolved": resolved}
                logger.info(f"CSB retry {attempt}: repriced stake ${req.stake:.2f} -> ${current_stake:.2f} (odds {req.odds} -> {new_odds})")

        try:
            if req.bet_type.upper() == "SGM":
                place_result = await asyncio.to_thread(
                    lambda: place_sgm_bet(
                        legacy_token=s["legacy_token"],
                        account_number=s["account_number"],
                        propositions=resolved["propositions"],
                        combined_odds=resolved["combined_odds"],
                        stake=str(current_stake),
                        proxy_url=s["proxy_url"],
                    )
                )
            else:
                place_result = await asyncio.to_thread(
                    lambda: place_multi_bet(
                        legacy_token=s["legacy_token"],
                        account_number=s["account_number"],
                        legs=[{"propositionId": p["propositionId"], "odds": p["odds"]} for p in resolved["propositions"]],
                        stake=str(current_stake),
                        proxy_url=s["proxy_url"],
                    )
                )
        except Exception as e:
            place_result = {"success": False, "error": str(e)}

        # Check if retryable error — price changed, transient proxy/SSL, or upstream 5xx.
        # 5xx inclusion catches the exact 502-cascade scenario where TAB's gateway briefly
        # flakes: previously bubbled up as a failure the user had to manually retry, now
        # heals internally with exponential backoff.
        err = place_result.get("error", "")
        err_lower = err.lower()
        is_price_change = "price" in err_lower and "changed" in err_lower
        is_proxy_error = any(k in err_lower for k in ("ssl", "proxy", "boringssl", "connect", "tunnel", "curl"))
        is_5xx = any(code in err_lower for code in (" 502", " 503", " 504", "502 ", "503 ", "504 ", "gateway", "upstream", "timeout", "timed out"))
        if not place_result.get("success") and (is_price_change or is_proxy_error or is_5xx) and attempt < MAX_PRICE_RETRIES:
            logger.info(f"CSB retryable error on attempt {attempt}: {err[:100]}")
            import time as _t, random as _rand
            # Exponential backoff with jitter: attempt 1 -> ~0.8s, 2 -> ~2s, 3 -> ~5s.
            # Gives TAB's gateway time to recover instead of slamming it during an outage.
            base = 0.5 * (3 ** (attempt - 1))
            _t.sleep(base + _rand.uniform(0, base * 0.5))
            continue

        break  # Success or non-retryable error — stop

    # Save to DB on success (outside retry loop)
    if place_result and place_result.get("success"):
        acct_label = ""
        try:
            accounts = await get_accounts(_user["username"])
            for a in accounts:
                if a["account_number"] == s["account_number"] or a["email"] == s.get("email"):
                    acct_label = a["label"]
                    break
        except Exception:
            pass

        tsn = place_result.get("ticket_number")
        legs_data = [{"name": p.get("name", "")} for p in resolved["propositions"]]

        # Try to get descriptive leg names from TAB
        if tsn and s.get("legacy_token"):
            try:
                import time as _t
                _t.sleep(1)
                tab_history = await asyncio.to_thread(get_my_bets, s["legacy_token"], s["account_number"], s["proxy_url"], 5)
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
            "bet_type": req.bet_type.upper(),
            "legs": legs_data,
            "combined_odds": str(combined),
            "stake": str(current_stake),
            "status": "Pending",
            "raw_response": details,
            "source": "expload",
            "match_name": resolved.get("match_name"),
        })

    return {
        "success": place_result.get("success", False) if place_result else False,
        "ticket_number": place_result.get("ticket_number") if place_result else None,
        "combined_odds": resolved.get("combined_odds") if resolved else None,
        "stake": current_stake,
        "account_balance": place_result.get("account_balance") if place_result else None,
        "error": place_result.get("error") if place_result else "No placement attempted",
        "resolved": resolved,
        "retries": attempt if attempt > 1 else 0,
    }


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
        acct_info = await asyncio.to_thread(get_account_info, token, customer_id, proxy)
        account_number = acct_info["account_number"]
        balance = acct_info["account_balance"]
    except Exception as e:
        logger.warning(f"Account info with pasted token failed: {e}")
        # Try without proxy
        if proxy:
            try:
                acct_info = await asyncio.to_thread(get_account_info, token, customer_id, None)
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

        # For non-SGM (Multi), if pricing didn't return combined odds, calculate from individual legs
        if not result["combined_odds"] and not bet.is_same_event_multi:
            try:
                combined = 1.0
                for l in resolved_legs:
                    combined *= float(l["odds"])
                result["combined_odds"] = f"{combined:.2f}"
            except (ValueError, TypeError):
                pass

        return result

    except Exception as e:
        result["error"] = str(e)
        return result


@app.post("/api/place-json")
async def api_place_json(req: JsonBet, _user: dict = Depends(_verify_app_token)):
    """Resolve and place a single bet from JSON format."""
    import random as _random

    s = await _get_session_async(req.session_id)
    await _ensure_legacy_token(s, req.session_id)

    resolved = await asyncio.to_thread(lambda: _resolve_json_bet(s["token"], s["legacy_token"], s["proxy_url"], req))

    if not resolved["resolved"]:
        return {"success": False, "resolved": resolved}

    if not resolved.get("combined_odds"):
        return {"success": False, "error": "No combined odds from price check", "resolved": resolved}

    # Round stake to nearest $0.50 (TAB rejects non-multiples)
    import math as _math
    stake_val = float(req.stake)
    stake_val = _math.floor(stake_val * 2) / 2  # Round down to nearest $0.50
    if stake_val < 0.50:
        stake_val = 0.50
    stake_str = f"{stake_val:.2f}"

    # Place the bet
    try:
        if req.is_same_event_multi:
            place_result = await asyncio.to_thread(
                lambda: place_sgm_bet(
                    legacy_token=s["legacy_token"],
                    account_number=s["account_number"],
                    propositions=resolved["propositions"],
                    combined_odds=resolved["combined_odds"],
                    stake=stake_str,
                    proxy_url=s["proxy_url"],
                )
            )
        else:
            place_result = await asyncio.to_thread(
                lambda: place_multi_bet(
                    legacy_token=s["legacy_token"],
                    account_number=s["account_number"],
                    legs=[{"propositionId": l["propositionId"], "odds": l["odds"]} for l in resolved["propositions"]],
                    stake=stake_str,
                    proxy_url=s["proxy_url"],
                )
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
                    tab_history = await asyncio.to_thread(get_my_bets, s["legacy_token"], s["account_number"], s["proxy_url"], 5)
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
                "stake": stake_str,
                "status": "Pending",
                "raw_response": details,
                "source": "expload",
                "match_name": resolved.get("match_name"),
            })

        return {
            "success": place_result.get("success", False),
            "ticket_number": place_result.get("ticket_number"),
            "combined_odds": resolved.get("combined_odds"),
            "stake": stake_val,
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

    s = await _get_session_async(req.session_id)
    await _ensure_legacy_token(s, req.session_id)

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
        resolved = await asyncio.to_thread(lambda: _resolve_json_bet(s["token"], s["legacy_token"], s["proxy_url"], bet))

        if not resolved["resolved"]:
            results.append({"index": i, "success": False, "resolved": resolved})
            continue

        try:
            if bet.is_same_event_multi:
                place_result = await asyncio.to_thread(
                    lambda: place_sgm_bet(
                        legacy_token=s["legacy_token"],
                        account_number=s["account_number"],
                        propositions=resolved["propositions"],
                        combined_odds=resolved["combined_odds"],
                        stake=str(bet.stake),
                        proxy_url=s["proxy_url"],
                    )
                )
            else:
                place_result = await asyncio.to_thread(
                    lambda: place_multi_bet(
                        legacy_token=s["legacy_token"],
                        account_number=s["account_number"],
                        legs=[{"propositionId": l["propositionId"], "odds": l["odds"]} for l in resolved["propositions"]],
                        stake=str(bet.stake),
                        proxy_url=s["proxy_url"],
                    )
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
                        tab_history = await asyncio.to_thread(get_my_bets, s["legacy_token"], s["account_number"], s["proxy_url"], 5)
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
                    "source": "csv_paste",
                    "match_name": resolved.get("match_name"),
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


# ─── Multi-Bookie Routes ────────────────────────────────────────────────────
from multi_routes import multi_router
app.include_router(multi_router)

# ─── bet365 Routes ──────────────────────────────────────────────────────────
from bet365_routes import router as bet365_router
app.include_router(bet365_router)

# ─── Sportsbet Routes ──────────────────────────────────────────────────────
from sportsbet_routes import router as sportsbet_router
app.include_router(sportsbet_router)

# ─── Health ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    valid = 0
    for s in sessions.values():
        claims = decode_token_claims(s.get("token", ""))
        exp = claims.get("exp", 0)
        if exp and time.time() < exp - 300:
            valid += 1
    return {"status": "ok", "active_sessions": valid, "total_sessions": len(sessions)}


@app.post("/api/sessions/purge")
async def purge_expired_sessions(_user: dict = Depends(_verify_app_token)):
    """Remove all expired TAB sessions from memory and DB."""
    purged = 0
    for sid in list(sessions.keys()):
        claims = decode_token_claims(sessions[sid].get("token", ""))
        exp = claims.get("exp", 0)
        if exp and time.time() > exp:
            del sessions[sid]
            await delete_tab_session(sid)
            purged += 1
    return {"purged": purged, "remaining": len(sessions)}


# ── Tab Tokens (SGM Savers) ───────────────────────────────────────

import tab_tokens as _tt

# Group assignments loaded from DB per-request (multi-worker safe)


@app.get("/api/tab-tokens/accounts")
async def tab_tokens_accounts(_user: dict = Depends(_verify_app_token)):
    """List TAB sessions belonging to this user, with saver token status."""
    _tab_token_groups = await load_tab_groups(_user["username"])
    # Build label + ownership map from DB accounts (match by account_number OR email)
    db_accounts = []
    label_map = {}
    user_acct_numbers = set()
    user_emails = set()
    try:
        db_accounts = await get_accounts(_user["username"])
        for a in db_accounts:
            acct_num = str(a.get("account_number") or "")
            email = (a.get("email") or "").lower()
            if acct_num and acct_num != "None":
                label_map[acct_num] = a.get("label", "")
                user_acct_numbers.add(acct_num)
            if email:
                user_emails.add(email)
                label_map[email] = a.get("label", "")
    except Exception:
        pass

    # Merge in-memory sessions with DB sessions (multi-worker fix)
    all_sessions = dict(sessions)
    try:
        db_sessions = await load_all_tab_sessions()
        for sid, sdata in db_sessions.items():
            if sid not in all_sessions:
                all_sessions[sid] = sdata
                sessions[sid] = sdata
    except Exception:
        pass

    results = []
    seen_accounts = set()
    sorted_sessions = sorted(
        all_sessions.items(),
        key=lambda x: x[1].get("logged_in_at") or "",
        reverse=True,
    )
    # First pass: collect the rows we'll return (still without balance)
    pending = []  # list of (row_dict, session_dict, is_valid)
    for sid, s in sorted_sessions:
        acct_num = str(s.get("account_number", ""))
        session_email = (s.get("email") or "").lower()
        is_users = acct_num in user_acct_numbers or session_email in user_emails
        if (user_acct_numbers or user_emails) and not is_users:
            continue
        dedup_key = acct_num or session_email
        if dedup_key in seen_accounts:
            continue
        seen_accounts.add(dedup_key)
        claims = decode_token_claims(s.get("token", ""))
        exp = claims.get("exp", 0)
        is_valid = bool(exp and time.time() < exp - 300)
        label = label_map.get(acct_num) or label_map.get(session_email) or s.get("label") or s.get("email", sid[:6])
        row = {
            "session_id": sid,
            "email": s.get("email", ""),
            "account_number": acct_num,
            "label": label,
            "authenticated": is_valid,
            "group": _tab_token_groups.get(acct_num, "A"),
            "saver_count": 0,
            "savers": [],
            "balance": None,
            "balance_value": None,
        }
        pending.append((row, s, is_valid))

    # Second pass: fan out balance lookups in parallel for authenticated sessions.
    async def _fetch_balance(s: dict):
        try:
            bal = await asyncio.to_thread(
                get_balance, s.get("token", ""), s.get("customer_id", ""), s.get("proxy_url", ""),
            )
            return bal.get("account_balance") if isinstance(bal, dict) else None
        except Exception:
            return None

    fetch_targets = [(row, s) for row, s, v in pending if v]
    balances = await asyncio.gather(
        *[_fetch_balance(s) for _, s in fetch_targets], return_exceptions=False,
    ) if fetch_targets else []
    for (row, _s), bal_str in zip(fetch_targets, balances):
        row["balance"] = bal_str
        try:
            if bal_str:
                row["balance_value"] = float(str(bal_str).replace("$", "").replace(",", "").strip())
        except (ValueError, TypeError):
            row["balance_value"] = None

    return [row for row, _s, _v in pending]


@app.post("/api/tab-tokens/fetch-savers")
async def tab_tokens_fetch_savers(_user: dict = Depends(_verify_app_token)):
    """Fetch SGM saver tokens for this user's authenticated sessions."""
    _tab_token_groups = await load_tab_groups(_user["username"])
    user_accts = set()
    user_emails = set()
    try:
        for a in await get_accounts(_user["username"]):
            acct_num = str(a.get("account_number") or "")
            if acct_num and acct_num != "None":
                user_accts.add(acct_num)
            email = (a.get("email") or "").lower()
            if email:
                user_emails.add(email)
    except Exception:
        pass

    # Merge in-memory sessions with DB sessions (multi-worker fix)
    try:
        db_sessions = await load_all_tab_sessions()
        for sid, sdata in db_sessions.items():
            if sid not in sessions:
                sessions[sid] = sdata
    except Exception:
        pass

    total = 0
    account_savers = []
    for sid, s in sessions.items():
        acct = str(s.get("account_number", ""))
        session_email = (s.get("email") or "").lower()
        if (user_accts or user_emails) and acct not in user_accts and session_email not in user_emails:
            continue
        claims = decode_token_claims(s.get("token", ""))
        exp = claims.get("exp", 0)
        if not (exp and time.time() < exp - 300):
            continue
        savers = _tt.get_sgm_savers(s.get("token", ""), acct, s.get("proxy_url", ""), legacy_token=s.get("legacy_token", ""))
        if savers and savers[0].get("_proxy_error"):
            logger.info(f"Saver fetch proxy error for {acct}, auto-rotating...")
            new_proxy = await _rotate_account_proxy(s, sid)
            if new_proxy:
                savers = _tt.get_sgm_savers(s.get("token", ""), acct, new_proxy, legacy_token=s.get("legacy_token", ""))
                if savers and savers[0].get("_proxy_error"):
                    savers = []
            else:
                savers = []
        total += len(savers)
        account_savers.append({
            "session_id": sid, "account_number": acct,
            "email": s.get("email", ""), "label": s.get("label", ""),
            "group": _tab_token_groups.get(acct, "A"), "savers": savers,
        })
    return {"total": total, "accounts": account_savers}


@app.put("/api/tab-tokens/groups")
async def tab_tokens_set_groups(body: dict, _user: dict = Depends(_verify_app_token)):
    """Set group assignments. Body: {"groups": {"account_number": "A", ...}}"""
    existing = await load_tab_groups(_user["username"])
    existing.update(body.get("groups", {}))
    await save_tab_groups(_user["username"], existing)
    return {"ok": True, "groups": existing}


@app.post("/api/tab-tokens/parse-csv")
async def tab_tokens_parse_csv(body: dict, _user: dict = Depends(_verify_app_token)):
    """Parse and validate CSV of SGM bets."""
    return {"count": len(b := _tt.parse_bets_csv(body.get("csv_content", ""))), "bets": b}



@app.post("/api/tab-tokens/execute")
async def tab_tokens_execute(body: dict, _user: dict = Depends(_verify_app_token)):
    """Execute SGM saver bets from CSV across grouped sessions.
    dry_run=True: resolves synchronously (fast).
    dry_run=False: starts async job, returns job_id for polling.
    """
    csv_content = body.get("csv_content", "")
    dry_run = body.get("dry_run", True)
    enabled_accounts = [str(a) for a in body.get("enabled_accounts")] if body.get("enabled_accounts") else None
    bets = _tt.parse_bets_csv(csv_content)
    if not bets:
        return {"error": "No valid bets", "total": 0, "results": []}

    # Filter to this user's accounts
    user_accts = set()
    user_emails = set()
    try:
        for a in await get_accounts(_user["username"]):
            acct_num = str(a.get("account_number") or "")
            if acct_num and acct_num != "None":
                user_accts.add(acct_num)
            email = (a.get("email") or "").lower()
            if email:
                user_emails.add(email)
    except Exception:
        pass

    # Merge in-memory sessions with DB sessions (multi-worker fix)
    try:
        db_sessions = await load_all_tab_sessions()
        for sid, sdata in db_sessions.items():
            if sid not in sessions:
                sessions[sid] = sdata
    except Exception:
        pass

    # Group sessions (deduplicate by account_number, newest first)
    _tab_token_groups = await load_tab_groups(_user["username"])
    group_sessions: dict[str, list[dict]] = {}
    seen_accts = set()
    sorted_sessions = sorted(
        sessions.items(),
        key=lambda x: x[1].get("logged_in_at") or 0,
        reverse=True,
    )
    for sid, s in sorted_sessions:
        acct = str(s.get("account_number", ""))
        session_email = (s.get("email") or "").lower()
        if (user_accts or user_emails) and acct not in user_accts and session_email not in user_emails:
            continue
        if enabled_accounts and acct not in enabled_accounts:
            continue
        claims = decode_token_claims(s.get("token", ""))
        if not (claims.get("exp", 0) and time.time() < claims["exp"] - 300):
            continue
        dedup_key = acct or session_email
        if dedup_key in seen_accts:
            continue
        seen_accts.add(dedup_key)
        grp = _tab_token_groups.get(acct, "A")
        group_sessions.setdefault(grp, []).append(s)

    # Shared resolve + place logic
    async def _resolve_and_place(bets_list, group_sess, is_dry, username, progress_cb=None):
        def _log(msg):
            if progress_cb:
                progress_cb(msg)
        matches_cache: dict[str, list] = {}
        results = []
        # Pre-fetch account labels
        acct_labels = {}
        try:
            for a in await get_accounts(username):
                acct_labels[str(a.get("account_number", ""))] = a.get("label", "")
                acct_labels[(a.get("email") or "").lower()] = a.get("label", "")
        except Exception:
            pass

        for bet in bets_list:
            targets = group_sess.get(bet["group"], [])
            if not targets:
                results.append({"account": "N/A", "group": bet["group"], "match": bet["match"],
                                "legs": bet["legs"], "stake": 0, "odds": None, "has_saver": False,
                                "success": False, "error": f"No sessions in group '{bet['group']}'", "dry_run": is_dry})
                continue

            # ── Cross-match MULTI ──
            if bet.get("bet_type") == "MULTI":
                for s in targets:
                    token, legacy, acct, proxy = s.get("token", ""), s.get("legacy_token", ""), s.get("account_number", ""), s.get("proxy_url", "")
                    acct_label = acct_labels.get(acct) or acct_labels.get((s.get("email") or "").lower()) or acct
                    if not legacy:
                        results.append({"account": acct, "account_label": acct_label, "group": bet["group"],
                                        "match": bet["match"], "legs": bet["legs"], "stake": 0, "odds": None,
                                        "has_saver": False, "success": False, "error": "No legacy token", "dry_run": is_dry})
                        continue
                    multi_props = []
                    resolve_error = None
                    for ml in bet["multi_legs"]:
                        mlck = f"{ml['sport']}:{ml['competition']}"
                        if mlck not in matches_cache:
                            matches_cache[mlck] = await asyncio.to_thread(lambda _t=s: _tt.get_matches(_t["token"], ml["sport"], ml["competition"], _t.get("proxy_url")))
                        ml_match = _tt.find_match(matches_cache[mlck], ml["match"])
                        if not ml_match:
                            resolve_error = f"Match not found: {ml['match']}"
                            break
                        ml_markets = await asyncio.to_thread(lambda: _tt.get_sgm_markets(token, ml_match, ml["sport"], ml["competition"], proxy))
                        prop = _tt.resolve_proposition(ml["leg"], ml_markets, ml_match)
                        if "error" in prop:
                            resolve_error = f"{ml['match']}: {prop['error']}"
                            break
                        multi_props.append(prop)
                    if resolve_error:
                        results.append({"account": acct, "account_label": acct_label, "group": bet["group"],
                                        "match": bet["match"], "legs": bet["legs"], "stake": 0, "odds": None,
                                        "has_saver": False, "success": False, "error": resolve_error, "dry_run": is_dry})
                        continue
                    combined_odds = 1.0
                    for p in multi_props:
                        combined_odds *= p["odds"]
                    combined_odds = round(combined_odds, 2)
                    resolved_legs = [{"name": p["name"], "odds": p["odds"], "market": p.get("market", ""), "proposition_id": p.get("proposition_id")} for p in multi_props]
                    stake = 10.0
                    if is_dry:
                        results.append({"account": acct, "account_label": acct_label, "group": bet["group"],
                                        "match": bet["match"], "legs": [p["name"] for p in multi_props],
                                        "resolved_legs": resolved_legs, "stake": stake, "odds": combined_odds,
                                        "has_saver": False, "success": True, "bet_type": "MULTI",
                                        "error": None, "dry_run": True, "potential_return": round(stake * combined_odds, 2)})
                    else:
                        legs_for_place = [{"propositionId": p["proposition_id"], "odds": f"{p['odds']:.2f}"} for p in multi_props]
                        _log(f"{acct_label}: placing MULTI ${stake:.0f} @ {combined_odds:.2f} — {len(multi_props)} legs...")
                        pr = await asyncio.to_thread(lambda: place_multi_bet(legacy, acct, legs_for_place, str(stake), proxy))
                        if pr.get("success"):
                            _log(f"{acct_label}: ✓ MULTI placed — TSN {pr.get('tsn', '?')}")
                        else:
                            _log(f"{acct_label}: ✗ MULTI failed — {pr.get('error', 'unknown')}")
                        results.append({"account": acct, "account_label": acct_label, "group": bet["group"],
                                        "match": bet["match"], "legs": [p["name"] for p in multi_props],
                                        "resolved_legs": resolved_legs, "stake": stake, "odds": combined_odds,
                                        "has_saver": False, "success": pr.get("success", False), "bet_type": "MULTI",
                                        "bet_id": pr.get("bet_id"), "tsn": pr.get("tsn"),
                                        "error": pr.get("error"), "dry_run": False,
                                        "potential_return": round(stake * combined_odds, 2)})
                continue

            ck = f"{bet['sport']}:{bet['competition']}"
            if ck not in matches_cache:
                matches_cache[ck] = await asyncio.to_thread(lambda _t=targets[0]: _tt.get_matches(_t["token"], bet["sport"], bet["competition"], _t.get("proxy_url")))

            match = _tt.find_match(matches_cache[ck], bet["match"])
            if not match:
                for s in targets:
                    results.append({"account": s.get("account_number", ""), "email": s.get("email", ""),
                                    "group": bet["group"], "match": bet["match"], "legs": bet["legs"],
                                    "stake": 0, "odds": None, "has_saver": False, "success": False,
                                    "error": f"Match not found: {bet['match']}", "dry_run": is_dry})
                continue

            for s in targets:
                token, legacy, acct, proxy = s.get("token", ""), s.get("legacy_token", ""), s.get("account_number", ""), s.get("proxy_url", "")
                acct_label = acct_labels.get(acct) or acct_labels.get((s.get("email") or "").lower()) or acct

                markets = await asyncio.to_thread(lambda: _tt.get_sgm_markets(token, match, bet["sport"], bet["competition"], proxy))
                props = []
                tryscorer_team = None
                for leg in bet["legs"]:
                    prop = _tt.resolve_proposition(leg, markets, match, tryscorer_team=tryscorer_team)
                    props.append(prop)
                    if not tryscorer_team and "error" not in prop:
                        name = prop.get("name", "")
                        if "(" in name and ")" in name:
                            tryscorer_team = name.split("(")[-1].split(")")[0].strip()

                errors = [p for p in props if "error" in p]
                if errors:
                    results.append({"account": acct, "account_label": acct_label, "email": s.get("email"), "group": bet["group"],
                                    "match": bet["match"], "legs": bet["legs"], "stake": 0, "odds": None,
                                    "has_saver": False, "success": False,
                                    "error": str([e["error"] for e in errors]), "dry_run": is_dry})
                    continue

                if any(p["odds"] < 1.10 for p in props):
                    results.append({"account": acct, "account_label": acct_label, "email": s.get("email"), "group": bet["group"],
                                    "match": bet["match"], "legs": bet["legs"], "stake": 0, "odds": None,
                                    "has_saver": False, "success": False,
                                    "error": "Leg(s) below $1.10 min", "dry_run": is_dry})
                    continue

                pricing = await asyncio.to_thread(lambda: _tt.get_sgm_price(token, acct, props, proxy, legacy_token=legacy))
                if pricing.get("error") or not pricing.get("combined_odds"):
                    results.append({"account": acct, "account_label": acct_label, "email": s.get("email"), "group": bet["group"],
                                    "match": bet["match"], "legs": bet["legs"], "stake": 0, "odds": None,
                                    "has_saver": False, "success": False,
                                    "error": pricing.get("error", "Pricing failed"), "dry_run": is_dry})
                    continue

                co = pricing["combined_odds"]
                co_f = float(co)
                deco = pricing.get("deco_tokens", [])
                leg_deco = pricing.get("leg_deco_token")
                has_saver = pricing.get("has_saver", bool(deco))

                if co_f < 2.0:
                    results.append({"account": acct, "account_label": acct_label, "email": s.get("email"), "group": bet["group"],
                                    "match": bet["match"], "legs": bet["legs"], "stake": 0, "odds": co_f,
                                    "has_saver": has_saver, "success": False,
                                    "error": f"Combined odds {co_f:.2f} < $2.00", "dry_run": is_dry})
                    continue

                try:
                    svs = await asyncio.to_thread(lambda: _tt.get_sgm_savers(token, acct, proxy, legacy_token=legacy))
                except Exception as _e:
                    logger.warning("execute: saver fetch failed for %s: %s — defaulting to $10 stake", acct, _e)
                    svs = []
                # Drop proxy-error sentinel rows ({"_proxy_error": True}) — they
                # have no "match" key so empty-string substring matches against
                # any bet and breaks the max_reward lookup downstream.
                svs = [sv for sv in svs if not sv.get("_proxy_error")]
                bet_match_lower = bet["match"].lower()
                matching = [sv for sv in svs if sv.get("match") and (
                    bet_match_lower in sv["match"].lower() or
                    sv["match"].lower() in bet_match_lower
                )]
                stake = float(matching[0].get("max_reward") or 10.0) if matching else 10.0

                resolved_legs = []
                for p in props:
                    is_auto = p.get("leg_description", "").startswith("[AUTO]")
                    resolved_legs.append({"name": p["name"], "odds": p["odds"], "market": p.get("market", ""),
                                          "auto_picked": is_auto, "proposition_id": p.get("proposition_id")})

                saver_amount = stake

                if is_dry:
                    results.append({"account": acct, "account_label": acct_label, "email": s.get("email"), "group": bet["group"],
                                    "match": bet["match"], "legs": [p["name"] for p in props],
                                    "resolved_legs": resolved_legs, "stake": stake, "saver_amount": saver_amount,
                                    "odds": co_f, "has_saver": has_saver, "success": True,
                                    "bet_id": None, "error": None, "dry_run": True,
                                    "potential_return": round(stake * co_f, 2)})
                else:
                    if not legacy:
                        results.append({"account": acct, "account_label": acct_label, "email": s.get("email"), "group": bet["group"],
                                        "match": bet["match"], "legs": [p["name"] for p in props], "stake": stake,
                                        "odds": co_f, "has_saver": has_saver, "success": False,
                                        "error": "No legacy token", "dry_run": False})
                        continue

                    saver_token_group_id = matching[0].get("token_group_id") if matching else None
                    _log(f"{acct_label}: placing ${stake:.0f} @ {co_f:.2f} — {', '.join(l['name'] for l in resolved_legs[:2])}...")
                    pr = await asyncio.to_thread(lambda: _tt.place_sgm_with_saver(legacy, acct, props, stake, co, deco, leg_deco, proxy, token_group_id=saver_token_group_id))
                    leg_names = [p["name"] for p in props]
                    if pr.get("success"):
                        _log(f"{acct_label}: ✓ placed — TSN {pr.get('tsn', '?')}")
                    else:
                        _log(f"{acct_label}: ✗ failed — {pr.get('error', 'unknown')}")
                    results.append({"account": acct, "account_label": acct_label, "email": s.get("email"), "group": bet["group"],
                                    "match": bet["match"], "legs": leg_names, "resolved_legs": resolved_legs,
                                    "stake": stake, "saver_amount": saver_amount,
                                    "odds": co_f, "has_saver": has_saver, "success": pr.get("success", False),
                                    "bet_id": pr.get("bet_id"), "tsn": pr.get("tsn"),
                                    "error": pr.get("error"), "dry_run": False,
                                    "potential_return": round(stake * co_f, 2)})

                    if pr.get("success"):
                        try:
                            await save_bet(username, {
                                "account_number": acct, "account_label": acct_label,
                                "tsn": pr.get("tsn") or pr.get("bet_id"), "bet_type": "SGM",
                                "legs": [{"name": n} for n in leg_names], "combined_odds": co,
                                "stake": str(stake), "status": "Pending",
                                "raw_response": pr.get("details"), "source": "tab_tokens",
                                "match_name": bet["match"],
                            })
                        except Exception as e:
                            logger.warning("Failed to save tab_tokens bet to DB: %s", e)
                    await asyncio.sleep(2)

        ok = sum(1 for r in results if r.get("success"))
        return {"total": len(results), "success": ok, "failed": len(results) - ok,
                "with_saver": sum(1 for r in results if r.get("has_saver")),
                "total_stake": sum(r.get("stake", 0) for r in results if r.get("success")),
                "results": results}

    # Dry run: execute synchronously (fast, no placement)
    if dry_run:
        return await _resolve_and_place(bets, group_sessions, True, _user["username"])

    # Live placement: run async in background, return job_id (DB-backed for multi-worker)
    job_id = str(uuid.uuid4())[:8]
    _tt_log: list[str] = []
    await _save_job(job_id, "tab_tokens", "running")
    username = _user["username"]

    async def _run():
        try:
            _tt_log.append(f"Starting placement of {len(bets)} bets...")
            result = await _resolve_and_place(bets, group_sessions, False, username,
                                              progress_cb=lambda msg: _tt_log.append(msg))
            _tt_log.append(f"Done: {result['success']}/{result['total']} placed")
            await _save_job(job_id, "tab_tokens", "complete", {**result, "log": _tt_log})
        except Exception as e:
            logger.error("Tab tokens job %s error: %s", job_id, e, exc_info=True)
            _tt_log.append(f"Error: {e}")
            await _save_job(job_id, "tab_tokens", "error", {"error": str(e), "log": _tt_log})

    asyncio.create_task(_run())
    return {"job_id": job_id, "status": "running"}


@app.get("/api/tab-tokens/job-status/{job_id}")
async def tab_tokens_job_status(job_id: str, _user: dict = Depends(_verify_app_token)):
    """Poll for tab-tokens placement job results."""
    job = await _get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job

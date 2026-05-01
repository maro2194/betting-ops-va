"""
VPS-side client for the Token Farm service (Mini PC).

The token farm handles browser-dependent auth:
  - Sportsbet: patchright + Kasada bypass → JWT tokens
  - bet365: Camoufox → session cookies

Communication: REST API over WireGuard VPN tunnel.
Farm URL configured via TOKEN_FARM_URL env var.
"""
import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

FARM_URL = os.getenv("TOKEN_FARM_URL", "http://172.16.1.6:9000")
FARM_API_KEY = os.getenv("TOKEN_FARM_API_KEY", "botops-farm-2026")
FARM_TIMEOUT = 120  # Browser logins with proxy + geoip can take 60-90 seconds


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {FARM_API_KEY}",
        "Content-Type": "application/json",
    }


# ─── Sportsbet ───────────────────────────────────────────────────────────────

async def sportsbet_login(email: str, password: str, proxy_url: str | None = None) -> dict:
    """Request browser-based Sportsbet login from the token farm.

    Returns: {success, access_token, refresh_token, customer_id, account_number, expires_at}
    """
    try:
        payload = {"email": email, "password": password}
        if proxy_url:
            payload["proxy_url"] = proxy_url
        async with httpx.AsyncClient(timeout=FARM_TIMEOUT) as client:
            resp = await client.post(
                f"{FARM_URL}/auth/sportsbet/login",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm returned HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        data.setdefault("success", True)
        return data
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout (browser login took too long)"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Token farm error: {e}"}


async def sportsbet_get_promos(email: str, password: str, proxy_url: str | None = None) -> dict:
    """Fetch Sportsbet promotions via the token farm browser.

    The farm logs in via patchright and intercepts vouchers/freebets/promos
    responses from SB's frontend — ensures Kasada cookies are present.

    Returns unified promo structure:
      {success, boost_tokens, bonus_back_tokens, deposit_match_tokens, promos, redeemed}
    """
    try:
        payload = {"email": email, "password": password}
        if proxy_url:
            payload["proxy_url"] = proxy_url
        async with httpx.AsyncClient(timeout=FARM_TIMEOUT) as client:
            resp = await client.post(
                f"{FARM_URL}/auth/sportsbet/promos",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {
                "success": False,
                "error": f"Farm returned HTTP {resp.status_code}: {resp.text[:200]}",
                "boost_tokens": 0,
                "bonus_back_tokens": 0,
                "deposit_match_tokens": 0,
                "promos": [],
                "redeemed": [],
            }
        data = resp.json()
        data.setdefault("success", True)
        return data
    except httpx.ConnectError:
        return {
            "success": False,
            "error": "Token farm unreachable — check WireGuard tunnel",
            "boost_tokens": 0,
            "bonus_back_tokens": 0,
            "deposit_match_tokens": 0,
            "promos": [],
            "redeemed": [],
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Token farm promos error: {e}",
            "boost_tokens": 0,
            "bonus_back_tokens": 0,
            "deposit_match_tokens": 0,
            "promos": [],
            "redeemed": [],
        }


async def sportsbet_refresh(refresh_token: str, proxy_url: str | None = None) -> dict:
    """Refresh Sportsbet token via the farm (no browser needed, but routes through farm)."""
    try:
        payload = {"refresh_token": refresh_token}
        if proxy_url:
            payload["proxy_url"] = proxy_url
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{FARM_URL}/auth/sportsbet/refresh",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Refresh failed: HTTP {resp.status_code}"}
        data = resp.json()
        data.setdefault("success", True)
        return data
    except Exception as e:
        return {"success": False, "error": f"Token farm refresh error: {e}"}


# ─── PointsBet ───────────────────────────────────────────────────────────────

async def pointsbet_login(email: str, password: str, proxy_url: str | None = None) -> dict:
    """Request browser-based PointsBet login from the token farm."""
    try:
        payload = {"email": email, "password": password}
        if proxy_url:
            payload["proxy_url"] = proxy_url
        async with httpx.AsyncClient(timeout=FARM_TIMEOUT) as client:
            resp = await client.post(
                f"{FARM_URL}/auth/pointsbet/login",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm returned HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        data.setdefault("success", True)
        return data
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout (PointsBet browser login took too long)"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Token farm error: {e}"}


# ─── bet365 ──────────────────────────────────────────────────────────────────

async def bet365_get_sessions() -> dict:
    """Get active bet365 sessions from the token farm."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{FARM_URL}/auth/bet365/status",
                headers=_headers(),
            )
        if resp.status_code == 200:
            return resp.json()
        return {"active_sessions": 0, "sessions": {}}
    except Exception:
        return {"active_sessions": 0, "sessions": {}}


async def bet365_get_or_login(username: str, password: str, proxy_url: str | None = None) -> dict:
    """Reuse an existing alive AND logged-in bet365 session, or login fresh."""
    status = await bet365_get_sessions()
    sessions = status.get("sessions", {})
    for sid, info in sessions.items():
        if info.get("alive") and info.get("username") == username:
            # Verify session is actually logged in (not just alive browser)
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        f"{FARM_URL}/bet365/debug/{sid}",
                        headers=_headers(),
                    )
                if resp.status_code == 200:
                    debug = resp.json()
                    page_text = debug.get("text", "")
                    if "My Bets" in page_text and "Log In" not in page_text:
                        logger.info(f"bet365: reusing logged-in session {sid} for {username}")
                        return {"success": True, "session_id": sid, "reused": True}
                    else:
                        logger.info(f"bet365: session {sid} alive but logged out, skipping")
            except Exception as e:
                logger.warning(f"bet365: debug check failed for {sid}: {e}")
    logger.info(f"bet365: no logged-in session for {username}, doing fresh login")
    return await bet365_login(username, password, proxy_url=proxy_url)


async def bet365_login(username: str, password: str, proxy_url: str | None = None) -> dict:
    """Request Camoufox-based bet365 login from the token farm.

    Returns session data (cookies, tokens) for bet365 API calls.
    """
    try:
        payload = {"username": username, "password": password}
        if proxy_url:
            payload["proxy_url"] = proxy_url
        async with httpx.AsyncClient(timeout=FARM_TIMEOUT) as client:
            resp = await client.post(
                f"{FARM_URL}/auth/bet365/login",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm returned HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        data.setdefault("success", True)
        return data
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout (Camoufox login took too long)"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Token farm error: {e}"}


async def bet365_place_bet(session_id: str, bet_payload: dict) -> dict:
    """Send bet placement instruction to token farm (browser-based for bet365).

    bet365 has no REST betting API — placement is browser-automated.
    The token farm's Camoufox browser executes the bet.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{FARM_URL}/bet365/place-bet",
                headers=_headers(),
                json={"session_id": session_id, "bet": bet_payload},
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm bet365 place failed: HTTP {resp.status_code}"}
        return resp.json()
    except Exception as e:
        return {"success": False, "error": f"bet365 placement error: {e}"}


async def bet365_megaboost(session_id: str, sport: str, match_team: str, stake: float = 20, boost_index: int = 0) -> dict:
    """Place a Mega Boost on bet365 via Token Farm browser."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{FARM_URL}/bet365/megaboost",
                headers=_headers(),
                json={
                    "session_id": session_id,
                    "sport": sport,
                    "match_team": match_team,
                    "stake": stake,
                    "boost_index": boost_index,
                },
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm megaboost failed: HTTP {resp.status_code}: {resp.text[:200]}"}
        return resp.json()
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout — megaboost placement took too long"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Megaboost error: {e}"}


async def bet365_scan_boosts(session_id: str, sport: str = "HOME", match_team: str = "") -> dict:
    """List available boosts on bet365 via Token Farm browser."""
    try:
        params = {}
        if sport and sport.upper() != "HOME":
            params["sport"] = sport
        if match_team:
            params["match_team"] = match_team
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(
                f"{FARM_URL}/bet365/list-boosts/{session_id}",
                headers=_headers(),
                params=params,
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm scan-boosts failed: HTTP {resp.status_code}: {resp.text[:200]}"}
        return resp.json()
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout — boost scan took too long"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Scan boosts error: {e}"}


async def bet365_megaboost_all(sport: str, match_team: str, stake: float = 20, boost_index: int = 0,
                                accounts: list[str] | None = None) -> dict:
    """Place megaboost across all configured bet365 accounts via Token Farm.

    This fires all accounts in parallel on the token farm.
    Timeout is long because 5 parallel browser sessions take time.
    """
    try:
        payload = {
            "sport": sport,
            "match_team": match_team,
            "stake": stake,
            "boost_index": boost_index,
        }
        if accounts:
            payload["accounts"] = accounts
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{FARM_URL}/bet365/megaboost-all",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm megaboost-all failed: HTTP {resp.status_code}: {resp.text[:200]}"}
        return resp.json()
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout — megaboost-all took too long (>5min)"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Megaboost-all error: {e}"}


# ─── Health ──────────────────────────────────────────────────────────────────

async def health() -> dict:
    """Check token farm health and status of managed sessions."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{FARM_URL}/health", headers=_headers())
        if resp.status_code != 200:
            return {"status": "error", "detail": f"HTTP {resp.status_code}"}
        return resp.json()
    except httpx.ConnectError:
        return {"status": "unreachable", "detail": "Token farm unreachable"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

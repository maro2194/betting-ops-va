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

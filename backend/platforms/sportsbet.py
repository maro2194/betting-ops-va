"""
Sportsbet platform client.
Handles auth (OAuth2 password grant via tls_client), racing data, and bet placement.

Login flow:
  1. Try refresh_token (fast, no browser) → POST /apigw/ciam/token
  2. If no refresh_token, requires browser-based login (Chrome + patchright + Kasada bypass)
     — handled externally, tokens passed in via brand_config

API calls use tls_client with Chrome TLS fingerprint to avoid detection.
"""
import base64
import json
import logging
import secrets
import time
import uuid
from fractions import Fraction
from typing import Optional

from curl_cffi.requests import AsyncSession

from .base import PlatformClient

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sportsbet.com.au"
CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

SPORTSBET_BRANDS = {
    "sportsbet": {
        "base_url": BASE_URL,
    },
}


def _decode_jwt(jwt_token: str) -> dict:
    """Decode JWT payload without verification."""
    parts = jwt_token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")
    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def _decimal_to_fraction(decimal_odds: float) -> tuple[int, int]:
    """Convert decimal odds to Sportsbet's priceNum/priceDen format."""
    frac = Fraction(decimal_odds - 1).limit_denominator(100)
    return frac.numerator, frac.denominator


def _std_headers(access_token: str, customer_id: str) -> dict:
    """Standard authenticated API headers."""
    return {
        "accesstoken": access_token,
        "apptoken": "cxp-desktop-web",
        "channel": "cxp",
        "customer-id": customer_id,
        "content-type": "application/json",
        "accept": "application/json",
        "accept-language": "en-AU,en;q=0.9",
        "origin": BASE_URL,
        "referer": f"{BASE_URL}/",
        "user-agent": CHROME_UA,
    }


def _bet_headers(access_token: str, customer_id: str) -> dict:
    """Headers for bet placement (includes transaction ID)."""
    h = _std_headers(access_token, customer_id)
    h["transuniqueid"] = secrets.token_hex(15)[:29]
    h["x-api-version"] = "3.0.0"
    h["x-request-id"] = uuid.uuid4().hex
    return h


def _public_headers() -> dict:
    """Public (unauthenticated) headers for racing data."""
    return {
        "accept": "application/json",
        "accept-language": "en-AU,en;q=0.9",
        "origin": BASE_URL,
        "referer": f"{BASE_URL}/",
        "user-agent": CHROME_UA,
    }


class SportsbetClient(PlatformClient):
    """Sportsbet platform client for multi-bookie framework."""

    # ── Login ─────────────────────────────────────────────────────

    async def login(self, email: str, password: str, proxy_url: str, brand_config: dict) -> dict:
        """Login to Sportsbet.

        Tries refresh_token first (from brand_config) — fast, no browser.
        Falls back to Chrome-based login if no refresh_token available.

        brand_config can include:
          - refresh_token: str — for token refresh without browser
          - access_token: str — pre-existing JWT (skip login entirely)
        """
        base = brand_config.get("base_url", BASE_URL)

        # 1. If we already have an access_token, validate it
        existing_token = brand_config.get("access_token")
        if existing_token:
            try:
                claims = _decode_jwt(existing_token)
                exp = claims.get("exp", 0)
                if time.time() < exp - 60:  # Still valid with 60s buffer
                    return self._build_session(existing_token, claims, brand_config.get("refresh_token"))
            except Exception:
                pass

        # 2. Try refresh_token (no browser needed)
        refresh_token = brand_config.get("refresh_token")
        if refresh_token:
            session = await self._try_refresh(base, refresh_token, proxy_url)
            if session:
                return session

        # 3. Browser-based login (Chrome + patchright + Kasada bypass)
        session = await self._browser_login(email, password, proxy_url, base)
        if session:
            return session

        return {"success": False, "error": "All login methods failed"}

    async def _try_refresh(self, base: str, refresh_token: str, proxy_url: str) -> dict | None:
        """Refresh JWT via API (no browser). Returns session dict or None."""
        try:
            url = f"{base}/apigw/ciam/token"
            headers = {
                "content-type": "application/x-www-form-urlencoded",
                "accept": "application/json",
                "accept-language": "en-AU,en;q=0.9",
                "origin": base,
                "referer": f"{base}/",
                "user-agent": CHROME_UA,
            }
            body = f"grant_type=refresh_token&refresh_token={refresh_token}"

            async with AsyncSession(
                impersonate="chrome131",
                proxy=proxy_url if proxy_url else None,
            ) as s:
                resp = await s.post(url, headers=headers, data=body, timeout=15)

            if resp.status_code != 200:
                logger.warning(f"Sportsbet refresh failed: HTTP {resp.status_code}")
                return None

            data = resp.json()
            if "access_token" not in data:
                return None

            new_refresh = data.get("refresh_token", refresh_token)
            claims = _decode_jwt(data["access_token"])
            session = self._build_session(data["access_token"], claims, new_refresh)
            logger.info(f"Sportsbet refresh OK for account {session.get('account_number')}")
            return session

        except Exception as e:
            logger.warning(f"Sportsbet refresh error: {e}")
            return None

    async def _browser_login(self, email: str, password: str, proxy_url: str, base: str) -> dict | None:
        """Browser-based login using patchright (Chrome + Kasada bypass).

        This spawns a real Chrome process, navigates to sportsbet.com.au,
        fills the login form, and captures the JWT from /ciam/token response.
        """
        try:
            from patchright.async_api import async_playwright
        except ImportError:
            logger.error("patchright not installed — cannot do browser login")
            return {"success": False, "error": "patchright not installed on server"}

        captured_token = None

        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(
                channel="chrome",
                headless=True,
                args=[
                    f"--proxy-server={proxy_url}" if proxy_url else "",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await browser.new_context(
                user_agent=CHROME_UA,
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()

            # Intercept /ciam/token response
            async def handle_response(response):
                nonlocal captured_token
                if "/ciam/token" in response.url and response.status == 200:
                    try:
                        data = await response.json()
                        if "access_token" in data:
                            captured_token = data
                    except Exception:
                        pass

            page.on("response", handle_response)

            # Navigate and wait for Kasada
            await page.goto(f"{base}/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(8000)

            # Click login button
            login_btn = page.locator('[data-automation-id="header-login-touchable"]')
            await login_btn.click(timeout=10000)
            await page.wait_for_timeout(1000)

            # Fill credentials
            await page.locator('[data-automation-id="login-username"]').fill(email)
            await page.wait_for_timeout(500)
            await page.locator('[data-automation-id="login-password"]').fill(password)
            await page.wait_for_timeout(500)
            await page.locator('button[type="submit"]').click()

            # Wait for token capture
            for _ in range(30):
                if captured_token:
                    break
                await page.wait_for_timeout(500)

            await browser.close()
            await pw.stop()

            if not captured_token:
                return {"success": False, "error": "Login failed — no token captured"}

            refresh = captured_token.get("refresh_token")
            claims = _decode_jwt(captured_token["access_token"])
            session = self._build_session(captured_token["access_token"], claims, refresh)
            logger.info(f"Sportsbet browser login OK for {email}")
            return session

        except Exception as e:
            logger.error(f"Sportsbet browser login error: {e}")
            return {"success": False, "error": f"Browser login failed: {e}"}

    def _build_session(self, access_token: str, claims: dict, refresh_token: str | None = None) -> dict:
        """Build a session dict from JWT and claims."""
        return {
            "success": True,
            "platform": "sportsbet",
            "brand": "sportsbet",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "customer_id": str(claims.get("custId", "")),
            "account_number": str(claims.get("accountNo", "")),
            "username": claims.get("username") or claims.get("email", ""),
            "expires_at": claims.get("exp", time.time() + 3600),
        }

    # ── Session validation ────────────────────────────────────────

    def is_session_valid(self, session: dict) -> bool:
        """Check if JWT is still valid (with 2 min buffer)."""
        expires_at = session.get("expires_at", 0)
        return time.time() < (expires_at - 120)

    # ── Racing data ───────────────────────────────────────────────

    async def find_race(self, session: dict, track: str, race_number: int) -> dict | None:
        """Find a race by track name and race number."""
        proxy = session.get("proxy_url")

        async with AsyncSession(
            impersonate="chrome131",
            proxy=proxy if proxy else None,
        ) as s:
            url = f"{BASE_URL}/apigw/sportsbook-racing/Sportsbook/Racing/AllRacing/sportsbet"
            resp = await s.get(url, headers=_public_headers(), timeout=15)

        if resp.status_code != 200:
            logger.warning(f"Sportsbet get_races failed: HTTP {resp.status_code}")
            return None

        data = resp.json()
        track_lower = track.lower().strip()

        for date_block in data.get("dates", []):
            for section in date_block.get("sections", []):
                for meeting in section.get("meetings", []):
                    meeting_name = meeting.get("name", "").lower().strip()
                    if meeting_name != track_lower:
                        continue
                    for event in meeting.get("events", []):
                        if event.get("raceNumber") == race_number:
                            return {
                                "event_id": event.get("id") or event.get("eventId"),
                                "track": meeting.get("name", track),
                                "race_number": race_number,
                                "race_type": meeting.get("type", "horse"),
                                "status": event.get("bettingStatus", ""),
                            }
        return None

    async def get_runners(self, session: dict, race_info: dict) -> list[dict]:
        """Get runners with odds for a race."""
        event_id = race_info.get("event_id")
        if not event_id:
            return []

        proxy = session.get("proxy_url")
        url = f"{BASE_URL}/apigw/sportsbook-racing/Sportsbook/Racing/Events/{event_id}/Racecard"

        async with AsyncSession(
            impersonate="chrome131",
            proxy=proxy if proxy else None,
        ) as s:
            resp = await s.get(url, headers=_public_headers(), timeout=15)

        if resp.status_code != 200:
            logger.warning(f"Sportsbet racecard failed: HTTP {resp.status_code}")
            return []

        data = resp.json()

        # Find Win or Place market
        win_market = None
        for market in data.get("markets", []):
            if market.get("name") in ("Win or Place", "Win"):
                win_market = market
                break
        if not win_market and data.get("markets"):
            win_market = data["markets"][0]
        if not win_market:
            return []

        runners = []
        for sel in win_market.get("selections", []):
            if sel.get("isOut", False):
                continue

            decimal_price = None
            price_num = None
            price_den = None
            place_price = None
            for price in sel.get("prices", []):
                if price.get("priceCode") == "L":
                    decimal_price = price.get("winPrice")
                    price_num = price.get("winPriceNum")
                    price_den = price.get("winPriceDen")
                    place_price = price.get("placePrice")
                    break

            if decimal_price and (price_num is None or price_den is None):
                price_num, price_den = _decimal_to_fraction(decimal_price)

            runners.append({
                "id": sel.get("id", 0),
                "name": sel.get("name", "?"),
                "number": sel.get("runnerNumber", 0),
                "jockey": sel.get("jockey", ""),
                "odds": decimal_price,
                "price_num": price_num,
                "price_den": price_den,
                "place_odds": place_price,
                "status": "active",
            })

        runners.sort(key=lambda r: r["number"])
        return runners

    # ── Bet placement ─────────────────────────────────────────────

    async def place_bet(self, session: dict, race_info: dict, runner: dict,
                        stake: float, stake_type: str, brand_config: dict) -> dict:
        """Place a single bet on Sportsbet."""
        access_token = session.get("access_token")
        customer_id = session.get("customer_id")
        proxy = session.get("proxy_url")

        if not access_token or not customer_id:
            return {"success": False, "error": "No valid session"}

        outcome_id = runner.get("id") or runner.get("outcome_id")
        price_num = runner.get("price_num")
        price_den = runner.get("price_den")

        if not outcome_id or price_num is None or price_den is None:
            return {"success": False, "error": f"Missing runner data: id={outcome_id} num={price_num} den={price_den}"}

        # Map stake_type to legType
        leg_type = "W" if stake_type.lower() in ("win", "w") else "P"

        payload = {
            "betItems": [{
                "betNo": 0,
                "stakePerLine": stake,
                "numLines": 1,
                "betType": "SGL",
                "legs": [{
                    "legNo": 0,
                    "legSort": "--",
                    "legType": leg_type,
                    "legDesc": "",
                    "isPYPSingle": False,
                    "parts": [{
                        "outcome": outcome_id,
                        "partNo": 1,
                        "priceType": "L",
                        "partDesc": "RACECARD",
                        "priceNum": price_num,
                        "priceDen": price_den,
                    }],
                }],
                "legType": leg_type,
            }],
            "checkBalance": True,
            "errorDetail": "ALL",
            "firstBet": True,
            "fullDetails": True,
            "pendingBetCount": True,
            "returnBalance": True,
            "returnCashoutAvailable": True,
        }

        url = f"{BASE_URL}/apigw/acs/bets"
        headers = _bet_headers(access_token, customer_id)

        async with AsyncSession(
            impersonate="chrome131",
            proxy=proxy if proxy else None,
        ) as s:
            resp = await s.post(url, headers=headers, json=payload, timeout=15)

        if resp.status_code >= 400:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}

        data = resp.json()

        # Check for limit/suspension
        re_code = data.get("reCode")
        re_reason = data.get("reReason")
        if re_code or re_reason:
            return {
                "success": False,
                "error": f"Rejected: {re_reason or re_code}",
                "limited": True,
                "raw": data,
            }

        # Check for bet failures
        failures = data.get("betFailures", [])
        if failures:
            reason = failures[0].get("betFailureReason", "Unknown failure")
            return {"success": False, "error": reason, "raw": data}

        # Success
        placements = data.get("betPlacements", [])
        if placements:
            p = placements[0]
            balance = data.get("accountBalance", {}).get("balance")
            return {
                "success": True,
                "bet_id": p.get("betId") or p.get("receipt"),
                "receipt": p.get("receipt"),
                "stake": p.get("totalStake"),
                "potential_win": p.get("betPotentialWin"),
                "balance": balance,
                "raw": data,
            }

        return {"success": False, "error": "No placement data in response", "raw": data}

    # ── Balance ───────────────────────────────────────────────────

    async def get_balance(self, session: dict) -> float:
        """Get account cash balance."""
        access_token = session.get("access_token")
        customer_id = session.get("customer_id")
        proxy = session.get("proxy_url")

        url = f"{BASE_URL}/apigw/accounts/balance?pendingbetcount=true&freebetcount=true&jointAccountBalance=true"

        async with AsyncSession(
            impersonate="chrome131",
            proxy=proxy if proxy else None,
        ) as s:
            resp = await s.get(url, headers=_std_headers(access_token, customer_id), timeout=15)

        if resp.status_code >= 400:
            raise RuntimeError(f"Balance check failed: HTTP {resp.status_code}")

        data = resp.json()
        # Balance can be in different formats
        if isinstance(data, dict):
            return float(data.get("balance", data.get("accountBalance", {}).get("balance", 0)))
        return 0.0

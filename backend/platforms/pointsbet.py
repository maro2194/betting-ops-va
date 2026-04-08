"""
PointsBet platform client for multi-bookie allocation framework.

Auth: Browser-based via token farm (Xvfb + patchright, Kasada bypass).
      Tries token farm first (POST /auth/pointsbet/login), falls back to
      local patchright browser if farm endpoint not yet deployed.

API: curl_cffi AsyncSession with chrome131 impersonation.
     No Kasada on the API tier — only on the login/auth flow.
     Public PointsBet API endpoints work without AU geo restriction.

Session dict keys:
  access_token, refresh_token, customer_id (crn), expires_at,
  email, password, proxy_url, platform, brand
"""
import json
import logging
import time
import uuid
from typing import Optional

from curl_cffi.requests import AsyncSession

from .base import PlatformClient

logger = logging.getLogger(__name__)

BASE_URL = "https://api.au.pointsbet.com"
CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# Racing type ID -> human label mapping (used for token matching)
_RACING_TYPE_LABEL = {
    1: "Horses",
    2: "Harness",
    4: "Greys",
}


def _bearer_headers(access_token: str) -> dict:
    """Standard authenticated headers for PointsBet API."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": CHROME_UA,
        "Origin": "https://pointsbet.com.au",
        "Referer": "https://pointsbet.com.au/",
    }


def _public_headers() -> dict:
    """Unauthenticated headers for public PointsBet racing endpoints."""
    return {
        "Accept": "application/json",
        "User-Agent": CHROME_UA,
        "Origin": "https://pointsbet.com.au",
        "Referer": "https://pointsbet.com.au/",
    }


class PointsbetClient(PlatformClient):
    """PointsBet platform client for multi-bookie allocation framework."""

    # ── Login ─────────────────────────────────────────────────────

    async def login(self, email: str, password: str, proxy_url: str, brand_config: dict) -> dict:
        """Login to PointsBet.

        Strategy:
          1. Try token farm /auth/pointsbet/login (Xvfb+patchright on mini PC)
          2. Fall back to local patchright browser login if farm unavailable
        """
        # 1. Try token farm
        try:
            import token_farm_client
            farm_result = await token_farm_client.pointsbet_login(email, password, proxy_url=proxy_url)
            if farm_result.get("success"):
                return self._build_session(farm_result, email, password, proxy_url)
            logger.warning(f"PointsBet token farm login failed: {farm_result.get('error')}")
        except AttributeError:
            # pointsbet_login not yet deployed on farm — expected, fall through
            logger.info("PointsBet farm endpoint not deployed yet — trying local browser")
        except Exception as e:
            logger.warning(f"Token farm unavailable for PointsBet: {e}")

        # 2. Local browser login via patchright
        return await self._browser_login(email, password, proxy_url)

    def _build_session(self, data: dict, email: str, password: str, proxy_url: str) -> dict:
        """Normalise token farm response into the standard session dict."""
        expires_at = data.get("expires_at")
        if not expires_at:
            expires_at = time.time() + 28800  # 8-hour default

        return {
            "success": True,
            "platform": "pointsbet",
            "brand": "pointsbet",
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "customer_id": data.get("customer_id") or data.get("crn", ""),
            "email": email,
            "password": password,
            "proxy_url": proxy_url,
            "expires_at": expires_at,
        }

    async def _browser_login(self, email: str, password: str, proxy_url: str) -> dict:
        """Local patchright browser login for PointsBet (Kasada bypass).

        Navigates to pointsbet.com.au, intercepts the JWT token response,
        and returns a fully-formed session dict.
        """
        try:
            from patchright.async_api import async_playwright
        except ImportError:
            logger.error("patchright not installed — cannot do local PointsBet browser login")
            return {"success": False, "error": "patchright not installed; token farm endpoint not deployed either"}

        captured: Optional[dict] = None

        try:
            import re as _re
            pw = await async_playwright().start()

            proxy_config = None
            if proxy_url:
                m = _re.match(r'https?://([^:]+):([^@]+)@([^:]+):(\d+)', proxy_url)
                if m:
                    proxy_config = {
                        "server": f"http://{m.group(3)}:{m.group(4)}",
                        "username": m.group(1),
                        "password": m.group(2),
                    }
                else:
                    proxy_config = {"server": proxy_url}

            browser = await pw.chromium.launch(
                channel="chrome",
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=CHROME_UA,
                viewport={"width": 1920, "height": 1080},
                proxy=proxy_config,
            )
            page = await context.new_page()

            # Intercept PointsBet login API responses to capture JWT
            async def handle_response(response):
                nonlocal captured
                if captured:
                    return
                url = response.url
                # PointsBet login endpoints return access_token in JSON
                if ("pointsbet.com" in url and response.status == 200 and
                        any(k in url for k in ("/auth/", "/login", "/token"))):
                    try:
                        data = await response.json()
                        if "accessToken" in data or "access_token" in data:
                            captured = data
                    except Exception:
                        pass

            page.on("response", handle_response)

            await page.goto("https://pointsbet.com.au/login", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # Fill login form
            await page.fill('input[type="email"], input[name="email"], input[placeholder*="email" i]', email)
            await page.fill('input[type="password"], input[name="password"]', password)
            await page.click('button[type="submit"], button:has-text("Log in"), button:has-text("Login")')

            # Wait for auth interception (up to 20 seconds)
            deadline = time.time() + 20
            while not captured and time.time() < deadline:
                await page.wait_for_timeout(500)

            await browser.close()
            await pw.stop()

            if not captured:
                return {"success": False, "error": "PointsBet browser login timed out — no token captured"}

            # Normalise field names (PB uses camelCase in browser API)
            access_token = captured.get("accessToken") or captured.get("access_token", "")
            refresh_token = captured.get("refreshToken") or captured.get("refresh_token", "")
            crn = captured.get("crn") or captured.get("customerId") or captured.get("customer_id", "")
            expires_in = captured.get("expiresIn") or captured.get("expires_in", 28800)

            if not access_token:
                return {"success": False, "error": "PointsBet browser login: no access token in captured response"}

            return {
                "success": True,
                "platform": "pointsbet",
                "brand": "pointsbet",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "customer_id": str(crn),
                "email": email,
                "password": password,
                "proxy_url": proxy_url,
                "expires_at": time.time() + int(expires_in),
            }

        except Exception as e:
            logger.error(f"PointsBet browser login error: {e}")
            return {"success": False, "error": f"PointsBet browser login failed: {e}"}

    def is_session_valid(self, session: dict) -> bool:
        """Session valid if not within 5 minutes of expiry."""
        return time.time() < (session.get("expires_at", 0) - 300)

    # ── Racing ────────────────────────────────────────────────────

    async def find_race(self, session: dict, track: str, race_number: int) -> dict | None:
        """Find a PointsBet race by track name and race number.

        Uses the nextup endpoint to get upcoming races for all racing types,
        matching venue name against the requested track.
        """
        # PointsBet racing type IDs: 1=thoroughbred, 2=harness, 4=greyhound
        racing_types = [1, 2, 4]
        track_lower = track.lower().strip()

        async with AsyncSession(impersonate="chrome131") as s:
            for racing_type in racing_types:
                url = (
                    f"{BASE_URL}/api/racing/v4/races/nextup"
                    f"?racingType={racing_type}&region=3&raceCount=20&runnerCount=0"
                )
                try:
                    resp = await s.get(url, headers=_public_headers(), timeout=15)
                    if resp.status_code != 200:
                        logger.warning(f"PointsBet nextup HTTP {resp.status_code} for type {racing_type}")
                        continue

                    data = resp.json()
                    races = data if isinstance(data, list) else data.get("races", data.get("data", []))
                    if not isinstance(races, list):
                        logger.warning(f"PointsBet nextup: unexpected shape for type {racing_type}: {type(races)}")
                        continue

                    for race in races:
                        venue = (race.get("venueName") or race.get("venue") or "").lower().strip()
                        rnum = race.get("raceNumber") or race.get("number") or 0

                        # Flexible match: exact, substring, or reverse substring
                        venue_match = (
                            track_lower == venue
                            or track_lower in venue
                            or venue in track_lower
                        )
                        if venue_match and int(rnum) == race_number:
                            race_id = race.get("raceId") or race.get("id")
                            logger.info(
                                f"PointsBet find_race: matched '{venue}' R{race_number} "
                                f"-> raceId={race_id} type={racing_type}"
                            )
                            return {
                                "race_id": race_id,
                                "track": race.get("venueName", track),
                                "race_number": race_number,
                                "racing_type": racing_type,
                                "racing_type_label": _RACING_TYPE_LABEL.get(racing_type, "Horses"),
                                "start_time": race.get("startTime") or race.get("scheduledStart", ""),
                                "status": race.get("raceStatus") or race.get("status", ""),
                                "_raw": race,
                            }

                except Exception as e:
                    logger.warning(f"PointsBet nextup error (type {racing_type}): {e}")
                    continue

        # Log available venues for debugging
        logger.warning(f"PointsBet find_race: track '{track}' R{race_number} not found in upcoming races")
        return None

    async def get_runners(self, session: dict, race_info: dict) -> list[dict]:
        """Get runners with win/place prices for a PointsBet race.

        Calls GET /api/racing/v3/races?raceIds={raceId} and parses
        runners[] + markets[] (FixedWin / FixedPlc).
        """
        race_id = race_info.get("race_id")
        if not race_id:
            logger.error("PointsBet get_runners: no race_id in race_info")
            return []

        url = f"{BASE_URL}/api/racing/v3/races?raceIds={race_id}"
        try:
            async with AsyncSession(impersonate="chrome131") as s:
                resp = await s.get(url, headers=_public_headers(), timeout=15)

            if resp.status_code != 200:
                logger.warning(f"PointsBet get_runners: HTTP {resp.status_code} for raceId={race_id}")
                return []

            data = resp.json()
            # Response is either a list of race objects or a wrapper
            races = data if isinstance(data, list) else data.get("races", [data] if isinstance(data, dict) else [])
            if not races:
                logger.warning(f"PointsBet get_runners: empty races list for raceId={race_id}")
                return []

            race_data = races[0]
            raw_runners = race_data.get("runners", [])
            markets = race_data.get("markets", [])

            # Build price lookup: runnerId -> {win_price, place_price, legacyMarketId}
            prices: dict[str, dict] = {}
            for market in markets:
                mtype = market.get("marketType", "")
                is_win = mtype == "FixedWin"
                is_place = mtype == "FixedPlc"
                if not (is_win or is_place):
                    continue
                legacy_id = market.get("legacyMarketId") or market.get("id")
                for sel in market.get("selections", []):
                    rid = str(sel.get("runnerId", ""))
                    if not rid:
                        continue
                    if rid not in prices:
                        prices[rid] = {"win_price": None, "place_price": None, "legacyMarketId": None}
                    p = sel.get("price") or sel.get("currentPrice") or 0
                    if is_win:
                        prices[rid]["win_price"] = float(p) if p else None
                        prices[rid]["legacyMarketId"] = legacy_id
                    elif is_place:
                        prices[rid]["place_price"] = float(p) if p else None

            runners = []
            for r in raw_runners:
                # Skip scratched runners
                if r.get("scratched") or r.get("isScratched"):
                    continue

                rid = str(r.get("runnerId", r.get("id", "")))
                # PointsBet optionId format: "RS:{legacyMarketId}/{runnerNumber}"
                runner_num = int(r.get("runnerNumber") or r.get("number") or 0)
                p_data = prices.get(rid, {})
                legacy_market_id = p_data.get("legacyMarketId")
                option_id = f"RS:{legacy_market_id}/{runner_num}" if legacy_market_id else rid

                runners.append({
                    "id": option_id,
                    "number": runner_num,
                    "name": r.get("runnerName") or r.get("name") or "",
                    "barrier": r.get("barrierNumber") or r.get("barrier") or 0,
                    "jockey": r.get("jockeyName") or r.get("jockey") or "",
                    "trainer": r.get("trainerName") or r.get("trainer") or "",
                    "win_price": p_data.get("win_price"),
                    "place_price": p_data.get("place_price"),
                    "scratched": False,
                    # Keep raw ID for internal bet placement
                    "_runner_id": rid,
                    "_legacy_market_id": legacy_market_id,
                })

            logger.info(f"PointsBet get_runners: {len(runners)} active runners for raceId={race_id}")
            return runners

        except Exception as e:
            logger.error(f"PointsBet get_runners error: {e}")
            return []

    async def place_bet(self, session: dict, race_info: dict, runner: dict,
                        stake: float, stake_type: str, brand_config: dict) -> dict:
        """Place a PointsBet racing bet.

        stake_type:
          "win" / "cash" / "w" → FixedWin
          "place" / "p"        → FixedPlace
          "promo" / "token"    → FixedWin with best matching PAYBACK token attached

        Stake is in dollars — converted to cents for the API.
        """
        access_token = session.get("access_token")
        if not access_token:
            return {"success": False, "error": "No PointsBet access token in session"}

        stake_type_lower = stake_type.lower() if stake_type else "cash"
        is_place = stake_type_lower in ("place", "p")
        is_promo = stake_type_lower in ("promo", "token")

        # Determine subBetType
        sub_bet_type = "RacingFixedPlace" if is_place else "RacingFixedWin"

        # Resolve price
        if is_place:
            price = runner.get("place_price") or runner.get("win_price") or 0.0
        else:
            price = runner.get("win_price") or 0.0

        if not price:
            return {"success": False, "error": "No price available for runner"}

        option_id = runner.get("id", "")
        if not option_id:
            return {"success": False, "error": "No optionId (runner.id) available"}

        stake_cents = int(round(stake * 100))

        # Build base payload
        payload: dict = {
            "acceptPriceChangeStatus": "AcceptAll",
            "comboType": "single",
            "placementRequestId": str(uuid.uuid4()),
            "stakeInCents": stake_cents,
            "channelId": "web",
            "subBets": [{
                "subBetType": sub_bet_type,
                "source": "TBS",
                "price": round(float(price), 2),
                "optionId": option_id,
            }],
        }

        # Attach promo token if requested
        if is_promo:
            token = await self._find_promo_token(access_token, race_info.get("racing_type", 1))
            if token:
                payload["promo"] = {
                    "promoCategory": "Token",
                    "promoId": token["tokenId"],
                    "promoType": "PAYBACK",
                }
                logger.info(f"PointsBet place_bet: attaching promo token {token['tokenId']}")
            else:
                logger.warning("PointsBet place_bet: no PAYBACK token found, placing as cash bet")

        url = f"{BASE_URL}/api/betting/v1/bets"
        try:
            async with AsyncSession(impersonate="chrome131") as s:
                resp = await s.post(
                    url,
                    headers=_bearer_headers(access_token),
                    json=payload,
                    timeout=15,
                )

            logger.info(f"PointsBet place_bet: HTTP {resp.status_code}")

            if resp.status_code in (200, 201):
                data = resp.json()
                # Extract bet ID from response — PB returns betId or id at top level
                bet_id = (
                    data.get("betId")
                    or data.get("id")
                    or data.get("placementRequestId", "")
                )
                # Check for errors in response body
                errors = data.get("errors") or []
                if isinstance(errors, list) and errors:
                    msg = "; ".join(
                        e.get("message") or e.get("detail") or str(e)
                        for e in errors
                    )
                    return {"success": False, "error": msg, "raw": data}

                return {
                    "success": True,
                    "bet_id": str(bet_id),
                    "receipt": str(bet_id),
                    "stake": stake,
                    "price": price,
                    "option_id": option_id,
                    "raw": data,
                }
            elif resp.status_code == 400:
                try:
                    err_data = resp.json()
                    msg = (
                        err_data.get("message")
                        or err_data.get("error")
                        or err_data.get("detail")
                        or resp.text[:300]
                    )
                except Exception:
                    msg = resp.text[:300]
                return {"success": False, "error": f"PointsBet bet rejected: {msg}"}
            elif resp.status_code == 401:
                return {"success": False, "error": "PointsBet session expired — re-login required"}
            else:
                return {"success": False, "error": f"PointsBet place_bet HTTP {resp.status_code}: {resp.text[:300]}"}

        except Exception as e:
            logger.error(f"PointsBet place_bet error: {e}")
            return {"success": False, "error": f"PointsBet placement failed: {e}"}

    # ── Balance ───────────────────────────────────────────────────

    async def get_balance(self, session: dict) -> float:
        """Get PointsBet cash balance from account summary."""
        access_token = session.get("access_token")
        if not access_token:
            return 0.0
        try:
            url = f"{BASE_URL}/api/v2/accounts/user/summary"
            async with AsyncSession(impersonate="chrome131") as s:
                resp = await s.get(url, headers=_bearer_headers(access_token), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                # Navigate: balances.balance (dollars, float)
                balances = data.get("balances") or data.get("balance") or {}
                if isinstance(balances, dict):
                    raw = balances.get("balance") or balances.get("cashBalance") or 0
                else:
                    raw = balances  # scalar fallback
                return float(raw) if raw else 0.0
            else:
                logger.warning(f"PointsBet get_balance: HTTP {resp.status_code}")
                return 0.0
        except Exception as e:
            logger.error(f"PointsBet get_balance error: {e}")
            return 0.0

    async def get_balances(self, session: dict) -> dict:
        """Get PointsBet cash and bonus balances."""
        cash = await self.get_balance(session)
        return {"cash": cash, "bonus": 0.0}

    # ── Promos ────────────────────────────────────────────────────

    async def get_user_promos(self, session: dict) -> dict:
        """Fetch PointsBet promo tokens and odds boosts.

        Sources:
          - GET /api/promo-tokens/v2/tokens → PAYBACK tokens
          - GET /api/v2/oddsboost/getremainingboost → odds boost count

        Returns unified promo structure matching other platform clients.
        """
        access_token = session.get("access_token")
        empty = {
            "boost_tokens": 0,
            "bonus_back_tokens": 0,
            "deposit_match_tokens": 0,
            "promos": [],
            "redeemed": [],
        }
        if not access_token:
            return empty

        boost_tokens = 0
        bonus_back_tokens = 0
        promos = []

        async with AsyncSession(impersonate="chrome131") as s:
            # 1. Promo / PAYBACK tokens
            try:
                resp = await s.get(
                    f"{BASE_URL}/api/promo-tokens/v2/tokens",
                    headers=_bearer_headers(access_token),
                    timeout=15,
                )
                if resp.status_code == 200:
                    tokens = resp.json()
                    if not isinstance(tokens, list):
                        tokens = tokens.get("tokens") or tokens.get("data") or []
                    for t in tokens:
                        token_id = t.get("tokenId") or t.get("id") or ""
                        promo_type = (t.get("promoType") or t.get("type") or "").upper()
                        category = t.get("promoCategory") or t.get("category") or ""
                        desc = t.get("description") or t.get("name") or promo_type
                        expiry = t.get("expiryDate") or t.get("expiry") or ""
                        amount = float(t.get("amount") or t.get("value") or 0)

                        if promo_type == "PAYBACK" or "bonus" in desc.lower() or "bonus" in category.lower():
                            bonus_back_tokens += 1
                            cat = "BonusBack"
                        else:
                            boost_tokens += 1
                            cat = "Boost"

                        promos.append({
                            "promo_id": token_id,
                            "type": cat,
                            "description": desc,
                            "status": "Active",
                            "tokens": 1,
                            "tokens_remaining": 1,
                            "expiry_date": expiry,
                            "boost_data": {"boost_type": promo_type, "boost_percentage": 0} if cat == "Boost" else None,
                            "bonus_back_data": {
                                "details": f"PAYBACK token",
                                "criteria": promo_type,
                                "event_config_type": "Global",
                                "global_config": [],
                                "event_config": [],
                                "max_deposit": int(amount * 100) if amount else 0,
                                "field_size": 0,
                            } if cat == "BonusBack" else None,
                            "deposit_match_data": None,
                            # Keep raw token for place_bet promo attachment
                            "_raw": t,
                        })
                else:
                    logger.warning(f"PointsBet promo-tokens HTTP {resp.status_code}")
            except Exception as e:
                logger.warning(f"PointsBet promo-tokens fetch failed: {e}")

            # 2. Odds boosts
            try:
                resp = await s.get(
                    f"{BASE_URL}/api/v2/oddsboost/getremainingboost",
                    headers=_bearer_headers(access_token),
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # PB returns {"remaining": N} or {"boostsRemaining": N}
                    remaining = (
                        data.get("remaining")
                        or data.get("boostsRemaining")
                        or data.get("count")
                        or 0
                    )
                    remaining = int(remaining)
                    if remaining > 0:
                        boost_tokens += remaining
                        promos.append({
                            "promo_id": "odds_boost",
                            "type": "Boost",
                            "description": f"Odds Boost x{remaining}",
                            "status": "Active",
                            "tokens": remaining,
                            "tokens_remaining": remaining,
                            "expiry_date": "",
                            "boost_data": {"boost_type": "OddsBoost", "boost_percentage": 0},
                            "bonus_back_data": None,
                            "deposit_match_data": None,
                            "_raw": data,
                        })
            except Exception as e:
                logger.warning(f"PointsBet odds boost fetch failed: {e}")

        return {
            "boost_tokens": boost_tokens,
            "bonus_back_tokens": bonus_back_tokens,
            "deposit_match_tokens": 0,
            "promos": promos,
            "redeemed": [],
        }

    # ── Internal helpers ──────────────────────────────────────────

    async def _find_promo_token(self, access_token: str, racing_type: int) -> Optional[dict]:
        """Fetch PAYBACK tokens and return the best match for the given racing type.

        Tries to find a token whose description/raceType matches
        "Horses" (type 1/2) or "Greys" (type 4). Returns first match or any
        PAYBACK token if no sport-specific one is available.
        """
        try:
            url = f"{BASE_URL}/api/promo-tokens/v2/tokens"
            async with AsyncSession(impersonate="chrome131") as s:
                resp = await s.get(url, headers=_bearer_headers(access_token), timeout=15)

            if resp.status_code != 200:
                return None

            tokens = resp.json()
            if not isinstance(tokens, list):
                tokens = tokens.get("tokens") or tokens.get("data") or []

            sport_label = _RACING_TYPE_LABEL.get(racing_type, "Horses").lower()
            payback_tokens = [
                t for t in tokens
                if (t.get("tokenType") or t.get("promoType") or t.get("type") or "").upper() == "PAYBACK"
            ]
            if not payback_tokens:
                return None

            # Prefer sport-specific match
            for t in payback_tokens:
                desc = (t.get("description") or t.get("name") or "").lower()
                race_type_field = (t.get("raceType") or "").lower()
                if sport_label in desc or sport_label in race_type_field:
                    return t

            # Fall back to first available PAYBACK token
            return payback_tokens[0]

        except Exception as e:
            logger.warning(f"PointsBet _find_promo_token error: {e}")
            return None

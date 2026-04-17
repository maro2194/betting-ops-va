"""
Entain v2 platform client (Ladbrokes + Neds).

Both brands share identical APIs — only the base URL differs.
No anti-bot measures: curl_cffi with chrome131 impersonation works directly.
Auth is NOT required for reading odds/racing data (public API).

API endpoints:
  - POST {base}/v2/racing/next-races          → upcoming races
  - GET  {base}/v2/racing/meeting?date=...    → all meetings for a date
  - GET  {base}/rest/v1/racing/?method=racecard&id={race_id} → full race card

Price format: {odds: {numerator: N, denominator: D}} → decimal = 1.0 + N/D
"""
import logging
import time
from datetime import datetime, timezone

from curl_cffi.requests import AsyncSession

from .base import PlatformClient

logger = logging.getLogger(__name__)

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

ENTAIN_BRANDS = {
    "ladbrokes": {
        "api_base": "https://api.ladbrokes.com.au",
        "web_base": "https://www.ladbrokes.com.au",
    },
    "neds": {
        "api_base": "https://api.neds.com.au",
        "web_base": "https://www.neds.com.au",
    },
}

# Market names that represent the main Win market
_WIN_MARKET_NAMES = {"final field", "win fixed", "fixed win", "win"}


def _brand_headers(brand_config: dict) -> dict:
    """Standard browser headers for Entain public API."""
    web_base = brand_config.get("web_base", "https://www.ladbrokes.com.au")
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": CHROME_UA,
        "Origin": web_base,
        "Referer": f"{web_base}/",
    }


def _parse_price(price_entry: dict) -> float | None:
    """Parse an Entain price entry into decimal odds.

    Format: {odds: {numerator: N, denominator: D}} → 1.0 + N/D
    Returns None if suspended or invalid.
    """
    odds = price_entry.get("odds")
    if not odds or not isinstance(odds, dict):
        return None
    num = odds.get("numerator", 0)
    den = odds.get("denominator", 0)
    if not num or not den or num <= 0 or den <= 0:
        return None
    return round(1.0 + num / den, 2)


class EntainClient(PlatformClient):
    """Entain v2 platform client for Ladbrokes and Neds."""

    # ── Login ─────────────────────────────────────────────────────

    async def login(self, email: str, password: str, proxy_url: str, brand_config: dict) -> dict:
        """Login via OAuth2 Hydra + HyperSolutions Akamai bypass.

        Returns session with access_token for bet placement.
        Falls back to public-only mode if login fails (can still read odds).
        """
        brand = brand_config.get("brand", "ladbrokes")
        entain_config = ENTAIN_BRANDS.get(brand, ENTAIN_BRANDS["ladbrokes"])

        logger.info(f"Entain login: brand={brand}, email={email}")

        try:
            from entain_login import entain_browser_login
            result = await entain_browser_login(
                username=email,
                password=password,
                brand=brand,
                proxy_url=proxy_url or None,
            )

            if result.get("success") and result.get("access_token"):
                logger.info(f"Entain login SUCCESS for {email}@{brand}")
                return {
                    "success": True,
                    "platform": "ladbrokes",
                    "brand": brand,
                    "email": email,
                    "password": password,
                    "proxy_url": proxy_url,
                    "access_token": result["access_token"],
                    "cookies": result.get("cookies", {}),
                    "expires_at": time.time() + 3600,  # 1 hour
                    "brand_config": {**entain_config, "brand": brand},
                }
            else:
                error = result.get("error", "Unknown login error")
                logger.warning(f"Entain login failed for {email}@{brand}: {error}")
                # Fall back to public-only mode
                return {
                    "success": True,
                    "platform": "ladbrokes",
                    "brand": brand,
                    "email": email,
                    "password": password,
                    "proxy_url": proxy_url,
                    "expires_at": time.time() + 86400,
                    "brand_config": {**entain_config, "brand": brand},
                    "_public_only": True,
                    "_login_error": error,
                }
        except Exception as e:
            logger.error(f"Entain login exception for {email}@{brand}: {e}")
            return {
                "success": True,
                "platform": "ladbrokes",
                "brand": brand,
                "email": email,
                "password": password,
                "proxy_url": proxy_url,
                "expires_at": time.time() + 86400,
                "brand_config": {**entain_config, "brand": brand},
                "_public_only": True,
                "_login_error": str(e),
            }

    def is_session_valid(self, session: dict) -> bool:
        """Check if session has a valid (non-expired) access token."""
        expires = session.get("expires_at", 0)
        if expires and time.time() > expires:
            return False
        return True

    # ── Racing ────────────────────────────────────────────────────

    async def find_race(self, session: dict, track: str, race_number: int) -> dict | None:
        """Find a race by track name and number.

        Strategy:
          1. Check next-races endpoint (fast, covers upcoming races)
          2. Fall back to full meetings endpoint for today's date
        """
        brand_config = session.get("brand_config", ENTAIN_BRANDS["ladbrokes"])
        api_base = brand_config.get("api_base", ENTAIN_BRANDS["ladbrokes"]["api_base"])
        headers = _brand_headers(brand_config)
        track_lower = track.lower().strip()

        async with AsyncSession(impersonate="chrome131") as s:
            # 1. Try next-races
            result = await self._find_in_next_races(s, api_base, headers, track_lower, race_number)
            if result:
                return result

            # 2. Fall back to meetings endpoint
            result = await self._find_in_meetings(s, api_base, headers, track_lower, race_number)
            if result:
                return result

        logger.warning(f"Entain find_race: track '{track}' R{race_number} not found")
        return None

    async def _find_in_next_races(
        self, session: AsyncSession, api_base: str, headers: dict,
        track_lower: str, race_number: int,
    ) -> dict | None:
        """Search the next-races endpoint for a matching race."""
        url = f"{api_base}/v2/racing/next-races"
        try:
            resp = await session.post(url, headers=headers, json={"count": 20}, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"Entain next-races HTTP {resp.status_code}")
                return None

            data = resp.json()
            summaries = data.get("race_summaries", {})

            for race_id, race in summaries.items():
                venue = (race.get("venue_name") or "").lower().strip()
                rnum = race.get("race_number", 0)

                if self._venue_match(track_lower, venue) and int(rnum) == race_number:
                    logger.info(f"Entain find_race: matched '{venue}' R{race_number} in next-races -> {race_id}")
                    return {
                        "race_id": race_id,
                        "track": race.get("venue_name", ""),
                        "race_number": race_number,
                        "meeting_id": race.get("meeting_id", ""),
                        "venue_name": race.get("venue_name", ""),
                        "start_time": race.get("advertised_start", ""),
                        "category_id": race.get("category_id", ""),
                    }

        except Exception as e:
            logger.warning(f"Entain next-races error: {e}")

        return None

    async def _find_in_meetings(
        self, session: AsyncSession, api_base: str, headers: dict,
        track_lower: str, race_number: int,
    ) -> dict | None:
        """Search the meetings endpoint for a matching race (fallback)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        url = f"{api_base}/v2/racing/meeting?date={today}&timezone=Australia/Brisbane"
        try:
            resp = await session.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"Entain meetings HTTP {resp.status_code}")
                return None

            data = resp.json()
            meetings = data.get("meetings", {})
            races = data.get("races", {})
            venues = data.get("venues", {})

            # Build venue lookup: venue_id -> venue_name
            venue_names: dict[str, str] = {}
            for vid, v in venues.items():
                venue_names[vid] = (v.get("name") or "").lower().strip()

            # Build meeting -> venue lookup
            meeting_venue: dict[str, str] = {}
            for mid, m in meetings.items():
                vid = m.get("venue_id", "")
                meeting_venue[mid] = venue_names.get(vid, "")

            for race_id, race in races.items():
                rnum = race.get("number", 0)
                if int(rnum) != race_number:
                    continue

                mid = race.get("meeting_id", "")
                venue = meeting_venue.get(mid, "")
                if not self._venue_match(track_lower, venue):
                    continue

                # Resolve display name from venues
                display_venue = ""
                for vid, vname in venue_names.items():
                    if vname == venue:
                        display_venue = venues[vid].get("name", venue)
                        break

                logger.info(f"Entain find_race: matched '{venue}' R{race_number} in meetings -> {race_id}")
                return {
                    "race_id": race_id,
                    "track": display_venue or venue,
                    "race_number": race_number,
                    "meeting_id": mid,
                    "venue_name": display_venue or venue,
                    "start_time": race.get("advertised_start", ""),
                    "category_id": race.get("category_id", ""),
                }

        except Exception as e:
            logger.warning(f"Entain meetings error: {e}")

        return None

    async def get_runners(self, session: dict, race_info: dict) -> list[dict]:
        """Get runners with win/place prices for an Entain race.

        Calls GET /rest/v1/racing/?method=racecard&id={race_id} and parses
        the flat-store response (markets, entrants, prices).
        """
        race_id = race_info.get("race_id")
        if not race_id:
            logger.error("Entain get_runners: no race_id in race_info")
            return []

        brand_config = session.get("brand_config", ENTAIN_BRANDS["ladbrokes"])
        api_base = brand_config.get("api_base", ENTAIN_BRANDS["ladbrokes"]["api_base"])
        headers = _brand_headers(brand_config)

        url = f"{api_base}/rest/v1/racing/?method=racecard&id={race_id}"
        try:
            async with AsyncSession(impersonate="chrome131") as s:
                resp = await s.get(url, headers=headers, timeout=15)

            if resp.status_code != 200:
                logger.warning(f"Entain get_runners: HTTP {resp.status_code} for race_id={race_id}")
                return []

            body = resp.json()
            data = body.get("data", body)

            markets = data.get("markets", {})
            entrants = data.get("entrants", {})
            prices = data.get("prices", {})
            price_fluctuations = data.get("price_fluctuations", {})

            # Find the main Win market ("Final Field", "Win Fixed", etc.)
            win_market_id, win_entrant_ids = self._find_win_market(markets)
            if not win_market_id:
                logger.warning(f"Entain get_runners: no win market found for race_id={race_id}")
                return []

            runners = []
            for eid in win_entrant_ids:
                entrant = entrants.get(eid)
                if not entrant:
                    continue

                # Skip invisible/scratched entrants
                if not entrant.get("visible", True):
                    continue

                form = entrant.get("form_summary", {})
                win_price, place_price = self._get_entrant_prices(
                    eid, prices, win_entrant_ids, price_fluctuations,
                )

                runners.append({
                    "id": eid,
                    "number": int(entrant.get("number", 0)),
                    "name": entrant.get("name", ""),
                    "barrier": int(entrant.get("barrier", 0)),
                    "win_price": win_price,
                    "place_price": place_price,
                    "market_id": win_market_id,
                    "jockey": form.get("riderOrDriver", ""),
                    "trainer": form.get("trainerName", ""),
                    "scratched": False,
                })

            runners.sort(key=lambda r: r["number"])
            logger.info(f"Entain get_runners: {len(runners)} active runners for race_id={race_id}")
            return runners

        except Exception as e:
            logger.error(f"Entain get_runners error: {e}")
            return []

    # Entain product type UUIDs (reverse-engineered from frontend)
    PRODUCT_FIXED_WIN = "940b8704-e497-4a76-b390-00918ff7d282"
    PRODUCT_FIXED_PLACE = "7cf3eea6-5654-42be-9c2e-6de280e7bb34"

    # Racing category UUIDs
    CATEGORY_HORSE = "4a2788f8-e825-4d36-9894-efd4baf1cfae"
    CATEGORY_GREYHOUND = "9daef0d7-bf3c-4f50-921d-8e818c60fe61"
    CATEGORY_HARNESS = "161d9be2-e909-4326-8c2c-35ed71fb460b"

    # Category ID mapping from racecard category_id field
    _CATEGORY_MAP = {
        "9daef0d7-bf3c-4f50-921d-8e818c60fe61": CATEGORY_GREYHOUND,
        "161d9be2-e909-4326-8c2c-35ed71fb460b": CATEGORY_HARNESS,
        "4a2788f8-e825-4d36-9894-efd4baf1cfae": CATEGORY_HORSE,
    }

    async def place_bet(self, session: dict, race_info: dict, runner: dict,
                        stake: float, stake_type: str, brand_config: dict) -> dict:
        """Place a fixed-odds WIN bet on Entain (Ladbrokes/Neds).

        Uses reverse-engineered /v2/betting/place-bet REST endpoint.
        Requires access_token from OAuth2 login.
        """
        access_token = session.get("access_token")
        if not access_token:
            if session.get("_public_only"):
                return {"success": False, "error": f"Entain login failed: {session.get('_login_error', 'no token')} — cannot place bets"}
            return {"success": False, "error": "No Entain access token — re-login required"}

        brand_config_resolved = session.get("brand_config", ENTAIN_BRANDS["ladbrokes"])
        api_base = brand_config_resolved.get("api_base", ENTAIN_BRANDS["ladbrokes"]["api_base"])
        web_base = brand_config_resolved.get("web_base", ENTAIN_BRANDS["ladbrokes"]["web_base"])
        proxy_url = session.get("proxy_url")

        entrant_id = runner.get("id")
        market_id = runner.get("market_id")
        win_price = runner.get("win_price", 0)
        race_id = race_info.get("race_id")

        if not entrant_id or not race_id:
            return {"success": False, "error": f"Missing data: entrant_id={entrant_id}, race_id={race_id}"}

        if not market_id:
            return {"success": False, "error": "No market_id — runner data incomplete"}

        # Determine racing category from race_info
        category_id = race_info.get("category_id", "")
        root_category_id = self._CATEGORY_MAP.get(category_id, self.CATEGORY_HORSE)

        # Convert decimal odds to numerator/denominator
        # Entain format: decimal = 1.0 + num/den
        if win_price and win_price > 1:
            # Find clean fraction: (price - 1) as num/den
            price_minus_one = win_price - 1.0
            # Use denominator of 100 for clean representation
            numerator = round(price_minus_one * 100)
            denominator = 100
            # Simplify common cases
            from math import gcd
            g = gcd(numerator, denominator)
            numerator //= g
            denominator //= g
        else:
            numerator = 1
            denominator = 1

        payload = {
            "client_id": "web-frontend",
            "stake": float(stake),
            "bets": [{
                "legs": [{
                    "product_type_id": self.PRODUCT_FIXED_WIN,
                    "root_category_id": root_category_id,
                    "selections": [{
                        "entrant_id": entrant_id,
                        "event_id": race_id,
                        "market_id": market_id,
                        "odds": {
                            "numerator": numerator,
                            "denominator": denominator,
                        },
                        "position": 1,
                    }],
                }],
            }],
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": CHROME_UA,
            "Origin": web_base,
            "Referer": f"{web_base}/",
        }

        try:
            async with AsyncSession(impersonate="chrome131") as s:
                if proxy_url:
                    resp = await s.post(
                        f"{api_base}/v2/betting/place-bet",
                        headers=headers, json=payload, proxy=proxy_url, timeout=20,
                    )
                else:
                    resp = await s.post(
                        f"{api_base}/v2/betting/place-bet",
                        headers=headers, json=payload, timeout=20,
                    )

            logger.info(f"Entain place_bet: HTTP {resp.status_code}")

            if resp.status_code == 403:
                return {"success": False, "error": "Kasada blocked (403) — Akamai bypass may be needed on bet API"}

            data = resp.json()

            # Check for errors
            if resp.status_code != 200:
                detail = data.get("detail", data.get("message", resp.text[:300]))
                return {"success": False, "error": f"HTTP {resp.status_code}: {detail}", "raw": data}

            # Check for bet placement errors in response
            error = data.get("error") or data.get("detail")
            if error:
                return {"success": False, "error": str(error), "raw": data}

            # Extract bet ID / receipt from response
            bet_id = data.get("bet_id") or data.get("id") or data.get("transaction_id") or ""
            receipt = data.get("receipt") or bet_id

            # Check for successful placement indicators
            status = data.get("status", "")
            if status in ("rejected", "failed", "error"):
                reason = data.get("reason") or data.get("message") or data.get("detail") or "Bet rejected"
                return {"success": False, "error": reason, "raw": data}

            logger.info(f"Entain place_bet SUCCESS: bet_id={bet_id}")
            return {
                "success": True,
                "bet_id": str(bet_id),
                "receipt": str(receipt),
                "stake": stake,
                "balance": data.get("balance"),
                "raw": data,
            }

        except Exception as e:
            logger.error(f"Entain place_bet error: {e}")
            return {"success": False, "error": f"Entain placement failed: {e}"}

    # ── Balance ───────────────────────────────────────────────────

    async def get_balance(self, session: dict) -> float:
        """Get account balance via authenticated API."""
        access_token = session.get("access_token")
        if not access_token:
            return 0.0

        brand_config = session.get("brand_config", ENTAIN_BRANDS["ladbrokes"])
        api_base = brand_config.get("api_base", ENTAIN_BRANDS["ladbrokes"]["api_base"])
        web_base = brand_config.get("web_base", ENTAIN_BRANDS["ladbrokes"]["web_base"])
        proxy_url = session.get("proxy_url")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": CHROME_UA,
            "Origin": web_base,
            "Referer": f"{web_base}/",
        }

        try:
            async with AsyncSession(impersonate="chrome131") as s:
                if proxy_url:
                    resp = await s.get(f"{api_base}/v2/account/balance", headers=headers, proxy=proxy_url, timeout=10)
                else:
                    resp = await s.get(f"{api_base}/v2/account/balance", headers=headers, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                # Try common balance field names
                bal = data.get("balance") or data.get("cash_balance") or data.get("available_balance") or 0
                return float(bal) if bal else 0.0
            else:
                logger.warning(f"Entain balance check: HTTP {resp.status_code}")
                return 0.0
        except Exception as e:
            logger.warning(f"Entain balance error: {e}")
            return 0.0

    async def get_balances(self, session: dict) -> dict:
        """Get cash + bonus balances."""
        cash = await self.get_balance(session)
        return {"cash": cash, "bonus": 0.0}

    # ── Internal helpers ─────────────────────────────────────────

    @staticmethod
    def _venue_match(track_lower: str, venue_lower: str) -> bool:
        """Flexible venue name matching: exact, substring, or reverse substring."""
        if not track_lower or not venue_lower:
            return False
        return (
            track_lower == venue_lower
            or track_lower in venue_lower
            or venue_lower in track_lower
        )

    @staticmethod
    def _find_win_market(markets: dict) -> tuple[str | None, list[str]]:
        """Find the main Win market from the markets dict.

        Returns (market_id, entrant_ids) or (None, []) if not found.
        Looks for markets named "Final Field", "Win Fixed", etc.
        """
        for mid, market in markets.items():
            name = (market.get("name") or "").lower().strip()
            if name in _WIN_MARKET_NAMES:
                entrant_ids = market.get("entrant_ids", [])
                return mid, entrant_ids

        # Fallback: pick the market with the most entrants (likely the Win market)
        best_mid = None
        best_ids: list[str] = []
        for mid, market in markets.items():
            eids = market.get("entrant_ids", [])
            if len(eids) > len(best_ids):
                best_mid = mid
                best_ids = eids

        if best_mid:
            logger.info(f"Entain _find_win_market: no named win market, using '{markets[best_mid].get('name')}' ({len(best_ids)} entrants)")
            return best_mid, best_ids

        return None, []

    @staticmethod
    def _get_entrant_prices(
        entrant_id: str,
        prices_dict: dict,
        all_entrant_ids: list[str],
        price_fluctuations: dict,
    ) -> tuple[float | None, float | None]:
        """Find the current fixed win and place prices for an entrant.

        Strategy:
        1. Collect all valid prices (num>0, den>0) for this entrant
        2. Group by source_id
        3. Find sources that appear for ALL entrants (= current price feeds)
        4. Among those, separate win vs place by value range
        5. Use price_fluctuations as a cross-reference for the win price
        6. If that fails, just take the highest valid price as win
        """
        # Collect all valid prices for this entrant, grouped by source
        entrant_prices: dict[str, float] = {}  # key suffix -> decimal price
        for price_key, price_entry in prices_dict.items():
            if not price_key.startswith(f"{entrant_id}:"):
                continue
            decimal = _parse_price(price_entry)
            if decimal is not None:
                # key format: "{entrant_id}:{source_id}::{suffix}"
                suffix = price_key[len(entrant_id) + 1:]  # everything after "entrant_id:"
                entrant_prices[suffix] = decimal

        if not entrant_prices:
            return None, None

        # Group ALL prices by source across all entrants
        source_counts: dict[str, int] = {}
        for price_key, price_entry in prices_dict.items():
            parts = price_key.split(":", 1)
            if len(parts) < 2:
                continue
            eid = parts[0]
            if eid not in all_entrant_ids:
                continue
            decimal = _parse_price(price_entry)
            if decimal is not None:
                suffix = price_key[len(eid) + 1:]
                source_counts[suffix] = source_counts.get(suffix, 0) + 1

        num_entrants = len(all_entrant_ids)

        # Sources present for all (or most) entrants are likely current feeds
        universal_sources = {
            src for src, count in source_counts.items()
            if count >= num_entrants * 0.8  # allow some tolerance for scratched runners
        }

        # Get prices from universal sources for this entrant
        universal_prices = {
            src: price for src, price in entrant_prices.items()
            if src in universal_sources
        }

        # Try to identify win price using price_fluctuations as reference
        flucs_last = None
        for fid, flucs in price_fluctuations.items():
            if entrant_id in fid and isinstance(flucs, list) and flucs:
                flucs_last = float(flucs[-1])
                break

        win_price = None
        place_price = None

        if universal_prices:
            price_list = sorted(universal_prices.values(), reverse=True)

            if flucs_last:
                # Pick the price closest to the last fluctuation as win
                win_price = min(price_list, key=lambda p: abs(p - flucs_last))
                # Place is a different, lower price (if available)
                remaining = [p for p in price_list if p != win_price and p < win_price]
                if remaining:
                    place_price = max(remaining)
            elif len(price_list) >= 2:
                # Highest = win, second = place (common pattern)
                win_price = price_list[0]
                place_price = price_list[1]
            else:
                win_price = price_list[0]
        else:
            # No universal sources — just use all prices for this entrant
            all_prices = sorted(entrant_prices.values(), reverse=True)

            if flucs_last:
                win_price = min(all_prices, key=lambda p: abs(p - flucs_last))
                remaining = [p for p in all_prices if p != win_price and p < win_price]
                if remaining:
                    place_price = max(remaining)
            elif len(all_prices) >= 2:
                win_price = all_prices[0]
                place_price = all_prices[1]
            else:
                win_price = all_prices[0]

        return win_price, place_price

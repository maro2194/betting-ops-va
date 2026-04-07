"""
Amused/BlackStream platform client.
Handles auth (Auth0 ROPC), racing data (REST), and bet placement for all Amused brands.
Brands: BetDeluxe, BetNation, Surge, PulseBet, BigBet, YesBet, MightyBet.
"""
import re
import time
import logging
from typing import Optional

from curl_cffi.requests import AsyncSession

from .base import PlatformClient

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
SEC_CH_UA = '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'

API_BASE = "https://api.blackstream.com.au"

# ─── Brand Configurations ──────────────────────────────────────────────────

AMUSED_BRANDS = {
    "betdeluxe": {
        "org_id": "400",
        "channel_id": "502",
        "auth0_tenant": "betdeluxe-prd.au.auth0.com",
        "auth0_client_id": "qQBxMiu03tzO7SZVJcVZ3juPN4fEFHW7",
        "auth0_connection": "BetDeluxe-Prd",
        "auth0_audience": "https://api.betdeluxe.com",
    },
    "betnation": {
        "org_id": "300",
        "channel_id": "402",
        "auth0_tenant": "betnation-prd.au.auth0.com",
        "auth0_client_id": "bbsGSuvQdHbMhofURrDbusAOKWAjbwF6",
        "auth0_connection": "BetNation-Prd",
        "auth0_audience": "https://api.betnation.com",
    },
    "surge": {
        "org_id": "100",
        "channel_id": "2",
        "auth0_tenant": "surge-production.au.auth0.com",
        "auth0_client_id": "eKKeXSr5HnCIONr3T9R1MeeokLm7jw9l",
        "auth0_connection": "Surge-Prd",
        "auth0_audience": "https://api.surge.com",
    },
    "pulsebet": {
        "org_id": "500",
        "channel_id": "602",
        "auth0_tenant": "pulsebet-prd.au.auth0.com",
        "auth0_client_id": "0TnVnmiCuEAX9gU1cgLxWNHnYxvPhJSh",
        "auth0_connection": "PulseBet-Prd",
        "auth0_audience": "https://api.pulsebet.com",
    },
    "bigbet": {
        "org_id": "600",
        "channel_id": "702",
        "auth0_tenant": "bigbet-prd.au.auth0.com",
        "auth0_client_id": "LmRErw01tDwzjiEiY5Gj1RGM39FdYvnE",
        "auth0_connection": "BigBet-Prd",
        "auth0_audience": "https://api.bigbet.com",
    },
    "yesbet": {
        "org_id": "700",
        "channel_id": "802",
        "auth0_tenant": "yesbet-prd.au.auth0.com",
        "auth0_client_id": "C7hshmPHt9J7xdTIrt0TY8KAi0mJeosF",
        "auth0_connection": "YesBet-Prd",
        "auth0_audience": "https://api.yesbet.com",
    },
    "mightybet": {
        "org_id": "900",
        "channel_id": "1002",
        "auth0_tenant": "mightybet-prd.au.auth0.com",
        "auth0_client_id": "KNZSLxyTc3xA4QnIbngHl88vBUEoBKOY",
        "auth0_connection": "MightyBet-Prd",
        "auth0_audience": "https://api.mightybet.com",
    },
}


# ─── Helper Functions ───────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Normalize a horse/track name for fuzzy matching."""
    name = name.lower().strip()
    name = re.sub(r"['\u2019\u2018]", "", name)   # remove apostrophes
    name = re.sub(r"[^a-z0-9\s]", "", name)        # remove other punctuation
    name = re.sub(r"\s+", " ", name).strip()        # collapse whitespace
    return name


def _names_match(a: str, b: str) -> bool:
    """Fuzzy match two names (horse or track)."""
    return _normalize_name(a) == _normalize_name(b)


def _browser_headers(referer: str = "https://www.betdeluxe.com.au/") -> dict:
    """Base browser headers for Amused/BlackStream requests."""
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "user-agent": USER_AGENT,
        "origin": referer.rstrip("/"),
        "referer": referer,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
        "accept-language": "en-AU,en-US;q=0.9,en;q=0.8",
    }


def _api_get_headers(token: str, org_id: str) -> dict:
    """Headers for GET requests to BlackStream API."""
    headers = _browser_headers()
    headers["Authorization"] = f"Bearer {token}"
    headers["X-NextGen-OrgId"] = org_id
    headers["X-NextGen-AppVersion"] = "7.8.0"
    return headers


def _api_post_headers(token: str, org_id: str, channel_id: str) -> dict:
    """Headers for POST requests (bet placement) to BlackStream API."""
    headers = _browser_headers()
    headers["Authorization"] = f"Bearer {token}"
    headers["X-NextGen-OrgId"] = org_id
    headers["X-NextGen-ChannelID"] = channel_id
    headers["X-NextGen-AppVersion"] = "7.8.0"
    return headers


# ─── Amused Client ──────────────────────────────────────────────────────────

class AmusedClient(PlatformClient):
    """Client for all Amused/BlackStream brands."""

    async def login(self, email: str, password: str, proxy_url: str, brand_config: dict) -> dict:
        """Authenticate via Auth0 password grant.
        Token lifetime is only 300s (5 minutes) -- caller must re-login frequently."""
        tenant = brand_config.get("auth0_tenant", "")
        client_id = brand_config.get("auth0_client_id", "")
        connection = brand_config.get("auth0_connection", "")
        audience = brand_config.get("auth0_audience", "")

        if not tenant or not client_id:
            logger.error(f"Amused login: missing auth0 config for brand")
            return {"success": False, "error": "Missing auth0_tenant or auth0_client_id in brand config"}

        token_url = f"https://{tenant}/oauth/token"
        payload = {
            "grant_type": "password",
            "username": email,
            "password": password,
            "client_id": client_id,
            "audience": audience,
            "scope": "openid profile email",
            "connection": connection,
        }

        headers = {
            "content-type": "application/json",
            "user-agent": USER_AGENT,
            "accept": "application/json",
        }

        async with AsyncSession(impersonate="chrome") as session:
            if proxy_url:
                session.proxies = {"http": proxy_url, "https": proxy_url}

            try:
                resp = await session.post(token_url, json=payload, headers=headers, timeout=15)
            except Exception as e:
                logger.error(f"Amused login request failed: {e}")
                return {"success": False, "error": f"Request failed: {e}"}

            if resp.status_code != 200:
                logger.error(f"Amused Auth0 login failed: {resp.status_code} {resp.text[:300]}")
                return {"success": False, "error": f"Auth0 login failed: {resp.status_code}"}

            data = resp.json()
            access_token = data.get("access_token", "")
            if not access_token:
                return {"success": False, "error": "No access_token in Auth0 response"}

            expires_in = data.get("expires_in", 300)
            return {
                "success": True,
                "access_token": access_token,
                "id_token": data.get("id_token", ""),
                "expires_at": time.time() + expires_in,
                "brand_config": brand_config,
                "proxy_url": proxy_url,
                "email": email,
                "password": password,
            }

    def is_session_valid(self, session: dict) -> bool:
        """Check if Auth0 token is still valid. Token lifetime is ~300s (5 minutes).
        Leave 30s buffer so we can re-login before it expires."""
        expires_at = session.get("expires_at", 0)
        return time.time() < (expires_at - 30)

    async def find_race(self, session: dict, track: str, race_number: int) -> dict | None:
        """Find a race by venue name and race number via nextup endpoint.
        Returns race info dict with race_id, meeting_id, track, number, etc."""
        access_token = session["access_token"]
        brand_config = session["brand_config"]
        org_id = brand_config["org_id"]
        proxy_url = session.get("proxy_url")

        url = f"{API_BASE}/api/racing/v2/home/nextup"
        headers = _api_get_headers(access_token, org_id)

        async with AsyncSession(impersonate="chrome") as s:
            if proxy_url:
                s.proxies = {"http": proxy_url, "https": proxy_url}

            try:
                resp = await s.get(url, headers=headers, timeout=15)
            except Exception as e:
                logger.error(f"Amused find_race request failed: {e}")
                return None

            if resp.status_code != 200:
                logger.error(f"Amused nextup failed: {resp.status_code} {resp.text[:300]}")
                return None

            raw = resp.json()
            # Response: {code, data: {thoroughbred: [...], greyhound: [...], harness: [...]}}
            # Each race type contains a flat list of race objects (NOT grouped by meeting)
            data = raw.get("data", raw) if isinstance(raw, dict) else raw

            # Flatten all race types into one list
            all_races = []
            if isinstance(data, dict):
                for race_type_key, races_list in data.items():
                    if isinstance(races_list, list):
                        all_races.extend(races_list)
            elif isinstance(data, list):
                all_races = data

            for race in all_races:
                venue = race.get("venue", race.get("venueName", ""))
                r_num = race.get("raceNumber", race.get("number", 0))
                if _names_match(venue, track) and r_num == race_number:
                    race_id = race.get("raceId", race.get("id"))
                    meeting_id = race.get("meetId", race.get("meetingId"))
                    return {
                        "race_id": race_id,
                        "meeting_id": meeting_id,
                        "track": venue,
                        "race_number": r_num,
                        "race_name": race.get("raceName", race.get("name", "")),
                        "status": race.get("raceStatus", race.get("status", "")),
                        "start_time": race.get("startTime", race.get("advertisedStartTime", "")),
                    }

            logger.warning(f"Amused: race not found - {track} R{race_number}")
            return None

    async def get_runners(self, session: dict, race_info: dict) -> list[dict]:
        """Get runners with fixed odds for a race via futures endpoint.
        Returns list of runner dicts with id, name, tab_no, win_price, outcome_id, etc."""
        access_token = session["access_token"]
        brand_config = session["brand_config"]
        org_id = brand_config["org_id"]
        proxy_url = session.get("proxy_url")

        meet_id = race_info["meeting_id"]
        race_id = race_info["race_id"]
        url = f"{API_BASE}/api/racing/v1/meetings/{meet_id}/races/{race_id}/futures"
        headers = _api_get_headers(access_token, org_id)

        async with AsyncSession(impersonate="chrome") as s:
            if proxy_url:
                s.proxies = {"http": proxy_url, "https": proxy_url}

            try:
                resp = await s.get(url, headers=headers, timeout=15)
            except Exception as e:
                logger.error(f"Amused get_runners request failed: {e}")
                return []

            if resp.status_code != 200:
                logger.error(f"Amused futures failed: {resp.status_code} {resp.text[:300]}")
                return []

            raw = resp.json()

            # Response: {code, data: {meeting: {race: {runners: [...], raceFixedMarketPrices: [...]}}}}
            data = raw.get("data", raw) if isinstance(raw, dict) else raw
            meeting = data.get("meeting", data) if isinstance(data, dict) else {}
            race_data = meeting.get("race", meeting) if isinstance(meeting, dict) else {}
            runners_raw = race_data.get("runners", [])
            fixed_prices = race_data.get("raceFixedMarketPrices", [])

            # Build lookup: (outcomeId, marketTypeCode) -> price info
            # raceFixedMarketPrices has separate WIN and PLC entries per runner
            win_price_map = {}   # outcomeId -> {price, fixedMarketId}
            place_price_map = {} # outcomeId -> {price, fixedMarketId}
            for fp in fixed_prices:
                oid = fp.get("outcomeId")
                mtype = fp.get("marketTypeCode", "")
                if oid is None:
                    continue
                if mtype == "WIN":
                    win_price_map[oid] = fp
                elif mtype == "PLC":
                    place_price_map[oid] = fp

            runners = []
            for r in runners_raw:
                runner_name = r.get("runnerName", r.get("name", ""))
                tab_no = r.get("runnerNumber", r.get("tabNo", r.get("number", 0)))
                outcome_id = r.get("outcomeId", r.get("id"))
                status = r.get("status", r.get("runnerStatus", "")).upper()

                if status in ("SCRATCHED", "LATE_SCRATCHING"):
                    continue

                # Get fixed odds from raceFixedMarketPrices
                win_data = win_price_map.get(outcome_id, {})
                place_data = place_price_map.get(outcome_id, {})
                win_price = win_data.get("price")
                place_price = place_data.get("price")
                fixed_market_id = win_data.get("fixedMarketId")

                # Fallback: try runner-level price from runnerPrice array
                if win_price is None and r.get("marketTypeCode") == "WIN":
                    prices = r.get("runnerPrice", [])
                    if prices:
                        win_price = prices[-1]  # last entry is current price

                runners.append({
                    "id": outcome_id,
                    "outcome_id": outcome_id,
                    "tab_no": tab_no,
                    "name": runner_name,
                    "win_price": win_price,
                    "place_price": place_price,
                    "fixed_market_id": fixed_market_id,
                    "jockey": r.get("jockeyName", r.get("jockey", "")),
                    "trainer": r.get("trainerName", r.get("trainer", "")),
                    "barrier": r.get("barrier", r.get("barrierNumber", 0)),
                })

            # Deduplicate by outcome_id (futures returns WIN + PLC entries per runner)
            seen_ids = set()
            unique_runners = []
            for r in runners:
                if r["outcome_id"] not in seen_ids:
                    seen_ids.add(r["outcome_id"])
                    unique_runners.append(r)
            return unique_runners

    async def place_bet(self, session: dict, race_info: dict, runner: dict,
                        stake: float, stake_type: str, brand_config: dict) -> dict:
        """Place a fixed-odds win bet via BlackStream API.
        Uses PascalCase payload format. Stake is in dollars (float)."""
        access_token = session.get("access_token", "")
        org_id = brand_config["org_id"]
        channel_id = brand_config["channel_id"]
        proxy_url = session.get("proxy_url")

        race_id = race_info["race_id"]
        meet_id = race_info["meeting_id"]
        outcome_id = runner.get("outcome_id", runner.get("id"))
        fixed_market_id = runner.get("fixed_market_id")
        price = runner.get("win_price", 0)

        if not outcome_id or not fixed_market_id:
            return {"success": False, "error": "Missing outcome_id or fixed_market_id on runner"}

        url = f"{API_BASE}/api/bet/v1/placebet/single/racing"
        headers = _api_post_headers(access_token, org_id, channel_id)

        payload = {
            "EventId": int(race_id) if isinstance(race_id, (int, str)) and str(race_id).isdigit() else race_id,
            "MeetingId": int(meet_id) if isinstance(meet_id, (int, str)) and str(meet_id).isdigit() else meet_id,
            "OutcomeId": int(outcome_id) if isinstance(outcome_id, (int, str)) and str(outcome_id).isdigit() else outcome_id,
            "FixedMarketId": int(fixed_market_id) if isinstance(fixed_market_id, (int, str)) and str(fixed_market_id).isdigit() else fixed_market_id,
            "MarketTypeCode": "WIN",
            "DividendTypeCode": "FIXED",
            "TotalStake": stake,
            "FixedPrice": price,
            "AcceptOddsChange": True,
        }

        # Add bonus bet flag if applicable
        if stake_type in ("token", "promo", "bonus"):
            payload["IsBonusBet"] = True

        async with AsyncSession(impersonate="chrome") as s:
            if proxy_url:
                s.proxies = {"http": proxy_url, "https": proxy_url}

            try:
                resp = await s.post(url, json=payload, headers=headers, timeout=15)
            except Exception as e:
                logger.error(f"Amused place_bet request failed: {e}")
                return {"success": False, "error": f"Request failed: {e}"}

            if resp.status_code not in (200, 201):
                logger.error(f"Amused place_bet failed: {resp.status_code} {resp.text[:500]}")
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}

            data = resp.json()
            code = data.get("code", "")
            message = data.get("message", "")
            bet_data = data.get("data", {})

            # BetPlaced = success
            if code == "BetPlaced":
                bet_id = bet_data.get("betId", bet_data.get("ticketNumber", ""))
                return {
                    "success": True,
                    "bet_id": str(bet_id),
                    "status": "placed",
                    "details": data,
                }

            # Any other code is an error
            return {"success": False, "error": f"{code}: {message}", "details": data}

    async def get_user_promos(self, session: dict) -> dict:
        """Fetch user-specific promo tokens via BlackStream promotions API.
        Returns dict with token counts and promo details."""
        access_token = session.get("access_token", "")
        brand_config = session["brand_config"]
        org_id = brand_config["org_id"]
        proxy_url = session.get("proxy_url")

        url = f"{API_BASE}/api/promotion/v1/user/v2/promotions"
        headers = _api_get_headers(access_token, org_id)

        async with AsyncSession(impersonate="chrome") as s:
            try:
                resp = await s.get(url, headers=headers, proxy=proxy_url, timeout=15)
            except Exception as e:
                logger.error(f"Amused get_user_promos failed: {e}")
                return {"boost_tokens": 0, "bonus_back_tokens": 0, "deposit_match_tokens": 0, "promos": [], "redeemed": []}

            if resp.status_code != 200:
                logger.error(f"Amused user promos: HTTP {resp.status_code} {resp.text[:200]}")
                return {"boost_tokens": 0, "bonus_back_tokens": 0, "deposit_match_tokens": 0, "promos": [], "redeemed": []}

            data = resp.json()
            # May be wrapped: {code, data: {...}} or direct
            promo_data = data.get("data", data) if isinstance(data, dict) and "data" in data else data

            boost_tokens = 0
            bonus_back_tokens = 0
            deposit_match_tokens = 0
            promos = []

            # Price Boosts (Racing Singles, SGM, Multi)
            for key, label in [
                ("priceBoostRacingSingles", "Racing Singles Boost"),
                ("priceBoostSGM", "SGM Boost"),
                ("priceBoostMulti", "Multi Boost"),
            ]:
                boost = promo_data.get(key)
                if boost and boost.get("remainingTokenCount", 0) > 0:
                    count = boost["remainingTokenCount"]
                    max_stake = boost.get("maxStake", 0)
                    boost_tokens += count
                    desc = f"{label} (Max ${max_stake:.0f})" if max_stake else label
                    promos.append({
                        "promo_id": key,
                        "type": "Boost",
                        "description": desc,
                        "status": boost.get("status", "Active"),
                        "tokens": count,
                        "tokens_remaining": count,
                        "expiry_date": boost.get("expiryDateTime", ""),
                        "boost_data": {
                            "boost_type": key,
                            "boost_percentage": 0,
                        },
                        "bonus_back_data": None,
                        "deposit_match_data": None,
                    })

            # ULMBS (Bonus Backs)
            for ulmb in promo_data.get("ulmbs", []):
                count = ulmb.get("remainingTokenCount", 0)
                if count > 0:
                    bonus_back_tokens += count
                    max_bb = ulmb.get("maximumBonusBack", 0)
                    event_types = ulmb.get("masterEventType", [])
                    # masterEventType can be strings ["Racing"] or ints [0, 1]
                    type_labels = []
                    for et in event_types:
                        if et == 0 or et == "Racing":
                            type_labels.append("Racing")
                        elif et == 1 or et == "Sports":
                            type_labels.append("Sports")
                        elif isinstance(et, str):
                            type_labels.append(et)

                    # Extract criteria (2nd, 2nd/3rd, 1 leg fails, etc.)
                    criteria = "BonusBack"
                    offer_criteria = ulmb.get("offerCriteria", [])
                    for oc in offer_criteria:
                        if oc.get("field") == "SelectionFinalPosition":
                            positions = oc.get("values", [])
                            if "3" in positions:
                                criteria = "BonusBack3rd"
                            else:
                                criteria = "BonusBack2nd"
                        elif oc.get("field") == "FailedLegCount":
                            criteria = "BonusBackMulti"

                    # Extract event type from eligibility (Thoroughbred, Greyhounds, etc.)
                    elig_event_types = []
                    for ec in ulmb.get("eligibilityCriteria", []):
                        if ec.get("field") == "EventType" and ec.get("operator") == "IN":
                            elig_event_types = ec.get("values", [])

                    promos.append({
                        "promo_id": ulmb.get("promotionId", ulmb.get("title", "ulmb")),
                        "type": "BonusBack",
                        "description": ulmb.get("title", ulmb.get("description", "Bonus Back")),
                        "status": "Active",
                        "tokens": count,
                        "tokens_remaining": count,
                        "expiry_date": ulmb.get("expiryDateTime", ""),
                        "boost_data": None,
                        "bonus_back_data": {
                            "details": ulmb.get("description", ""),
                            "criteria": criteria,
                            "event_config_type": "Global",
                            "global_config": [{"event_type": t} for t in (elig_event_types or type_labels)],
                            "event_config": [],
                            "max_deposit": int(max_bb * 100),
                            "field_size": 0,
                        },
                        "deposit_match_data": None,
                    })

            # Deposit Offers
            for dep in promo_data.get("depositOffers", []):
                deposit_match_tokens += 1
                promos.append({
                    "promo_id": "depositOffer",
                    "type": "DepositMatch",
                    "description": "Deposit Match Offer",
                    "status": "Active",
                    "tokens": 1,
                    "tokens_remaining": 1,
                    "expiry_date": dep.get("expiryDateTime", ""),
                    "boost_data": None,
                    "bonus_back_data": None,
                    "deposit_match_data": dep,
                })

            return {
                "boost_tokens": boost_tokens,
                "bonus_back_tokens": bonus_back_tokens,
                "deposit_match_tokens": deposit_match_tokens,
                "promos": promos,
                "redeemed": [],
            }

    async def get_balance(self, session: dict) -> float:
        """Get account cash balance."""
        b = await self.get_balances(session)
        return b.get("cash", 0.0)

    async def get_balances(self, session: dict) -> dict:
        """Get cash + bonus balance. Returns {"cash": float, "bonus": float}."""
        access_token = session.get("access_token", "")
        brand_config = session["brand_config"]
        org_id = brand_config["org_id"]
        proxy_url = session.get("proxy_url")

        url = f"{API_BASE}/api/account/v1/user/wallet"
        headers = _api_get_headers(access_token, org_id)

        async with AsyncSession(impersonate="chrome") as s:
            try:
                resp = await s.get(url, headers=headers, proxy=proxy_url, timeout=15)
            except Exception as e:
                logger.error(f"Amused get_balances failed: {e}")
                return {"cash": 0.0, "bonus": 0.0}

            if resp.status_code != 200:
                logger.error(f"Amused balance failed: {resp.status_code} {resp.text[:300]}")
                return {"cash": 0.0, "bonus": 0.0}

            data = resp.json()
            cash = 0.0
            bonus = 0.0
            # Response can be {code, data: [...]} or just [...]
            wallets = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(wallets, list):
                for w in wallets:
                    if w.get("accountType") == 1:
                        cash = float(w.get("balance", 0))
                    elif w.get("accountType") == 2:
                        bonus = float(w.get("balance", 0))
            return {"cash": cash, "bonus": bonus}

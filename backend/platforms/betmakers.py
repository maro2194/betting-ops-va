"""
BetMakers/Apollo platform client.
Handles auth (Cognito), racing data (GraphQL), and bet placement for all BetMakers brands.
Brands: TerryBet, CrownBet, PonyBet, BetIt, DiamondBet, BetDash, SwiftBet.
"""
import re
import time
import uuid
import logging
from typing import Optional

from curl_cffi.requests import AsyncSession

from .base import PlatformClient

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
SEC_CH_UA = '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'

COGNITO_URL = "https://cognito-idp.ap-southeast-2.amazonaws.com"

# ─── Brand Configurations ──────────────────────────────────────────────────

BETMAKERS_BRANDS = {
    "terrybet": {
        "cognito_client_id": "20rvqtl5rtqr59qmems6ai1i3t",
        "user_pool_id": "ap-southeast-2_8npzXFCss",
        "racing_host": "racing.terrybet.bmapollo.com",
        "platform_host": "platform.terrybet.bmapollo.com",
        "referer": "https://terrybet.com.au/",
        "product_keyword": "TBF",
    },
    "crownbet": {
        "cognito_client_id": "1b8vvipjgq694cqt6koarsuqrj",
        "user_pool_id": "ap-southeast-2_UxQKFkiUE",
        "racing_host": "racing.crownbet.bmapollo.com",
        "platform_host": "platform.crownbet.bmapollo.com",
        "referer": "https://crownbet.com.au/",
        "product_keyword": "TBF",
    },
    "ponybet": {
        "cognito_client_id": "1va9olufe5nbf1r704durfkfa5",
        "user_pool_id": "ap-southeast-2_sGlghz4Ov",
        "racing_host": "racing.ponybet.bmapollo.com",
        "platform_host": "platform.ponybet.bmapollo.com",
        "referer": "https://ponybet.com.au/",
        "product_keyword": "TBF",
    },
    "betit": {
        "cognito_client_id": "7m70ns60lc6g3ovhhdjhs0lcb6",
        "user_pool_id": "ap-southeast-2_LImK0TYTM",
        "racing_host": "racing.betit.bmapollo.com",
        "platform_host": "platform.betit.bmapollo.com",
        "referer": "https://betit.com.au/",
        "product_keyword": "TBF",
    },
    "diamondbet": {
        "cognito_client_id": "65tumqa6elclm410pq4bqklra3",
        "user_pool_id": "ap-southeast-2_tEqGrxbhn",
        "racing_host": "racing.diamondbet.bmapollo.com",
        "platform_host": "platform.diamondbet.bmapollo.com",
        "referer": "https://diamondbet.com.au/",
        "product_keyword": "TBF",
    },
    "betdash": {
        "cognito_client_id": "4cq6ubu5rfga8aq3uqoa0iu707",
        "user_pool_id": "ap-southeast-2_PIH8zd6Xh",
        "racing_host": "racing.betdash.bmapollo.com",
        "platform_host": "platform.betdash.bmapollo.com",
        "referer": "https://betdash.com.au/",
        "product_keyword": "TBF",
    },
    "swiftbet": {
        "cognito_client_id": "2k7qfmkk7jaisoack274e4em7i",
        "user_pool_id": "ap-southeast-2_qG2ieTekV",
        "racing_host": "racing.swiftbet.bmapollo.com",
        "platform_host": "platform.swiftbet.bmapollo.com",
        "referer": "https://swiftbet.com.au/",
        "product_keyword": "TBF",
    },
}

# ─── GraphQL Queries ────────────────────────────────────────────────────────

NEXT_TO_JUMP_QUERY = """
query nextToJumpRaces($first: Int, $raceType: [RaceType], $country: [String]) {
  nextToJumpRaces(first: $first, raceType: $raceType, country: $country) {
    id
    name
    number
    status
    start_at
    meeting {
      id
      type
      track {
        name
        state
        country
      }
    }
  }
}
"""

RACE_CARD_QUERY = """
query meetingRaces($meetingId: ID!) {
  meetingRaces(meetingId: $meetingId) {
    id
    type
    track {
      name
      state
      country
    }
    races {
      id
      name
      number
      status
      start_at
      distance
      fixed_odds_enabled
      places_to_pay
      runners {
        id
        tab_no
        name
        barrier
        weight_total
        jockey
        trainer
        status
        prices {
          bet_type
          product {
            keyword
            is_fixed
          }
          display_price
          price
        }
      }
    }
  }
}
"""

CREATE_BET_MUTATION = """
mutation createBet($input: CreateBetInput!) {
  createBet(input: $input) {
    id
    status
    bets {
      id
      status
      payout
      odds
      amount
      is_bonus_bet
    }
    errors {
      message
      code
    }
  }
}
"""

BALANCE_QUERY = """
query getUser {
  getUser {
    account_balance
    bonus_account_balance
    withdrawal_balance
  }
}
"""


# ─── Helper Functions ───────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Normalize a horse/track name for fuzzy matching.
    Strips punctuation, lowercases, handles apostrophes."""
    name = name.lower().strip()
    name = re.sub(r"['\u2019\u2018]", "", name)  # remove apostrophes
    name = re.sub(r"[^a-z0-9\s]", "", name)       # remove other punctuation
    name = re.sub(r"\s+", " ", name).strip()       # collapse whitespace
    return name


def _names_match(a: str, b: str) -> bool:
    """Fuzzy match two names (horse or track)."""
    return _normalize_name(a) == _normalize_name(b)


def _make_headers(referer: str) -> dict:
    """Base browser headers for BetMakers requests."""
    return {
        "accept": "application/graphql-response+json, application/json",
        "content-type": "application/json",
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "user-agent": USER_AGENT,
        "referer": referer,
    }


def _racing_headers(api_key: str, referer: str) -> dict:
    """Headers for racing.{brand}.bmapollo.com (X-Api-Key only, NO Bearer)."""
    headers = _make_headers(referer)
    headers["x-api-key"] = api_key
    return headers


def _platform_headers(access_token: str, api_key: str, referer: str) -> dict:
    """Headers for platform.{brand}.bmapollo.com (X-Api-Key + Bearer)."""
    headers = _make_headers(referer)
    headers["x-api-key"] = api_key
    headers["authorization"] = f"Bearer {access_token}"
    return headers


# ─── BetMakers Client ───────────────────────────────────────────────────────

class BetMakersClient(PlatformClient):
    """Client for all BetMakers/Apollo brands."""

    async def login(self, email: str, password: str, proxy_url: str, brand_config: dict) -> dict:
        """Authenticate via AWS Cognito USER_PASSWORD_AUTH.
        Returns session dict with access_token, id_token, refresh_token, expires_at."""
        client_id = brand_config["cognito_client_id"]

        payload = {
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": client_id,
            "AuthParameters": {
                "USERNAME": email,
                "PASSWORD": password,
            },
        }

        headers = {
            "content-type": "application/x-amz-json-1.1",
            "x-amz-target": "AWSCognitoIdentityProviderService.InitiateAuth",
            "user-agent": USER_AGENT,
        }

        async with AsyncSession(impersonate="chrome") as session:
            if proxy_url:
                session.proxies = {"http": proxy_url, "https": proxy_url}

            try:
                resp = await session.post(
                    COGNITO_URL, json=payload, headers=headers, timeout=15
                )
            except Exception as e:
                logger.error(f"BetMakers login request failed: {e}")
                return {"success": False, "error": f"Request failed: {e}"}

            if resp.status_code != 200:
                logger.error(f"BetMakers Cognito auth failed: {resp.status_code} {resp.text[:300]}")
                return {"success": False, "error": f"Cognito auth failed: {resp.status_code}"}

            data = resp.json()
            auth_result = data.get("AuthenticationResult", {})
            access_token = auth_result.get("AccessToken", "")
            if not access_token:
                # Handle challenge (e.g. NEW_PASSWORD_REQUIRED)
                challenge = data.get("ChallengeName", "unknown")
                logger.error(f"BetMakers login challenge: {challenge}")
                return {"success": False, "error": f"Auth challenge: {challenge}"}

            expires_in = auth_result.get("ExpiresIn", 3600)
            return {
                "success": True,
                "access_token": access_token,
                "id_token": auth_result.get("IdToken", ""),
                "refresh_token": auth_result.get("RefreshToken", ""),
                "expires_at": time.time() + expires_in,
                "brand_config": brand_config,
                "proxy_url": proxy_url,
            }

    def is_session_valid(self, session: dict) -> bool:
        """Check if Cognito tokens are still valid. Token lifetime is ~3600s (1 hour)."""
        expires_at = session.get("expires_at", 0)
        # Leave 60s buffer
        return time.time() < (expires_at - 60)

    async def find_race(self, session: dict, track: str, race_number: int) -> dict | None:
        """Find a race by track name and number using nextToJumpRaces.
        Returns race info dict with race_id, meeting_id, track, number, etc."""
        brand_config = session["brand_config"]
        racing_host = brand_config["racing_host"]
        api_key = brand_config["cognito_client_id"]
        referer = brand_config["referer"]
        proxy_url = session.get("proxy_url")

        url = f"https://{racing_host}/query"
        headers = _racing_headers(api_key, referer)
        payload = {
            "query": NEXT_TO_JUMP_QUERY,
            "variables": {
                "first": 100,
                "raceType": ["THOROUGHBRED", "HARNESS", "GREYHOUND"],
                "country": ["AUS", "NZL"],
            },
        }

        async with AsyncSession(impersonate="chrome") as s:
            if proxy_url:
                s.proxies = {"http": proxy_url, "https": proxy_url}

            try:
                resp = await s.post(url, json=payload, headers=headers, timeout=15)
            except Exception as e:
                logger.error(f"BetMakers find_race request failed: {e}")
                return None

            if resp.status_code != 200:
                logger.error(f"BetMakers nextToJump failed: {resp.status_code} {resp.text[:300]}")
                return None

            data = resp.json()
            races = data.get("data", {}).get("nextToJumpRaces", [])

            for race in races:
                race_track = race.get("meeting", {}).get("track", {}).get("name", "")
                race_num = race.get("number", 0)
                if _names_match(race_track, track) and race_num == race_number:
                    meeting = race.get("meeting", {})
                    return {
                        "race_id": race["id"],
                        "meeting_id": meeting.get("id", ""),
                        "meeting_type": meeting.get("type", ""),
                        "track": race_track,
                        "race_number": race_num,
                        "race_name": race.get("name", ""),
                        "status": race.get("status", ""),
                        "start_at": race.get("start_at", ""),
                    }

            logger.warning(f"BetMakers: race not found - {track} R{race_number}")
            return None

    async def get_runners(self, session: dict, race_info: dict) -> list[dict]:
        """Get runners with fixed odds for a race using meetingRaces (raceCard).
        Returns list of runner dicts with id, name, tab_no, price, etc."""
        brand_config = session["brand_config"]
        racing_host = brand_config["racing_host"]
        api_key = brand_config["cognito_client_id"]
        referer = brand_config["referer"]
        proxy_url = session.get("proxy_url")
        product_keyword = brand_config.get("product_keyword", "TBF")

        url = f"https://{racing_host}/query"
        headers = _racing_headers(api_key, referer)
        payload = {
            "query": RACE_CARD_QUERY,
            "variables": {"meetingId": race_info["meeting_id"]},
        }

        async with AsyncSession(impersonate="chrome") as s:
            if proxy_url:
                s.proxies = {"http": proxy_url, "https": proxy_url}

            try:
                resp = await s.post(url, json=payload, headers=headers, timeout=15)
            except Exception as e:
                logger.error(f"BetMakers get_runners request failed: {e}")
                return []

            if resp.status_code != 200:
                logger.error(f"BetMakers raceCard failed: {resp.status_code} {resp.text[:300]}")
                return []

            data = resp.json()
            meetings = data.get("data", {}).get("meetingRaces", [])

            # Find the specific race within the meeting
            target_race_id = race_info["race_id"]
            for meeting in meetings:
                for race in meeting.get("races", []):
                    if race.get("id") == target_race_id:
                        runners = []
                        for r in race.get("runners", []):
                            if r.get("status") != "STARTER":
                                continue

                            # Find fixed odds win price for this brand
                            win_price = None
                            place_price = None
                            for p in r.get("prices", []):
                                kw = p.get("product", {}).get("keyword", "")
                                bt = p.get("bet_type", "")
                                if kw == product_keyword and bt == "win":
                                    win_price = p.get("price")
                                elif kw == product_keyword and bt == "place":
                                    place_price = p.get("price")

                            runners.append({
                                "id": r["id"],
                                "tab_no": r.get("tab_no", 0),
                                "name": r.get("name", ""),
                                "barrier": r.get("barrier", 0),
                                "jockey": r.get("jockey", ""),
                                "trainer": r.get("trainer", ""),
                                "win_price": win_price,
                                "place_price": place_price,
                            })
                        return runners

            logger.warning(f"BetMakers: race {target_race_id} not found in meeting data")
            return []

    async def place_bet(self, session: dict, race_info: dict, runner: dict,
                        stake: float, stake_type: str, brand_config: dict) -> dict:
        """Place a fixed-odds win bet via createBet mutation.
        stake is in DOLLARS, converted to CENTS internally."""
        access_token = session.get("access_token", "")
        platform_host = brand_config["platform_host"]
        api_key = brand_config["cognito_client_id"]
        referer = brand_config["referer"]
        proxy_url = session.get("proxy_url")
        product_keyword = brand_config.get("product_keyword", "TBF")

        stake_cents = int(round(stake * 100))
        price = runner.get("win_price", 0)
        race_id = race_info["race_id"]
        runner_id = runner["id"]

        url = f"https://{platform_host}/query"
        headers = _platform_headers(access_token, api_key, referer)

        bet_input = {
            "account_type": "Platform",
            "source": "Web",
            "ticket_id": str(uuid.uuid4()),
            "bets": [{
                "id": str(uuid.uuid4()),
                "bet_type": "win",
                "original_amount": stake_cents,
                "original_percentage": 0,
                "is_bonus_bet": stake_type in ("token", "promo"),
                "is_boxed": False,
                "currency": "AUD",
                "legs": [{
                    "leg_type": "Racing",
                    "bet_type_id": "win",
                    "product_keyword": product_keyword,
                    "event_id": race_id,
                    "selections": [{
                        "selection_id": [runner_id],
                        "selection_odds": str(price),
                        "position": 0,
                    }],
                }],
            }],
        }

        payload = {
            "query": CREATE_BET_MUTATION,
            "variables": {"input": bet_input},
        }

        async with AsyncSession(impersonate="chrome") as s:
            if proxy_url:
                s.proxies = {"http": proxy_url, "https": proxy_url}

            try:
                resp = await s.post(url, json=payload, headers=headers, timeout=15)
            except Exception as e:
                logger.error(f"BetMakers place_bet request failed: {e}")
                return {"success": False, "error": f"Request failed: {e}"}

            if resp.status_code != 200:
                logger.error(f"BetMakers createBet failed: {resp.status_code} {resp.text[:500]}")
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}

            data = resp.json()

            # Check for GraphQL errors
            gql_errors = data.get("errors", [])
            if gql_errors:
                error_msg = "; ".join(e.get("message", str(e)) for e in gql_errors)
                logger.error(f"BetMakers createBet GraphQL error: {error_msg}")
                return {"success": False, "error": error_msg}

            result = data.get("data", {}).get("createBet", {})
            bet_errors = result.get("errors", [])
            if bet_errors:
                error_msg = "; ".join(e.get("message", str(e)) for e in bet_errors)
                return {"success": False, "error": error_msg}

            bet_id = result.get("id", "")
            status = result.get("status", "")
            bets = result.get("bets", [])

            return {
                "success": status.lower() in ("accepted", "placed", "pending"),
                "bet_id": bet_id,
                "status": status,
                "bets": bets,
            }

    async def get_balance(self, session: dict) -> float:
        """Get account cash balance."""
        b = await self.get_balances(session)
        return b.get("cash", 0.0)

    async def get_balances(self, session: dict) -> dict:
        """Get cash + bonus balance. Returns {"cash": float, "bonus": float}."""
        access_token = session.get("access_token", "")
        brand_config = session["brand_config"]
        platform_host = brand_config["platform_host"]
        api_key = brand_config["cognito_client_id"]
        referer = brand_config["referer"]
        proxy_url = session.get("proxy_url")

        url = f"https://{platform_host}/query"
        headers = _platform_headers(access_token, api_key, referer)
        payload = {"query": BALANCE_QUERY}

        async with AsyncSession(impersonate="chrome") as s:
            try:
                resp = await s.post(url, json=payload, headers=headers, proxy=proxy_url, timeout=15)
            except Exception as e:
                logger.error(f"BetMakers get_balances failed: {e}")
                return {"cash": 0.0, "bonus": 0.0}

            if resp.status_code != 200:
                logger.error(f"BetMakers balance failed: {resp.status_code} {resp.text[:300]}")
                return {"cash": 0.0, "bonus": 0.0}

            data = resp.json()
            user_data = data.get("data", {}).get("getUser", {})
            if not user_data:
                logger.error(f"BetMakers balance: no getUser data: {data}")
                return {"cash": 0.0, "bonus": 0.0}

            cash_cents = user_data.get("account_balance", 0) or 0
            bonus_cents = user_data.get("bonus_account_balance", 0) or 0
            return {"cash": cash_cents / 100.0, "bonus": bonus_cents / 100.0}

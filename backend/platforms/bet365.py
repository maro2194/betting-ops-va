"""
bet365 platform client for multi-bookie framework.

bet365 is fully browser-automated — there's no REST API for betting.
Auth and bet placement go through the token farm (Mini PC Camoufox).

For the Telegram pick pipeline (bet365_service.py), the existing
standalone Bet365Service continues to work. This client is for the
unified multi-bookie framework only.
"""
import logging
import time

from .base import PlatformClient

logger = logging.getLogger(__name__)


class Bet365Client(PlatformClient):
    """bet365 platform client — delegates to token farm for browser operations."""

    async def login(self, email: str, password: str, proxy_url: str, brand_config: dict) -> dict:
        """Login to bet365 via token farm's Camoufox browser."""
        try:
            import token_farm_client
            result = await token_farm_client.bet365_login(email, password, proxy_url=proxy_url)
            if result.get("success"):
                return {
                    "success": True,
                    "platform": "bet365",
                    "brand": "bet365",
                    "session_id": result.get("session_id"),
                    "balance": result.get("balance"),
                    "username": email,
                    "expires_at": time.time() + 7200,  # bet365 sessions last hours
                }
            return result
        except Exception as e:
            logger.error(f"bet365 login error: {e}")
            return {"success": False, "error": f"bet365 login failed: {e}"}

    def is_session_valid(self, session: dict) -> bool:
        """Check if bet365 session is still valid."""
        expires_at = session.get("expires_at", 0)
        return time.time() < (expires_at - 300)

    async def find_race(self, session: dict, track: str, race_number: int) -> dict | None:
        """bet365 racing — not implemented in multi-bookie framework yet."""
        return None

    async def get_runners(self, session: dict, race_info: dict) -> list[dict]:
        """bet365 runners — not implemented."""
        return []

    async def place_bet(self, session: dict, race_info: dict, runner: dict,
                        stake: float, stake_type: str, brand_config: dict) -> dict:
        """Place a bet on bet365 via token farm browser."""
        session_id = session.get("session_id")
        if not session_id:
            return {"success": False, "error": "No active bet365 browser session"}

        try:
            import token_farm_client
            return await token_farm_client.bet365_place_bet(session_id, {
                "type": "racing",
                "track": race_info.get("track"),
                "race_number": race_info.get("race_number"),
                "runner_name": runner.get("name"),
                "runner_number": runner.get("number"),
                "stake": stake,
                "stake_type": stake_type,
            })
        except Exception as e:
            return {"success": False, "error": f"bet365 placement error: {e}"}

    async def place_sports_bet(self, session: dict, bet_payload: dict) -> dict:
        """Place a sports bet on bet365 via token farm browser automation.

        The token farm's Camoufox browser navigates to the event, finds the market,
        and places the bet. bet_payload contains event, bet_desc, sport, odds, stake.
        """
        session_id = session.get("session_id")
        if not session_id:
            return {"success": False, "error": "No active bet365 browser session"}

        try:
            import token_farm_client
            return await token_farm_client.bet365_place_bet(session_id, {
                "type": "sports",
                "sport": bet_payload.get("sport", ""),
                "event": bet_payload.get("event", ""),
                "bet_desc": bet_payload.get("bet_desc", ""),
                "odds": bet_payload.get("odds", 0),
                "min_odds": bet_payload.get("min_odds", 0),
                "stake": bet_payload.get("stake", 0),
                "stake_type": bet_payload.get("stake_type", "cash"),
            })
        except Exception as e:
            return {"success": False, "error": f"bet365 sports placement error: {e}"}

    async def get_balances(self, session: dict) -> dict:
        """Get cash + bonus balances for multi-bookie framework."""
        bal = await self.get_balance(session)
        return {"cash": bal, "bonus": 0.0}

    async def get_balance(self, session: dict) -> float:
        """Get bet365 balance from session data."""
        return float(session.get("balance", 0))

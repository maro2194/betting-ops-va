"""
Abstract base class defining the platform client interface.
All bookie platform clients (BetMakers, Amused, TAB) must implement this.
"""
from abc import ABC, abstractmethod


class PlatformClient(ABC):
    """Abstract base for all bookie platform clients."""

    @abstractmethod
    async def login(self, email: str, password: str, proxy_url: str, brand_config: dict) -> dict:
        """Login and return session dict with tokens, expires_at, etc."""

    @abstractmethod
    def is_session_valid(self, session: dict) -> bool:
        """Check if session tokens are still valid."""

    @abstractmethod
    async def find_race(self, session: dict, track: str, race_number: int) -> dict | None:
        """Find a race by track name and number. Returns race info dict or None."""

    @abstractmethod
    async def get_runners(self, session: dict, race_info: dict) -> list[dict]:
        """Get runners with odds for a race. Returns list of runner dicts."""

    @abstractmethod
    async def place_bet(self, session: dict, race_info: dict, runner: dict,
                        stake: float, stake_type: str, brand_config: dict) -> dict:
        """Place a bet. Returns result dict with status, bet_id, error, etc."""

    @abstractmethod
    async def get_balance(self, session: dict) -> float:
        """Get account cash balance."""

"""Multi-platform session cache with thread-safe login and expiry management."""
import asyncio
import logging
import random
import time

from platforms.registry import get_client

logger = logging.getLogger(__name__)

# Session validity buffers (seconds before expiry to consider invalid)
VALIDITY_BUFFERS = {
    "tab": 300,        # 5 min buffer
    "betmakers": 120,  # 2 min buffer
    "amused": 60,      # 1 min buffer (tokens only last 300s!)
    "sportsbet": 120,  # 2 min buffer
    "bet365": 300,     # 5 min buffer (sessions last ~2hrs)
}


def _session_key(account: dict) -> str:
    """Build a unique session key from account info."""
    return f"{account['platform']}:{account['brand']}:{account['initials']}"


def _generate_proxy(proxy_base: str) -> str:
    """Generate a unique Oxylabs sticky session proxy URL."""
    if not proxy_base:
        return ""
    sess_id = str(random.randint(1000000000, 9999999999))
    return f"{proxy_base}-sessid-{sess_id}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"


class SessionManager:
    """Thread-safe multi-platform session cache."""

    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for a session key."""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get_or_create(self, account: dict) -> dict:
        """Get cached session or login. Thread-safe per account."""
        key = _session_key(account)
        lock = self._get_lock(key)

        async with lock:
            if key in self._sessions and self.is_valid(key):
                return self._sessions[key]

            # Login required
            session = await self._login(account)
            if session.get("success"):
                self._sessions[key] = session
            return session

    def is_valid(self, session_key: str) -> bool:
        """Check if a cached session is still valid based on platform-specific buffers."""
        session = self._sessions.get(session_key)
        if not session:
            return False

        expires_at = session.get("expires_at", 0)
        platform = session.get("platform", "betmakers")
        buffer = VALIDITY_BUFFERS.get(platform, 120)
        return time.time() < (expires_at - buffer)

    async def ensure_valid(self, account: dict) -> dict:
        """Re-login if expired. CRITICAL for Amused (300s tokens)."""
        key = _session_key(account)
        lock = self._get_lock(key)

        async with lock:
            if key in self._sessions and self.is_valid(key):
                return self._sessions[key]

            # Session expired or missing — re-login
            logger.info(f"Session expired for {key}, re-logging in")
            session = await self._login(account)
            if session.get("success"):
                self._sessions[key] = session
            return session

    async def invalidate(self, session_key: str):
        """Remove a cached session."""
        self._sessions.pop(session_key, None)

    async def _login(self, account: dict) -> dict:
        """Perform platform login for an account."""
        platform = account["platform"]
        brand = account["brand"]

        if platform == "tab":
            # TAB uses its own login module — not handled here yet
            return {"success": False, "error": "TAB login not yet implemented in session manager"}

        # Sportsbet: try token farm for browser login, refresh_token for fast path
        if platform == "sportsbet":
            return await self._login_sportsbet(account)

        try:
            client = get_client(platform)
        except ValueError as e:
            return {"success": False, "error": str(e)}

        proxy_url = _generate_proxy(account.get("proxy_base", ""))

        # Look up brand config from platform registry (user DB doesn't store this)
        brand_config = {}
        if platform == "betmakers":
            from platforms.betmakers import BETMAKERS_BRANDS
            brand_config = BETMAKERS_BRANDS.get(brand, {}) or {}
        elif platform == "amused":
            from platforms.amused import AMUSED_BRANDS
            brand_config = AMUSED_BRANDS.get(brand, {}) or {}
        # Merge any user-level overrides from DB
        brand_config = {**brand_config, **account.get("brand_config", {})}

        try:
            session = await client.login(
                email=account["email"],
                password=account["password"],
                proxy_url=proxy_url,
                brand_config=brand_config,
            )
        except Exception as e:
            logger.error(f"Login failed for {_session_key(account)}: {e}")
            return {"success": False, "error": f"Login exception: {e}"}

        if session.get("success"):
            session["platform"] = platform
            session["brand"] = brand
            session["initials"] = account.get("initials", "")
            session["account_id"] = account.get("id", "")

        return session


    async def _login_sportsbet(self, account: dict) -> dict:
        """Login to Sportsbet: try refresh first, then token farm browser login."""
        from platforms.sportsbet import SportsbetClient

        client = SportsbetClient()
        email = account["email"]
        password = account["password"]
        proxy_url = _generate_proxy(account.get("proxy_base", ""))

        # Build brand_config with any existing refresh_token
        brand_config = account.get("brand_config", {})

        # Try refresh_token first (fast, no browser)
        refresh_token = brand_config.get("refresh_token")
        if refresh_token:
            session = await client._try_refresh(
                brand_config.get("base_url", "https://www.sportsbet.com.au"),
                refresh_token, proxy_url,
            )
            if session and session.get("success"):
                session["platform"] = "sportsbet"
                session["brand"] = "sportsbet"
                session["initials"] = account.get("initials", "")
                session["account_id"] = account.get("id", "")
                return session

        # Try token farm (browser login on mini PC)
        try:
            import token_farm_client
            farm_result = await token_farm_client.sportsbet_login(email, password)
            if farm_result.get("success"):
                farm_result["platform"] = "sportsbet"
                farm_result["brand"] = "sportsbet"
                farm_result["initials"] = account.get("initials", "")
                farm_result["account_id"] = account.get("id", "")
                return farm_result
            logger.warning(f"Token farm SB login failed: {farm_result.get('error')}")
        except Exception as e:
            logger.warning(f"Token farm unavailable, trying local browser: {e}")

        # Fallback: local browser login (if patchright available)
        session = await client.login(email, password, proxy_url, brand_config)
        if session.get("success"):
            session["platform"] = "sportsbet"
            session["brand"] = "sportsbet"
            session["initials"] = account.get("initials", "")
            session["account_id"] = account.get("id", "")
        return session


# Singleton instance
session_manager = SessionManager()

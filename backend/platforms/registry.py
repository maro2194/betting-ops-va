"""
Bookmaker-to-platform routing registry.
Maps bookmaker names to their platform type and brand identifier.
"""
import logging

from .base import PlatformClient
from .betmakers import BetMakersClient
from .amused import AmusedClient
from .sportsbet import SportsbetClient
from .bet365 import Bet365Client
from .tab import TabClient
from .pointsbet import PointsbetClient
from .ladbrokes import EntainClient

logger = logging.getLogger(__name__)

# ─── Bookmaker -> (platform, brand) mapping ─────────────────────────────────

BOOKMAKER_PLATFORM_MAP: dict[str, tuple[str, str]] = {
    # TAB (native platform)
    "tab": ("tab", "tab"),
    # BetMakers/Apollo brands
    "crownbet": ("betmakers", "crownbet"),
    "diamondbet": ("betmakers", "diamondbet"),
    "betdash": ("betmakers", "betdash"),
    "terrybet": ("betmakers", "terrybet"),
    "ponybet": ("betmakers", "ponybet"),
    "betit": ("betmakers", "betit"),
    "swiftbet": ("betmakers", "swiftbet"),
    # Amused/BlackStream brands
    "betnation": ("amused", "betnation"),
    "betdeluxe": ("amused", "betdeluxe"),
    "surge": ("amused", "surge"),
    "pulsebet": ("amused", "pulsebet"),
    "bigbet": ("amused", "bigbet"),
    "yesbet": ("amused", "yesbet"),
    "mightybet": ("amused", "mightybet"),
    # Sportsbet (own platform, browser-auth via token farm)
    "sportsbet": ("sportsbet", "sportsbet"),
    # bet365 (browser-automated via token farm)
    "bet365": ("bet365", "bet365"),
    # PointsBet (own platform, browser-auth via token farm)
    "pointsbet": ("pointsbet", "pointsbet"),
    # Entain v2 brands (Ladbrokes + Neds — same platform, different base URLs)
    "ladbrokes": ("ladbrokes", "ladbrokes"),
    "neds": ("ladbrokes", "neds"),
}

# Unsupported (yet)
UNSUPPORTED_BOOKMAKERS: set[str] = set()

# Singleton client instances
_clients: dict[str, PlatformClient] = {}


def normalize_bookmaker(name: str) -> str:
    """Normalize a bookmaker name: lowercase, strip spaces."""
    return name.lower().strip().replace(" ", "")


def get_platform_and_brand(bookmaker: str) -> tuple[str, str] | None:
    """Look up the platform type and brand for a bookmaker.
    Returns (platform, brand) tuple or None if not found."""
    key = normalize_bookmaker(bookmaker)
    return BOOKMAKER_PLATFORM_MAP.get(key)


def get_client(platform: str) -> PlatformClient:
    """Get or create a platform client instance."""
    if platform not in _clients:
        if platform == "betmakers":
            _clients[platform] = BetMakersClient()
        elif platform == "amused":
            _clients[platform] = AmusedClient()
        elif platform == "sportsbet":
            _clients[platform] = SportsbetClient()
        elif platform == "bet365":
            _clients[platform] = Bet365Client()
        elif platform == "tab":
            _clients[platform] = TabClient()
        elif platform == "pointsbet":
            _clients[platform] = PointsbetClient()
        elif platform == "ladbrokes":
            _clients[platform] = EntainClient()
        else:
            raise ValueError(f"Unknown platform: {platform}")
    return _clients[platform]


def is_supported(bookmaker: str) -> bool:
    """Check if a bookmaker is supported (has a platform mapping)."""
    key = normalize_bookmaker(bookmaker)
    if key in UNSUPPORTED_BOOKMAKERS:
        return False
    return key in BOOKMAKER_PLATFORM_MAP

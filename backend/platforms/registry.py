"""
Bookmaker-to-platform routing registry.
Maps bookmaker names to their platform type and brand identifier.
"""
import logging

from .base import PlatformClient
from .betmakers import BetMakersClient
from .amused import AmusedClient
from .sportsbet import SportsbetClient

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
    # Sportsbet
    "sportsbet": ("sportsbet", "sportsbet"),
    # Amused/BlackStream brands
    "betnation": ("amused", "betnation"),
    "betdeluxe": ("amused", "betdeluxe"),
    "surge": ("amused", "surge"),
    "pulsebet": ("amused", "pulsebet"),
    "bigbet": ("amused", "bigbet"),
    "yesbet": ("amused", "yesbet"),
    "mightybet": ("amused", "mightybet"),
}

# Unsupported (yet)
UNSUPPORTED_BOOKMAKERS = {"neds", "ladbrokes", "bet365", "pointsbet"}

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
    """Get or create a platform client instance.
    Platform must be 'betmakers' or 'amused'. TAB uses its own login/betting modules."""
    if platform not in _clients:
        if platform == "betmakers":
            _clients[platform] = BetMakersClient()
        elif platform == "amused":
            _clients[platform] = AmusedClient()
        elif platform == "sportsbet":
            _clients[platform] = SportsbetClient()
        else:
            raise ValueError(f"Unknown platform: {platform}. Use 'betmakers', 'amused', or 'sportsbet'.")
    return _clients[platform]


def is_supported(bookmaker: str) -> bool:
    """Check if a bookmaker is supported (has a platform mapping)."""
    key = normalize_bookmaker(bookmaker)
    if key in UNSUPPORTED_BOOKMAKERS:
        return False
    return key in BOOKMAKER_PLATFORM_MAP

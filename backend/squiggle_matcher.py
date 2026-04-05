"""
Centralised AFL/NRL team name resolver using Squiggle API.
Single source of truth for mapping bookie-specific team names to canonical IDs.

Usage:
    from squiggle_matcher import matcher
    team = matcher.resolve("GWS")           # -> "Greater Western Sydney"
    game = await matcher.find_game("Brs-Col", round_num=3)
    teams = await matcher.get_teams()       # -> list of Squiggle team dicts
"""
import logging
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SQUIGGLE_BASE = "https://api.squiggle.com.au/"
SQUIGGLE_UA = "BotOps/1.0 (contact@botops.sh)"
CACHE_TTL = 3600  # 1 hour for team data (rarely changes)
GAME_CACHE_TTL = 300  # 5 min for game data

# ─── Canonical AFL Team Aliases ──────────────────────────────────────────────
# Key = Squiggle canonical name (lowercase for matching)
# Values = all known aliases across TAB, Sportsbet, BetMakers, Amused, bet365, Squiggle

AFL_ALIASES: dict[str, list[str]] = {
    "adelaide": [
        "adelaide", "adelaide crows", "crows", "ade", "adel", "adl",
    ],
    "brisbane lions": [
        "brisbane", "brisbane lions", "lions", "bris", "brl", "brs",
    ],
    "carlton": [
        "carlton", "carlton blues", "blues", "carl", "car", "crl",
    ],
    "collingwood": [
        "collingwood", "collingwood magpies", "magpies", "pies", "col",
    ],
    "essendon": [
        "essendon", "essendon bombers", "bombers", "ess", "dons",
    ],
    "fremantle": [
        "fremantle", "fremantle dockers", "dockers", "fre", "freo",
    ],
    "geelong": [
        "geelong", "geelong cats", "cats", "gee", "gel",
    ],
    "gold coast": [
        "gold coast", "gold coast suns", "suns", "gc", "gcs", "gcfc",
    ],
    "greater western sydney": [
        "greater western sydney", "gws giants", "gws", "giants",
    ],
    "hawthorn": [
        "hawthorn", "hawthorn hawks", "hawks", "haw",
    ],
    "melbourne": [
        "melbourne", "melbourne demons", "demons", "mel", "melb",
    ],
    "north melbourne": [
        "north melbourne", "north melbourne kangaroos", "kangaroos", "roos",
        "north", "nth", "nmc", "nmfc", "nme", "nm",
    ],
    "port adelaide": [
        "port adelaide", "port adelaide power", "power", "port", "pa", "por",
    ],
    "richmond": [
        "richmond", "richmond tigers", "tigers", "ric", "rich",
    ],
    "st kilda": [
        "st kilda", "st kilda saints", "saints", "stk", "stkilda",
    ],
    "sydney": [
        "sydney", "sydney swans", "swans", "syd",
    ],
    "west coast": [
        "west coast", "west coast eagles", "eagles", "wce", "wc",
    ],
    "western bulldogs": [
        "western bulldogs", "bulldogs", "dogs", "footscray", "wb", "wbd", "wbg",
    ],
}

# Build reverse lookup: normalised alias -> canonical name
_ALIAS_LOOKUP: dict[str, str] = {}
for canonical, aliases in AFL_ALIASES.items():
    for alias in aliases:
        _ALIAS_LOOKUP[re.sub(r'[^a-z0-9]', '', alias.lower())] = canonical


class SquiggleMatcher:
    """Cached Squiggle API client with team name resolution."""

    def __init__(self):
        self._teams_cache: Optional[tuple[float, list[dict]]] = None
        self._games_cache: dict[str, tuple[float, list[dict]]] = {}

    # ─── Team Resolution ─────────────────────────────────────────────────

    def resolve(self, name: str) -> Optional[str]:
        """Resolve any team name/code to Squiggle canonical name.

        Returns canonical name (e.g. "Greater Western Sydney") or None.
        """
        if not name:
            return None
        norm = re.sub(r'[^a-z0-9]', '', name.lower())

        # Direct alias match
        if norm in _ALIAS_LOOKUP:
            return _ALIAS_LOOKUP[norm].title()

        # Substring match (e.g. "brisbanelions" contains "brisbane") — prefer longest match
        matches = [(alias_norm, canonical) for alias_norm, canonical in _ALIAS_LOOKUP.items()
                   if len(alias_norm) >= 4 and alias_norm in norm]
        if matches:
            best = max(matches, key=lambda x: len(x[0]))
            return best[1].title()

        # Reverse: check if input is in any canonical name
        for canonical in AFL_ALIASES:
            if norm in re.sub(r'[^a-z0-9]', '', canonical):
                return canonical.title()

        return None

    def resolve_pair(self, teams_str: str) -> tuple[Optional[str], Optional[str]]:
        """Resolve a 'TeamA-TeamB' or 'TeamA v TeamB' string to canonical pair."""
        sep = None
        for s in [" v ", " vs ", "-", " - "]:
            if s in teams_str:
                sep = s
                break
        if not sep:
            return None, None

        parts = teams_str.split(sep, 1)
        if len(parts) != 2:
            return None, None

        return self.resolve(parts[0].strip()), self.resolve(parts[1].strip())

    # ─── Squiggle API ────────────────────────────────────────────────────

    async def get_teams(self, client: Optional[httpx.AsyncClient] = None) -> list[dict]:
        """Fetch all Squiggle teams (cached 1 hour)."""
        if self._teams_cache:
            ts, data = self._teams_cache
            if time.time() - ts < CACHE_TTL:
                return data

        own_client = client is None
        if own_client:
            client = httpx.AsyncClient()

        try:
            resp = await client.get(
                SQUIGGLE_BASE,
                params={"q": "teams"},
                headers={"User-Agent": SQUIGGLE_UA},
                timeout=15,
            )
            resp.raise_for_status()
            teams = resp.json().get("teams", [])
            self._teams_cache = (time.time(), teams)
            return teams
        except Exception as e:
            logger.error(f"Squiggle teams fetch failed: {e}")
            return self._teams_cache[1] if self._teams_cache else []
        finally:
            if own_client:
                await client.aclose()

    async def get_games(
        self,
        year: int = 2026,
        round_num: Optional[int] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> list[dict]:
        """Fetch Squiggle games for a year/round (cached 5 min)."""
        cache_key = f"games:{year}:{round_num}"
        if cache_key in self._games_cache:
            ts, data = self._games_cache[cache_key]
            if time.time() - ts < GAME_CACHE_TTL:
                return data

        params = {"q": f"games;year={year}"}
        if round_num:
            params["q"] += f";round={round_num}"

        own_client = client is None
        if own_client:
            client = httpx.AsyncClient()

        try:
            resp = await client.get(
                SQUIGGLE_BASE,
                params=params,
                headers={"User-Agent": SQUIGGLE_UA},
                timeout=15,
            )
            resp.raise_for_status()
            games = resp.json().get("games", [])
            self._games_cache[cache_key] = (time.time(), games)
            return games
        except Exception as e:
            logger.error(f"Squiggle games fetch failed: {e}")
            cached = self._games_cache.get(cache_key)
            return cached[1] if cached else []
        finally:
            if own_client:
                await client.aclose()

    async def find_game(
        self,
        teams_str: str,
        round_num: int,
        year: int = 2026,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Optional[dict]:
        """Find a Squiggle game by teams string (e.g. 'Brs-Col').

        Searches given round and +/- 1 for robustness.
        """
        team_a, team_b = self.resolve_pair(teams_str)
        if not team_a or not team_b:
            return None

        norm_a = re.sub(r'[^a-z]', '', team_a.lower())
        norm_b = re.sub(r'[^a-z]', '', team_b.lower())

        for rnd in [round_num, round_num - 1, round_num + 1]:
            if rnd < 1:
                continue
            games = await self.get_games(year=year, round_num=rnd, client=client)
            for game in games:
                h = re.sub(r'[^a-z]', '', game.get("hteam", "").lower())
                a = re.sub(r'[^a-z]', '', game.get("ateam", "").lower())
                if (norm_a in h and norm_b in a) or (norm_a in a and norm_b in h):
                    return game

        return None

    async def get_current_round(
        self, year: int = 2026, client: Optional[httpx.AsyncClient] = None
    ) -> int:
        """Get current AFL round number from Squiggle."""
        games = await self.get_games(year=year, client=client)
        if not games:
            return 1

        rounds_played = set()
        rounds_upcoming = set()
        for g in games:
            rnd = int(g.get("round", 0))
            if g.get("hscore") is not None or g.get("ascore") is not None:
                rounds_played.add(rnd)
            else:
                rounds_upcoming.add(rnd)

        if rounds_played:
            return max(rounds_played)
        if rounds_upcoming:
            return min(rounds_upcoming)
        return 1


# Singleton
matcher = SquiggleMatcher()

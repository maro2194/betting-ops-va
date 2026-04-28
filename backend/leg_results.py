"""
Per-leg result tracking using external sports stats APIs.

Supports:
  - AFL: Squiggle API (free, no auth, requires User-Agent header)
  - NBA: balldontlie API v1 (free tier, no key required for basic stats)
"""
import asyncio
import logging
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SQUIGGLE_UA = "BotOps/1.0 (contact@botops.sh)"
SQUIGGLE_BASE = "https://api.squiggle.com.au/"
ESPN_NBA_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

# In-process cache: key -> (timestamp, data)
_cache: dict[str, tuple[float, any]] = {}
CACHE_TTL = 300  # 5 minutes


def _cached_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() - entry[0] < CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, value):
    _cache[key] = (time.time(), value)


# ─── Leg Name Parser ─────────────────────────────────────────────────────────

# Stat keyword map: token (lowercase) → canonical stat name
AFL_STAT_MAP = {
    "disps": "disposals",
    "disposal": "disposals",
    "disposals": "disposals",
    "goals": "goals",
    "goal": "goals",
    "marks": "marks",
    "mark": "marks",
    "tackles": "tackles",
    "tackle": "tackles",
    "kicks": "kicks",
    "kick": "kicks",
    "handballs": "handballs",
    "handball": "handballs",
    "hitouts": "hitouts",
    "hitout": "hitouts",
    "clearances": "clearances",
    "clearance": "clearances",
    "contestedpossessions": "contested_possessions",
    "freeskicks": "frees_for",
    "inside50s": "inside_50s",
}

NBA_STAT_MAP = {
    "pts": "pts",
    "points": "pts",
    "point": "pts",
    "reb": "reb",
    "rebs": "reb",
    "rebounds": "reb",
    "rebound": "reb",
    "ast": "ast",
    "asts": "ast",
    "assists": "ast",
    "assist": "ast",
    "stl": "stl",
    "steals": "stl",
    "steal": "stl",
    "blk": "blk",
    "blocks": "blk",
    "block": "blk",
    "3": "fg3m",
    "3s": "fg3m",
    "3pm": "fg3m",
    "3pt": "fg3m",
    "3pts": "fg3m",
    "3pointers": "fg3m",
    "3-pointers": "fg3m",
    "threepointers": "fg3m",
    "threes": "fg3m",
    "fg3m": "fg3m",
    "blks": "blk",
    "pts+reb+ast": "pts_reb_ast",
    "pts+ast": "pts_ast",
    "pts+reb": "pts_reb",
    "reb+ast": "reb_ast",
    "stl+blk": "stl_blk",
}


def parse_leg_name(name: str) -> Optional[dict]:
    """
    Parse a leg name into structured fields.

    Handles formats:
      "AFL Brs-Col 15Disps Harry Perryman (COL)"
      "NBA Chi-Ind 10+Pts Jay Huff (IND)"
      "AFL Melb-GWS 2Goals Isaac Heeney (GWS)"

    Returns dict or None if unparseable.
    """
    if not name or not isinstance(name, str):
        return None

    # Normalise whitespace and strip apostrophes (both ASCII and curly) — TAB
    # market names like "10+ 3's" would otherwise fail the [A-Za-z0-9+\-]+ regex
    # and silently drop from the results table.
    name = name.strip().replace("’", "").replace("'", "")

    # Pattern: SPORT TEAMS LINE+STAT PLAYER_NAME (TEAM)
    # e.g. "AFL Brs-Col 15Disps Harry Perryman (COL)"
    #       sport     teams  line stat  player        team_abbr
    pattern = re.compile(
        r'^(?P<sport>AFL|NBA|NRL)\s+'
        r'(?P<teams>[A-Za-z0-9]+-[A-Za-z0-9]+)\s+'
        r'(?P<line>\d+(?:\.\d+)?)\+?'
        r'(?P<stat_raw>[A-Za-z0-9+\-]+)\s+'
        r'(?P<player>.+?)\s+'
        r'\((?P<team>[A-Z0-9]+)\)',
        re.IGNORECASE,
    )

    m = pattern.match(name)
    if not m:
        # Try without team abbr at end
        pattern2 = re.compile(
            r'^(?P<sport>AFL|NBA|NRL)\s+'
            r'(?P<teams>[A-Za-z0-9]+-[A-Za-z0-9]+)\s+'
            r'(?P<line>\d+(?:\.\d+)?)\+?'
            r'(?P<stat_raw>[A-Za-z0-9+\-]+)\s+'
            r'(?P<player>.+?)$',
            re.IGNORECASE,
        )
        m = pattern2.match(name)
        if not m:
            # Fallback: try "SPORT TEAMS ... Over/Under LINE STAT" format
            # e.g. "NBA Den-Mem Ced Coward PtOU Ced Coward Over 14.5 Pts"
            pattern3 = re.compile(
                r'^(?P<sport>AFL|NBA|NRL)\s+'
                r'(?P<teams>[A-Za-z0-9]+-[A-Za-z0-9]+)\s+'
                r'.*?(?:Over|Under)\s+(?P<line>\d+(?:\.\d+)?)\s*'
                r'(?P<stat_raw>[A-Za-z]+)',
                re.IGNORECASE,
            )
            m3 = pattern3.match(name)
            if m3:
                # Extract player name from between teams and Over/Under
                after_teams = name[m3.start("teams") + len(m3.group("teams")):].strip()
                player_match = re.match(r'(.+?)(?:PtOU|DbDb|Over|Under)', after_teams, re.IGNORECASE)
                player = player_match.group(1).strip() if player_match else "Unknown"
                sport = m3.group("sport").upper()
                stat_raw = m3.group("stat_raw").lower()
                stat = NBA_STAT_MAP.get(stat_raw, stat_raw) if sport == "NBA" else AFL_STAT_MAP.get(stat_raw, stat_raw)
                return {
                    "sport": sport,
                    "teams": m3.group("teams"),
                    "line": float(m3.group("line")),
                    "stat": stat,
                    "player": player,
                    "team": "",
                    "raw": name,
                }
            # V2 engine format: "Charlie Ballard (GC) — 15+ Disposals"
            # or "Tom McCartin (SYD) — 15+ Disposals"
            pattern_v2 = re.compile(
                r'^(?P<player>.+?)\s*'
                r'\((?P<team>[A-Z0-9]+)\)\s*'
                r'[—\-]+\s*'
                r'(?P<line>\d+(?:\.\d+)?)\+?\s*'
                r'(?P<stat_raw>[A-Za-z0-9\s]+?)$',
                re.IGNORECASE,
            )
            m_v2 = pattern_v2.match(name)
            if m_v2:
                player = m_v2.group("player").strip()
                team = m_v2.group("team").upper()
                line = float(m_v2.group("line"))
                stat_raw = m_v2.group("stat_raw").strip().lower().replace(" ", "")
                # Detect sport from stat type
                sport = "AFL" if stat_raw in ("disposals", "goals", "marks", "tackles") else "NBA"
                stat = AFL_STAT_MAP.get(stat_raw, stat_raw) if sport == "AFL" else NBA_STAT_MAP.get(stat_raw, stat_raw)
                return {
                    "sport": sport,
                    "teams": "",  # V2 doesn't have teams code — will be resolved by team abbr
                    "line": line,
                    "stat": stat,
                    "player": player,
                    "team": team,
                    "raw": name,
                }

            # V2 old format (unmigrated): "Player Name 15+ Disposals" (no team, no dash)
            pattern_v2_simple = re.compile(
                r'^(?P<player>.+?)\s+'
                r'(?P<line>\d+(?:\.\d+)?)\+?\s*'
                r'(?P<stat_raw>[A-Za-z0-9\s]+?)$',
                re.IGNORECASE,
            )
            m_v2s = pattern_v2_simple.match(name)
            if m_v2s:
                player = m_v2s.group("player").strip()
                line = float(m_v2s.group("line"))
                stat_raw = m_v2s.group("stat_raw").strip().lower().replace(" ", "")
                sport = "AFL" if stat_raw in ("disposals", "goals", "marks", "tackles") else "NBA"
                stat = AFL_STAT_MAP.get(stat_raw, stat_raw) if sport == "AFL" else NBA_STAT_MAP.get(stat_raw, stat_raw)
                return {
                    "sport": sport,
                    "teams": "",
                    "line": line,
                    "stat": stat,
                    "player": player,
                    "team": "",
                    "raw": name,
                }

            logger.warning(f"parse_leg_name: could not parse '{name}'")
            return None

    sport = m.group("sport").upper()
    teams = m.group("teams")
    line = float(m.group("line"))
    stat_raw = m.group("stat_raw").lower().replace(" ", "")
    player = m.group("player").strip()
    team = m.groupdict().get("team", "").upper()

    # Resolve stat name
    if sport == "AFL":
        stat = AFL_STAT_MAP.get(stat_raw, stat_raw)
    elif sport == "NBA":
        stat = NBA_STAT_MAP.get(stat_raw, stat_raw)
    else:
        stat = stat_raw

    return {
        "sport": sport,
        "teams": teams,
        "line": line,
        "stat": stat,
        "stat_raw": stat_raw,
        "player": player,
        "team": team,
        "name": name,
    }


# ─── AFL via Squiggle ─────────────────────────────────────────────────────────

async def _squiggle_get(client: httpx.AsyncClient, params: dict) -> dict:
    """Make a request to Squiggle API with caching.

    IMPORTANT: Squiggle uses semicolons in query params (e.g. q=games;year=2026;round=4).
    httpx's params dict URL-encodes semicolons to %3B which breaks the API.
    We must build the URL manually with raw query string.
    """
    cache_key = "squiggle:" + str(sorted(params.items()))
    cached = _cached_get(cache_key)
    if cached is not None:
        return cached

    try:
        # Build URL with raw semicolons (not URL-encoded)
        q = params.get("q", "")
        url = f"{SQUIGGLE_BASE}?q={q}"
        resp = await client.get(
            url,
            headers={"User-Agent": SQUIGGLE_UA},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _cache_set(cache_key, data)
        return data
    except Exception as e:
        logger.error(f"Squiggle API error: {e}")
        return {}


async def _get_afl_current_round(client: httpx.AsyncClient) -> int:
    """Get the current AFL round number.

    Finds the round that has in-progress games, or the most recent round
    with incomplete games (complete < 100).
    """
    cache_key = "squiggle:current_round"
    cached = _cached_get(cache_key)
    if cached is not None:
        return cached

    games_data = await _squiggle_get(client, {"q": "games;year=2026"})
    games = games_data.get("games", [])
    if not games:
        return 1

    # Find round with in-progress games first (0 < complete < 100)
    in_progress_rounds = set()
    latest_complete_round = 0
    for g in games:
        rnd = int(g.get("round", 0))
        complete = g.get("complete", 0)
        if complete is not None and 0 < complete < 100:
            in_progress_rounds.add(rnd)
        if complete == 100 and rnd > latest_complete_round:
            latest_complete_round = rnd

    if in_progress_rounds:
        current = min(in_progress_rounds)  # earliest in-progress round
    elif latest_complete_round:
        current = latest_complete_round
    else:
        current = 1

    _cache_set(cache_key, current)
    return current


def _normalize_team(name: str) -> str:
    """Normalise team name for fuzzy matching."""
    return re.sub(r'[^a-z]', '', (name or "").lower())


# Short code → full name fragments (for matching Squiggle team names)
AFL_TEAM_CODES = {
    "ade": "adelaide",
    "adel": "adelaide",
    "adl": "adelaide",
    "bris": "brisbane",
    "brl": "brisbane",
    "brs": "brisbane",
    "carl": "carlton",
    "car": "carlton",
    "crl": "carlton",
    "col": "collingwood",
    "ess": "essendon",
    "fre": "fremantle",
    "frm": "fremantle",
    "gc": "gold coast",
    "gcs": "gold coast",
    "gld": "gold coast",
    "gws": "greater western sydney",
    "haw": "hawthorn",
    "melb": "melbourne",
    "mel": "melbourne",
    "nth": "north melbourne",
    "nmc": "north melbourne",
    "nmfc": "north melbourne",
    "nm": "north melbourne",
    "pa": "port adelaide",
    "por": "port adelaide",
    "prt": "port adelaide",
    "rich": "richmond",
    "ric": "richmond",
    "stk": "st kilda",
    "sk": "st kilda",
    "syd": "sydney",
    "wb": "western bulldogs",
    "wbd": "western bulldogs",
    "wbg": "western bulldogs",
    "wce": "west coast",
    "wc": "west coast",
}


def _team_code_to_fragment(code: str) -> str:
    return AFL_TEAM_CODES.get(code.lower(), code.lower())


def _teams_match(squiggle_name: str, code: str) -> bool:
    """Check if a Squiggle team name matches a short code."""
    sq = _normalize_team(squiggle_name)
    fragment = _normalize_team(_team_code_to_fragment(code))
    # Also try the code itself
    code_norm = _normalize_team(code)
    return fragment in sq or code_norm in sq or sq.startswith(code_norm[:3])


async def _find_afl_game(client: httpx.AsyncClient, teams_str: str, round_num: int) -> Optional[dict]:
    """
    Find an AFL game by teams string like "Brs-Col".
    Searches given round_num and round_num-1 for robustness.
    Returns Squiggle game dict or None.
    """
    parts = teams_str.split("-")
    if len(parts) != 2:
        return None
    code_a, code_b = parts[0].strip(), parts[1].strip()

    for rnd in [round_num, round_num - 1, round_num + 1, round_num - 2, round_num + 2, round_num - 3, round_num + 3]:
        if rnd < 1:
            continue
        data = await _squiggle_get(client, {"q": f"games;year=2026;round={rnd}"})
        for game in data.get("games", []):
            hteam = game.get("hteam", "")
            ateam = game.get("ateam", "")
            if (
                (_teams_match(hteam, code_a) and _teams_match(ateam, code_b)) or
                (_teams_match(hteam, code_b) and _teams_match(ateam, code_a))
            ):
                return game

    return None


async def _get_afl_player_stats(client: httpx.AsyncClient, game_id: int, round_num: int) -> list[dict]:
    """Get player stats for an AFL game."""
    cache_key = f"squiggle:players:gid={game_id}"
    cached = _cached_get(cache_key)
    if cached is not None:
        return cached

    data = await _squiggle_get(client, {
        "q": f"players;year=2026;round={round_num};gid={game_id}",
    })
    players = data.get("players", [])
    _cache_set(cache_key, players)
    return players


def _normalize_player_name(name: str) -> str:
    """Normalise player name for fuzzy matching."""
    return re.sub(r'[^a-z]', '', name.lower())


def _find_player(players: list[dict], player_name: str) -> Optional[dict]:
    """Find a player by name with fuzzy matching.
    Handles abbreviated names (Nickeil A-Walker vs Nickeil Alexander-Walker),
    aliases (SGA vs Shai Gilgeous-Alexander), and partial matches."""
    # Try alias expansion first
    try:
        from resolver import _expand_alias
        player_name = _expand_alias(player_name)
    except ImportError:
        pass

    target = _normalize_player_name(player_name)
    # Token-based: split on spaces AND hyphens BEFORE normalizing to preserve word boundaries
    import re as _re
    target_tokens = [_normalize_player_name(t) for t in _re.split(r'[\s\-]+', player_name) if len(t) > 1]

    best = None
    best_score = 0

    for p in players:
        raw_name = p.get("player", "") or p.get("name", "") or ""
        pname = _normalize_player_name(raw_name)
        if not pname:
            continue
        pname_tokens = [_normalize_player_name(t) for t in _re.split(r'[\s\-]+', raw_name) if len(t) > 1]

        # Exact match
        if pname == target:
            return p

        score = 0

        # Substring match (full normalized strings)
        if target in pname or pname in target:
            score += 5

        # Token matching: each target token is a prefix of (or matches) a box score token
        if target_tokens and pname_tokens:
            token_matches = 0
            for tt in target_tokens:
                for pt in pname_tokens:
                    if tt == pt or pt.startswith(tt) or tt.startswith(pt):
                        token_matches += 1
                        break
            if token_matches >= len(target_tokens):
                score += 5  # All tokens matched
            elif token_matches >= len(target_tokens) - 1 and token_matches >= 2:
                score += 3  # Almost all matched (handles abbreviated middle names)

        # Last name exact match (strong signal)
        if target_tokens and pname_tokens:
            if target_tokens[-1] == pname_tokens[-1]:
                score += 3
            # Also try: last token of target is suffix of last token of pname
            elif pname_tokens[-1].endswith(target_tokens[-1]) or target_tokens[-1].endswith(pname_tokens[-1]):
                score += 2

        if score > best_score:
            best_score = score
            best = p

    if best_score >= 2:
        return best
    return None


async def check_afl_leg(client: httpx.AsyncClient, parsed: dict) -> dict:
    """
    Check result for a single AFL leg.
    Returns enriched dict with actual, result fields.
    """
    result = {**parsed, "actual": None, "result": "pending", "note": ""}

    try:
        round_num = await _get_afl_current_round(client)
        game = None
        if parsed["teams"]:
            game = await _find_afl_game(client, parsed["teams"], round_num)

        # V2 fallback: find game by player's team abbreviation
        if not game and parsed.get("team"):
            team_code = parsed["team"]
            # Match the 7-round window used by _find_afl_game so V2-formatted
            # legs (no teams string) get the same lookup reach.
            for rnd in [round_num, round_num - 1, round_num + 1, round_num - 2, round_num + 2, round_num - 3, round_num + 3]:
                if rnd < 1:
                    continue
                games_data = await _squiggle_get(client, {"q": f"games;year=2026;round={rnd}"})
                for g in games_data.get("games", []):
                    if _teams_match(g.get("hteam", ""), team_code) or _teams_match(g.get("ateam", ""), team_code):
                        game = g
                        break
                if game:
                    break

        # V2 last resort: no teams or team — search all games for the player
        if not game and not parsed.get("teams") and not parsed.get("team"):
            player_name = parsed.get("player", "").lower()
            for rnd in [round_num, round_num - 1]:
                games_data = await _squiggle_get(client, {"q": f"games;year=2026;round={rnd}"})
                for g in games_data.get("games", []):
                    gid = g.get("id")
                    if not gid:
                        continue
                    # Check if this game has started
                    if not g.get("complete") or g.get("complete") == 0:
                        continue
                    try:
                        pdata = await _squiggle_get(client, {"q": f"players;year=2026;round={rnd};gid={gid}"})
                        for p in pdata.get("players", []):
                            pn = (p.get("player", "") or p.get("surname", "")).lower()
                            if player_name.split()[-1] in pn or pn in player_name:
                                game = g
                                break
                    except Exception:
                        pass
                    if game:
                        break
                if game:
                    break

        if not game:
            result["note"] = f"Game not found for {parsed.get('teams') or parsed.get('team', '?')}"
            return result

        game_id = game.get("id")
        hteam = game.get("hteam", "")
        ateam = game.get("ateam", "")

        # Check if game has started / completed
        # Squiggle: 'complete' field (0=not started, 100=complete, partial otherwise)
        complete = game.get("complete", 0)
        is_complete = complete == 100
        has_started = complete is not None and complete > 0

        if not has_started:
            result["result"] = "pending"
            result["note"] = "Game hasn't started"
            return result

        # Fetch player stats — use Footywire (Squiggle players endpoint is blocked)
        try:
            from live_stats import get_live_stats, find_match_id_by_teams
            fw_match_id = find_match_id_by_teams(hteam, ateam)
            if fw_match_id:
                fw_data = get_live_stats(fw_match_id)
                players = fw_data.get("players", [])
                # Adapt Footywire format to match Squiggle format for _find_player
                for p in players:
                    if "player" not in p and "name" in p:
                        p["player"] = p["name"]
            else:
                players = []
        except Exception as e:
            logger.warning(f"Footywire stats failed, falling back to Squiggle: {e}")
            actual_round = game.get("round", round_num)
            players = await _get_afl_player_stats(client, game_id, actual_round)

        if not players:
            if is_complete:
                result["result"] = "pending"
                result["note"] = "Game complete but player stats not yet available"
            else:
                result["result"] = "pending"
                result["note"] = "Game in progress, stats not yet available"
            return result

        player = _find_player(players, parsed["player"])
        if not player:
            result["note"] = f"Player '{parsed['player']}' not found in game"
            result["result"] = "pending"
            return result

        stat_key = parsed["stat"]
        actual_value = player.get(stat_key)

        # Handle combo stats (e.g. "kicks+handballs" = disposals effectively)
        if actual_value is None and "+" in stat_key:
            parts = stat_key.split("+")
            vals = [player.get(p) for p in parts]
            if all(v is not None for v in vals):
                actual_value = sum(float(v) for v in vals)

        if actual_value is None:
            result["note"] = f"Stat '{stat_key}' not found for player (available: {list(player.keys())})"
            result["result"] = "pending"
            return result

        actual_value = float(actual_value)
        result["actual"] = actual_value

        if is_complete:
            result["result"] = "won" if actual_value >= parsed["line"] else "lost"
        else:
            # In progress: show partial result
            result["result"] = "pending"
            result["note"] = f"In progress ({int(complete)}% complete)"

    except Exception as e:
        logger.exception(f"check_afl_leg error: {e}")
        result["note"] = f"Error: {str(e)}"
        result["result"] = "pending"

    return result


# ─── NBA via ESPN ────────────────────────────────────────────────────────────

# Team code → ESPN abbreviation (for matching)
NBA_TEAM_CODES = {
    "atl": "ATL", "bkn": "BKN", "bos": "BOS", "cha": "CHA", "chi": "CHI",
    "cle": "CLE", "dal": "DAL", "den": "DEN", "det": "DET", "gsw": "GS",
    "hou": "HOU", "ind": "IND", "lac": "LAC", "lal": "LAL", "mem": "MEM",
    "mia": "MIA", "mil": "MIL", "min": "MIN", "nop": "NO", "nyk": "NY",
    "okc": "OKC", "orl": "ORL", "phi": "PHI", "phx": "PHX", "por": "POR",
    "sac": "SAC", "sas": "SA", "tor": "TOR", "uta": "UTAH", "was": "WSH",
}


async def _espn_get(client: httpx.AsyncClient, url: str) -> dict:
    cache_key = f"espn:{url}"
    cached = _cached_get(cache_key)
    if cached is not None:
        return cached
    try:
        resp = await client.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _cache_set(cache_key, data)
        return data
    except Exception as e:
        logger.error(f"ESPN API error: {e}")
        return {}


async def _find_nba_game_espn(client: httpx.AsyncClient, teams_str: str) -> Optional[dict]:
    """Find an NBA game on ESPN by teams string like 'Chi-Ind'."""
    parts = teams_str.split("-")
    if len(parts) != 2:
        return None
    code_a, code_b = parts[0].strip().lower(), parts[1].strip().lower()
    espn_a = NBA_TEAM_CODES.get(code_a, code_a.upper())
    espn_b = NBA_TEAM_CODES.get(code_b, code_b.upper())

    from datetime import date, timedelta
    # Check today and yesterday (AEST games show as previous day UTC)
    for offset in [0, -1, 1]:
        d = date.today() + timedelta(days=offset)
        date_str = d.strftime("%Y%m%d")
        data = await _espn_get(client, f"{ESPN_NBA_BASE}/scoreboard?dates={date_str}")
        for event in data.get("events", []):
            comp = event.get("competitions", [{}])[0]
            teams = comp.get("competitors", [])
            if len(teams) < 2:
                continue
            abbr_h = teams[0].get("team", {}).get("abbreviation", "")
            abbr_a = teams[1].get("team", {}).get("abbreviation", "")
            if (
                (espn_a == abbr_h and espn_b == abbr_a) or
                (espn_a == abbr_a and espn_b == abbr_h) or
                (espn_b == abbr_h and espn_a == abbr_a) or
                (espn_b == abbr_a and espn_a == abbr_h)
            ):
                return {"event_id": event.get("id"), "status": comp.get("status", {})}
    return None


async def _get_espn_box_score(client: httpx.AsyncClient, event_id: str) -> list[dict]:
    """Get player stats from ESPN box score. Returns flat list of {name, stats_dict}."""
    cache_key = f"espn:box:{event_id}"
    cached = _cached_get(cache_key)
    if cached is not None:
        return cached

    data = await _espn_get(client, f"{ESPN_NBA_BASE}/summary?event={event_id}")
    box = data.get("boxscore", {})
    all_players = []

    # ESPN labels: ['MIN', 'PTS', 'FG', '3PT', 'FT', 'REB', 'AST', 'TO', 'STL', 'BLK']
    stat_label_map = {
        "MIN": "min", "PTS": "pts", "FG": "fg", "3PT": "fg3m",
        "FT": "ft", "REB": "reb", "AST": "ast", "TO": "to",
        "STL": "stl", "BLK": "blk",
    }

    for team_data in box.get("players", []):
        for stat_group in team_data.get("statistics", []):
            labels = stat_group.get("labels", [])
            for athlete in stat_group.get("athletes", []):
                name = athlete.get("athlete", {}).get("displayName", "")
                raw_stats = athlete.get("stats", [])
                # DNP detection — when ESPN flags didNotPlay, returns an empty
                # stats array, or marks "DNP"/"DNP-..." in the stats column the
                # player was inactive. Their leg should grade as void rather
                # than sitting pending forever.
                dnp = bool(athlete.get("didNotPlay"))
                if not dnp:
                    if not raw_stats:
                        dnp = True
                    elif any(isinstance(v, str) and v.strip().upper().startswith("DNP")
                             for v in raw_stats):
                        dnp = True
                stats = {}
                if not dnp:
                    for i, label in enumerate(labels):
                        key = stat_label_map.get(label, label.lower())
                        if i < len(raw_stats):
                            try:
                                # Handle "4-11" FG format — extract made count
                                val = raw_stats[i]
                                if "-" in str(val) and key in ("fg", "fg3m", "ft"):
                                    val = val.split("-")[0]
                                stats[key] = float(val)
                            except (ValueError, TypeError):
                                stats[key] = 0
                all_players.append({"name": name, "stats": stats, "dnp": dnp})

    _cache_set(cache_key, all_players)
    return all_players


async def _find_nba_game_by_team(client: httpx.AsyncClient, team_code: str) -> Optional[dict]:
    """Find an NBA game on ESPN by a single team abbreviation (fallback when teams string is empty)."""
    espn_abbr = NBA_TEAM_CODES.get(team_code.lower(), team_code.upper())
    from datetime import date, timedelta
    # Widened from [0, -1, 1] — late-graded bets (2-3 days post game) were
    # showing "Game not found" because the lookup couldn't see far enough back.
    for offset in [0, -1, -2, -3, 1, 2]:
        d = date.today() + timedelta(days=offset)
        date_str = d.strftime("%Y%m%d")
        data = await _espn_get(client, f"{ESPN_NBA_BASE}/scoreboard?dates={date_str}")
        for event in data.get("events", []):
            comp = event.get("competitions", [{}])[0]
            teams = comp.get("competitors", [])
            if len(teams) < 2:
                continue
            abbr_h = teams[0].get("team", {}).get("abbreviation", "")
            abbr_a = teams[1].get("team", {}).get("abbreviation", "")
            if espn_abbr in (abbr_h, abbr_a):
                return {"event_id": event.get("id"), "status": comp.get("status", {})}
    return None


async def check_nba_leg(client: httpx.AsyncClient, parsed: dict) -> dict:
    """Check result for a single NBA leg using ESPN."""
    result = {**parsed, "actual": None, "result": "pending", "note": ""}

    try:
        game = None
        if parsed["teams"]:
            game = await _find_nba_game_espn(client, parsed["teams"])
        if not game and parsed.get("team"):
            game = await _find_nba_game_by_team(client, parsed["team"])

        if not game:
            result["note"] = f"Game not found for {parsed['teams'] or parsed.get('team', '?')}"
            return result

        event_id = game["event_id"]
        status = game.get("status", {})
        status_type = status.get("type", {}).get("name", "")
        is_final = status_type == "STATUS_FINAL"
        has_started = status_type in ("STATUS_IN_PROGRESS", "STATUS_FINAL", "STATUS_HALFTIME", "STATUS_END_PERIOD")

        if not has_started:
            result["result"] = "pending"
            result["note"] = "Game hasn't started"
            return result

        players = await _get_espn_box_score(client, event_id)
        if not players:
            result["result"] = "pending"
            result["note"] = "No player stats available yet"
            return result

        # Find the player using the shared fuzzy matcher — handles abbreviated
        # first names ("Shai G-Alexander" → "Shai Gilgeous-Alexander"), aliases
        # ("SGA"), and tokenised surname matches via PLAYER_ALIASES expansion.
        # The previous inline NBA matcher only did substring + last-name
        # comparisons on the *normalized* (no-space) string, which couldn't
        # see past abbreviated middle names.
        best_player = _find_player(players, parsed["player"])
        if not best_player:
            result["note"] = f"Player '{parsed['player']}' not found in box score"
            result["result"] = "pending"
            return result

        # DNP / inactive — TAB voids the leg, so we should too instead of
        # leaving it pending forever after game completion.
        if best_player.get("dnp"):
            result["result"] = "void"
            result["note"] = f"{best_player['name']} did not play"
            return result

        stat_key = parsed["stat"]
        stats = best_player["stats"]

        # Handle combo stats
        if stat_key == "pts_reb_ast":
            v = stats.get("pts", 0) + stats.get("reb", 0) + stats.get("ast", 0)
        elif stat_key == "pts_ast":
            v = stats.get("pts", 0) + stats.get("ast", 0)
        elif stat_key == "pts_reb":
            v = stats.get("pts", 0) + stats.get("reb", 0)
        elif stat_key == "reb_ast":
            v = stats.get("reb", 0) + stats.get("ast", 0)
        elif stat_key == "stl_blk":
            v = stats.get("stl", 0) + stats.get("blk", 0)
        else:
            v = stats.get(stat_key)

        if v is None:
            result["note"] = f"Stat '{stat_key}' not in box score"
            result["result"] = "pending"
            return result

        result["actual"] = float(v)
        if is_final:
            result["result"] = "won" if v >= parsed["line"] else "lost"
        else:
            result["result"] = "pending"
            detail = status.get("type", {}).get("shortDetail", "In progress")
            result["note"] = f"In progress ({detail})"

    except Exception as e:
        logger.exception(f"check_nba_leg error: {e}")
        result["note"] = f"Error: {str(e)}"
        result["result"] = "pending"

    return result


def _safe_float(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ─── Main Entry Point ─────────────────────────────────────────────────────────

async def check_leg_results(legs: list[dict]) -> list[dict]:
    """
    Check actual results for each leg.

    Each leg should be a dict with at least a 'name' key.
    Returns list of enriched dicts:
      {name, player, stat, line, actual, result: 'won'|'lost'|'pending', note, ...}
    """
    results = []

    async with httpx.AsyncClient(timeout=20) as client:
        tasks = []
        parsed_legs = []

        # Expand combined legs (old V2 format: "Player A 15+ Disposals/Player B 20+ Disposals")
        expanded_legs = []
        for leg in legs:
            leg_name = leg.get("name", "") if isinstance(leg, dict) else str(leg)
            if "/" in leg_name and "—" not in leg_name and "@" not in leg_name:
                # Split combined format into individual legs
                for part in leg_name.split("/"):
                    expanded_legs.append({"name": part.strip()})
            else:
                expanded_legs.append(leg)

        for leg in expanded_legs:
            leg_name = leg.get("name", "") if isinstance(leg, dict) else str(leg)
            parsed = parse_leg_name(leg_name)

            if not parsed:
                results.append({
                    "name": leg_name,
                    "player": None,
                    "stat": None,
                    "line": None,
                    "actual": None,
                    "result": "pending",
                    "note": "Could not parse leg name",
                })
                continue

            parsed_legs.append(parsed)

            if parsed["sport"] == "AFL":
                tasks.append(check_afl_leg(client, parsed))
            elif parsed["sport"] == "NBA":
                tasks.append(check_nba_leg(client, parsed))
            else:
                # NRL / other: not yet supported
                async def _unsupported(p=parsed):
                    return {**p, "actual": None, "result": "pending", "note": f"{p['sport']} not yet supported"}
                tasks.append(_unsupported())

        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, tr in enumerate(task_results):
                if isinstance(tr, Exception):
                    results.append({
                        **parsed_legs[i],
                        "actual": None,
                        "result": "pending",
                        "note": f"Exception: {tr}",
                    })
                else:
                    results.append(tr)

    return results

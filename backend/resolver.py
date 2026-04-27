"""
Resolve player names + markets to TAB proposition IDs.
Matches CSV bet descriptions to live SGM propositions.
"""
import re
import logging
from typing import Optional

from betting import get_matches, get_match_markets, get_sgm_propositions, price_check

logger = logging.getLogger(__name__)

# Team name mappings: abbreviation -> possible TAB names
TEAM_ALIASES = {
    # AFL
    "GWS": ["GWS Giants", "GWS", "Greater Western Sydney", "Giants"],
    "COL": ["Collingwood", "Collingwood Magpies", "Magpies"],
    "SYD": ["Sydney", "Sydney Swans", "Swans"],
    "MEL": ["Melbourne", "Melbourne Demons", "Demons"],
    "WCE": ["West Coast", "West Coast Eagles", "Eagles"],
    "HAW": ["Hawthorn", "Hawthorn Hawks", "Hawks"],
    "BRI": ["Brisbane", "Brisbane Lions", "Lions"],
    "GEE": ["Geelong", "Geelong Cats", "Cats"],
    "RIC": ["Richmond", "Richmond Tigers", "Tigers"],
    "ESS": ["Essendon", "Essendon Bombers", "Bombers"],
    "NM": ["North Melbourne", "North Melbourne Kangaroos", "Kangaroos", "Roos"],
    "NME": ["North Melbourne", "North Melbourne Kangaroos", "Kangaroos", "Roos"],
    "CAR": ["Carlton", "Carlton Blues", "Blues"],
    "STK": ["St Kilda", "St Kilda Saints", "Saints"],
    "FRE": ["Fremantle", "Fremantle Dockers", "Dockers"],
    "PA": ["Port Adelaide", "Port Adelaide Power", "Power"],
    "WB": ["Western Bulldogs", "Bulldogs", "Footscray"],
    "WBD": ["Western Bulldogs", "Bulldogs", "Footscray"],
    "ADL": ["Adelaide", "Adelaide Crows", "Crows"],
    "GC": ["Gold Coast", "Gold Coast Suns", "Suns"],
    # NBA
    "ATL": ["Atlanta", "Hawks"],
    "BOS": ["Boston", "Celtics"],
    "BKN": ["Brooklyn", "Nets"],
    "CHA": ["Charlotte", "Hornets"],
    "CHI": ["Chicago", "Bulls"],
    "CLE": ["Cleveland", "Cavaliers"],
    "DAL": ["Dallas", "Mavericks"],
    "DEN": ["Denver", "Nuggets"],
    "DET": ["Detroit", "Pistons"],
    "GSW": ["Golden State", "Warriors"],
    "HOU": ["Houston", "Rockets"],
    "IND": ["Indiana", "Pacers"],
    "LAC": ["LA Clippers", "Clippers"],
    "LAL": ["LA Lakers", "Lakers", "Los Angeles Lakers"],
    "MEM": ["Memphis", "Grizzlies"],
    "MIA": ["Miami", "Heat"],
    "MIL": ["Milwaukee", "Bucks"],
    "MIN": ["Minnesota", "Timberwolves"],
    "NOP": ["New Orleans", "Pelicans"],
    "NYK": ["New York", "Knicks"],
    "OKC": ["Oklahoma City", "Thunder"],
    "ORL": ["Orlando", "Magic"],
    "PHI": ["Philadelphia", "76ers"],
    "PHX": ["Phoenix", "Suns"],
    "POR": ["Portland", "Trail Blazers"],
    "SAC": ["Sacramento", "Kings"],
    "SAS": ["San Antonio", "Spurs"],
    "TOR": ["Toronto", "Raptors"],
    "UTA": ["Utah", "Jazz"],
    "WAS": ["Washington", "Wizards"],
    # NRL
    "BRO": ["Brisbane Broncos", "Broncos"],
    "PAN": ["Penrith", "Panthers"],
    "STO": ["Melbourne Storm", "Storm"],
    "ROO": ["Sydney Roosters", "Roosters"],
    "RAB": ["South Sydney", "Rabbitohs", "Souths"],
    "SHA": ["Cronulla", "Sharks"],
    "COW": ["North Queensland", "Cowboys"],
    "RAI": ["Canberra", "Raiders"],
    "MAN": ["Manly", "Sea Eagles"],
    "EEL": ["Parramatta", "Eels"],
    "DRA": ["St George", "Dragons"],
    "TIT": ["Gold Coast Titans", "Titans"],
    "KNI": ["Newcastle", "Knights"],
    "WAR": ["New Zealand", "Warriors"],
    "BUL": ["Canterbury", "Bulldogs"],
    "TIG": ["Wests Tigers", "Tigers"],
    "DOL": ["Dolphins", "Redcliffe"],
}


def _parse_game_id(game_id: str) -> tuple[str, str]:
    """Parse game ID like '20260327_GWS_COL' into (away_abbr, home_abbr)."""
    parts = game_id.split("_")
    if len(parts) >= 3:
        return parts[1], parts[2]
    return "", ""


def _team_matches(abbr: str, match_name: str) -> bool:
    """Check if a team abbreviation matches a TAB match name."""
    aliases = TEAM_ALIASES.get(abbr.upper(), [abbr])
    match_lower = match_name.lower()
    return any(a.lower() in match_lower for a in aliases)


def _find_match(matches: list, game_id: str) -> Optional[dict]:
    """Find the TAB match matching a game ID."""
    away, home = _parse_game_id(game_id)
    if not away or not home:
        return None

    for m in matches:
        name = m.get("match_name", "") or str(m.get("match_id", ""))
        if _team_matches(away, name) and _team_matches(home, name):
            return m

    # Fallback: try matching just one team
    for m in matches:
        name = m.get("match_name", "") or str(m.get("match_id", ""))
        if _team_matches(away, name) or _team_matches(home, name):
            return m

    return None


def _parse_leg_description(desc: str) -> dict:
    """
    Parse leg like 'Billy Frampton 15+ Disposals' into:
    {player: 'Billy Frampton', line: '14.5', market: 'Disposals'}

    Also handles team handicap/spread legs like:
    'WBD +35.5 Alt Spread' -> {team_line: True, team: 'WBD', line_value: '+35.5', ...}
    """
    stripped = desc.strip()

    # Team handicap/spread: "WBD +35.5 Alt Spread", "Sydney -5.5 Alt Spread", "Bulldogs +16.5"
    spread_match = re.match(
        r'^(.+?)\s+([+-]\d+\.?\d*)\s*(Alt\s+Spread|Alt\s+Line|Spread|Line)?$',
        stripped, re.IGNORECASE,
    )
    if spread_match:
        team_part = spread_match.group(1).strip()
        line_val = spread_match.group(2)
        # Check if team_part is a known team alias or full team name
        team_upper = team_part.upper()
        is_team = team_upper in TEAM_ALIASES
        if not is_team:
            # Check if it matches any alias value (e.g. "Bulldogs", "Sydney")
            team_lower = team_part.lower()
            for abbr, aliases in TEAM_ALIASES.items():
                if any(a.lower() == team_lower for a in aliases):
                    is_team = True
                    team_upper = abbr
                    break
        if is_team:
            return {
                "team_line": True,
                "team": team_upper,
                "team_name": team_part,
                "line_value": line_val,
                "player": "",
                "line": None,
                "market": None,
                "raw": stripped,
            }

    # Pattern: "Player Name N+ Market" e.g. "Billy Frampton 15+ Disposals"
    match = re.match(r'^(.+?)\s+(\d+)\+\s+(.+)$', stripped)
    if match:
        player = match.group(1).strip()
        threshold = int(match.group(2))
        market = match.group(3).strip()
        # TAB uses X.5 lines (e.g. 15+ = Over 14.5)
        line = f"{threshold - 0.5}"
        return {"player": player, "line": line, "market": market, "raw": stripped}

    # Pattern: "Player Name Over/Under N.5 Market"
    match = re.match(r'^(.+?)\s+(Over|Under)\s+([\d.]+)\s+(.+)$', stripped, re.IGNORECASE)
    if match:
        return {
            "player": match.group(1).strip(),
            "line": match.group(3),
            "market": match.group(4).strip(),
            "raw": stripped,
        }

    # Fallback: just use the whole thing as player name
    return {"player": stripped, "line": None, "market": None, "raw": stripped}


def _normalize(s: str) -> str:
    """Normalize string for fuzzy matching."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _hyphen_tokens(s: str) -> list:
    """
    Split on whitespace AND hyphens, then normalize each token.
    Used to bridge CSV abbreviations like 'Shai G-Alexander' against TAB's
    'Shai Gilgeous-Alexander' — tokens ['shai','g','alexander'] vs
    ['shai','gilgeous','alexander']. The surname 'alexander' now lines up
    directly as a standalone token even though the hyphenated full form
    would otherwise concatenate with 'gilgeous' and defeat substring checks.
    """
    return [_normalize(t) for t in re.split(r'[\s\-]+', (s or '').lower()) if t]


# Common player abbreviations/nicknames used by tipsters
PLAYER_ALIASES = {
    # NBA
    "sga": "shai gilgeous-alexander",
    "shai g-alexander": "shai gilgeous-alexander",
    "shai g alexander": "shai gilgeous-alexander",
    "shai galexander": "shai gilgeous-alexander",
    "s gilgeous-alexander": "shai gilgeous-alexander",
    "s g-alexander": "shai gilgeous-alexander",
    "naw": "nickeil alexander-walker",
    "nickeil a-walker": "nickeil alexander-walker",
    "n alexander-walker": "nickeil alexander-walker",
    "n a-walker": "nickeil alexander-walker",
    "kat": "karl-anthony towns",
    "karl-a towns": "karl-anthony towns",
    "k-a towns": "karl-anthony towns",
    "pg": "paul george",
    "ad": "anthony davis",
    "lbj": "lebron james",
    "cp3": "chris paul",
    "rj": "rj barrett",
    "sjr": "shai gilgeous-alexander",  # alternate
    "ant": "anthony edwards",
    "ja": "ja morant",
    "steph": "stephen curry",
    "giannis": "giannis antetokounmpo",
    "jokic": "nikola jokic",
    "luka": "luka doncic",
    "tatum": "jayson tatum",
    "dame": "damian lillard",
    "kyrie": "kyrie irving",
    "kd": "kevin durant",
    "jimmy": "jimmy butler",
    "bam": "bam adebayo",
    "fox": "de'aaron fox",
    "zion": "zion williamson",
    "chet": "chet holmgren",
    "herb": "herb jones",
    "jjj": "jaren jackson jr",
    "rui": "rui hachimura",
    "dlo": "d'angelo russell",
    "ayo": "ayo dosunmu",
    "tre": "trae young",
    # AFL
    "bont": "marcus bontempelli",
    "gawn": "max gawn",
    "cripps": "patrick cripps",
    "neale": "lachie neale",
    "mills": "callum mills",
    "danger": "patrick dangerfield",
    "dusty": "dustin martin",
    "pendles": "scott pendlebury",
    # NRL
    "turbo": "tom trbojevic",
    "tommy t": "tom trbojevic",
    "munster": "cameron munster",
    "ponga": "kalyn ponga",
    "cleary": "nathan cleary",
    "latrell": "latrell mitchell",
}


def _expand_alias(player_name: str) -> str:
    """Expand a player alias/nickname to full name if known."""
    key = player_name.strip().lower()
    return PLAYER_ALIASES.get(key, player_name)


def _match_proposition(leg: dict, propositions: list) -> Optional[dict]:
    """
    Find the best matching proposition for a leg description.
    Matches player name + market type (e.g. "30+ Disposals").
    TAB format: name="Jack Sinclair (STK)", marketName="30+ Disposals"
    CSV format: "Jack Sinclair 30+ Disposals"
    """
    # Expand aliases (SGA -> Shai Gilgeous-Alexander) before matching
    player_raw = _expand_alias(leg["player"])
    player_norm = _normalize(player_raw)
    market_raw = leg.get("market") or ""  # e.g. "Disposals"
    market_norm = _normalize(market_raw)
    line = leg.get("line")  # e.g. "29.5" for 30+

    # Build the full market string from CSV: "30+ Disposals"
    threshold = None
    if line:
        try:
            threshold = str(int(float(line) + 0.5))  # 29.5 -> "30"
        except (ValueError, TypeError):
            pass
    full_market = f"{threshold}+ {market_raw}" if threshold and market_raw else market_raw
    full_market_norm = _normalize(full_market)

    best = None
    best_score = 0

    for prop in propositions:
        sel_name = prop.get("selectionName", prop.get("name", ""))
        mkt_name = prop.get("marketName", prop.get("market", ""))
        prop_line = str(prop.get("line", ""))

        # Strip team suffix like "(STK)" from name for matching
        sel_clean = re.sub(r'\s*\([A-Z]{2,4}\)\s*$', '', sel_name)
        sel_norm = _normalize(sel_clean)
        mkt_norm = _normalize(mkt_name)

        score = 0

        # Player name match (most important)
        if player_norm in sel_norm or sel_norm in player_norm:
            score += 10
        elif player_norm and sel_norm and player_norm == sel_norm:
            score += 10
        else:
            # Try last name match
            last_name = _normalize(player_raw.split()[-1]) if player_raw else ""
            if last_name and len(last_name) > 2 and last_name in sel_norm:
                score += 7
            else:
                # Hyphen-aware multi-token match. Splits both sides on space AND hyphen
                # so "Shai G-Alexander" → ['shai','g','alexander'] lines up against TAB's
                # 'Shai Gilgeous-Alexander' → ['shai','gilgeous','alexander'] on 'shai'
                # and 'alexander'. Requires ≥2 multi-char CSV tokens to hit sel tokens
                # (as substring or prefix) to stay disambiguated from e.g. Alexander-Walker.
                csv_tokens = _hyphen_tokens(player_raw)
                csv_multi = [t for t in csv_tokens if len(t) > 1]
                sel_tokens_split = _hyphen_tokens(sel_clean)
                token_matches = sum(
                    1 for t in csv_multi
                    if any(t in st or st.startswith(t) or t.startswith(st) for st in sel_tokens_split)
                )
                if len(csv_multi) >= 2 and token_matches >= 2:
                    score += 8  # Strong hyphen-aware multi-token match
                else:
                    # Token-based fuzzy match: every word in the player name must appear
                    # in the selection name. Handles "Nickeil A Walker" vs "Nickeil Alexander-Walker"
                    # and abbreviated middle names.
                    player_tokens = [_normalize(t) for t in player_raw.split() if len(t) > 1]
                    if player_tokens and len(player_tokens) >= 2:
                        matches = sum(1 for tok in player_tokens if tok in sel_norm or sel_norm.startswith(tok[:3]))
                        # Also try: each token is a PREFIX of a word in sel_clean
                        sel_words = [_normalize(w) for w in sel_clean.split()]
                        prefix_matches = sum(1 for tok in player_tokens if any(w.startswith(tok) or tok.startswith(w) for w in sel_words))
                        best_match = max(matches, prefix_matches)
                        if best_match >= len(player_tokens) - 1 and best_match >= 2:
                            score += 6  # Good partial match

        if score == 0:
            continue  # Must at least match player name

        # Market match — TAB betOption is like "30+ Disposals"
        # STRICT: if CSV specifies a market type, the prop MUST match it.
        # This prevents "15+ Points" from matching "Double-Double" or "Rebounds".
        if market_norm:
            market_matched = False
            if full_market_norm and full_market_norm == mkt_norm:
                score += 8  # Exact market+line match
                market_matched = True
            elif full_market_norm and full_market_norm in mkt_norm:
                score += 6
                market_matched = True
            elif market_norm in mkt_norm:
                score += 5
                market_matched = True
            else:
                # Keyword fallback for common market types
                market_keywords = {
                    "disposal": "disposal", "goal": "goal", "mark": "mark",
                    "tackle": "tackle", "point": "pt", "rebound": "reb",
                    "assist": "ast", "hit": "hit", "steal": "steal",
                    "block": "block", "triple": "triple", "double": "double",
                }
                for csv_kw, tab_kw in market_keywords.items():
                    if csv_kw in market_norm and (csv_kw in mkt_norm or tab_kw in mkt_norm):
                        score += 5
                        market_matched = True
                        break

            if not market_matched:
                continue  # HARD REJECT: market type doesn't match at all

        # Line match
        if line and prop_line and str(line) == str(prop_line):
            score += 3

        # Over/under - prefer "Over" for N+ bets
        if "over" in mkt_norm.lower():
            score += 1

        if score > best_score:
            best_score = score
            best = prop

    return best


def _match_team_line(leg: dict, propositions: list) -> Optional[dict]:
    """
    Match a team handicap/spread leg against TAB line/handicap/Pick Your Own Line markets.
    Leg format: {team_line: True, team: 'WBD', line_value: '+35.5'}
    """
    team_abbr = leg.get("team", "")
    line_val = leg.get("line_value", "")
    if not team_abbr or not line_val:
        return None

    aliases = TEAM_ALIASES.get(team_abbr.upper(), [leg.get("team_name", team_abbr)])
    aliases_lower = [a.lower() for a in aliases]

    line_keywords = ("line", "handicap", "pick your own", "spread")

    best = None
    best_score = 0

    for prop in propositions:
        mkt_name = prop.get("marketName", prop.get("market", "")).lower()
        if not any(k in mkt_name for k in line_keywords):
            continue

        sel_name = prop.get("selectionName", prop.get("name", ""))
        sel_lower = sel_name.lower()

        # Check team name match
        team_matched = any(a in sel_lower for a in aliases_lower)
        if not team_matched:
            continue

        # Check line value match ("+35.5" in proposition name)
        if line_val not in sel_name and line_val not in sel_lower:
            continue

        score = 10
        # Prefer "Pick Your Own Line" over generic "Line" markets
        if "pick your own" in mkt_name:
            score += 5
        if "handicap" in mkt_name:
            score += 2

        if score > best_score:
            best_score = score
            best = prop

    return best


def resolve_and_price(
    token: str,
    proxy_url: str,
    game_id: str,
    bet_description: str,
    stake: str,
    min_odds: float,
    sport: str = "AFL Football",
    competition: str = "AFL",
) -> dict:
    """
    Resolve a bet description to propositions, price check, and validate odds.

    Returns dict with:
    - resolved: bool
    - match_name: str
    - legs: list of resolved legs
    - combined_odds: str or None
    - meets_minimum: bool
    - error: str or None
    """
    result = {
        "game_id": game_id,
        "description": bet_description,
        "resolved": False,
        "match_name": None,
        "legs": [],
        "propositions": [],
        "combined_odds": None,
        "meets_minimum": False,
        "error": None,
    }

    try:
        # Step 1: Get matches
        matches = get_matches(token, proxy_url, sport, competition)
        if not matches:
            result["error"] = "No matches found"
            return result

        # Step 2: Find the match
        match = _find_match(matches, game_id)
        if not match:
            result["error"] = f"Could not find match for {game_id}"
            return result

        result["match_name"] = match.get("match_name", match.get("match_id"))

        # Step 3: Get SGM propositions (use URL-encoded match name, not short ID)
        sgm_match_id = match.get("match_name_url", match["match_id"])
        sgm_data = get_sgm_propositions(token, sgm_match_id, proxy_url, sport, competition)

        # Flatten all propositions from the nested response
        # Structure: {data: [{title, data: [{betOption, data: [{propositions: [...]}]}]}]}
        all_props = []

        def _extract_props(obj, market_name="", depth=0):
            if depth > 8:
                return
            if isinstance(obj, dict):
                # Track market/betOption name
                current_market = obj.get("betOption", obj.get("title", market_name))

                # If this dict has a propositions array, extract them
                if "propositions" in obj and isinstance(obj["propositions"], list):
                    for p in obj["propositions"]:
                        prop = {
                            "propositionId": p.get("numberId", p.get("id")),
                            "name": p.get("name", ""),
                            "selectionName": p.get("name", ""),
                            "marketName": current_market,
                            "odds": {"decimal": str(p.get("returnWin", 0))},
                            "returnWin": p.get("returnWin", 0),
                            "line": str(p.get("line", "")),
                        }
                        all_props.append(prop)
                    return

                # If this has propositionId/numberId directly
                if "numberId" in obj or "propositionId" in obj:
                    prop = {
                        "propositionId": obj.get("numberId", obj.get("propositionId")),
                        "name": obj.get("name", ""),
                        "selectionName": obj.get("selectionName", obj.get("name", "")),
                        "marketName": market_name,
                        "odds": {"decimal": str(obj.get("returnWin", obj.get("odds", 0)))},
                        "returnWin": obj.get("returnWin", 0),
                    }
                    all_props.append(prop)
                    return

                # Recurse into nested data
                for k, v in obj.items():
                    _extract_props(v, current_market, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    _extract_props(item, market_name, depth + 1)

        _extract_props(sgm_data)

        if not all_props:
            result["error"] = f"No SGM propositions for match {result['match_name']}"
            return result

        # Step 4: Parse and resolve each leg
        leg_descriptions = bet_description.split("/")
        resolved_props = []

        for leg_desc in leg_descriptions:
            leg = _parse_leg_description(leg_desc)
            if leg.get("team_line"):
                prop = _match_team_line(leg, all_props)
            else:
                prop = _match_proposition(leg, all_props)

            leg_result = {
                "description": leg["raw"],
                "player": leg.get("player", leg.get("team_name", "")),
                "market": leg.get("market"),
                "line": leg.get("line") or leg.get("line_value"),
                "matched": prop is not None,
            }

            if prop:
                odds = prop.get("odds", {})
                odds_val = odds.get("decimal", odds) if isinstance(odds, dict) else str(odds)
                leg_result["propositionId"] = prop.get("propositionId", prop.get("id"))
                leg_result["odds"] = str(odds_val)
                leg_result["selectionName"] = prop.get("selectionName", prop.get("name"))
                leg_result["marketName"] = prop.get("marketName", "")
                resolved_props.append({
                    "propositionId": leg_result["propositionId"],
                    "odds": leg_result["odds"],
                })
            else:
                result["error"] = f"Could not match: {leg['raw']}"

            result["legs"].append(leg_result)

        if len(resolved_props) != len(leg_descriptions):
            result["error"] = result.get("error") or "Not all legs resolved"
            return result

        result["propositions"] = resolved_props
        result["resolved"] = True

        # Step 5: Price check
        price_result = price_check(token, resolved_props, stake, "SAME_GAME_MULTI", proxy_url)
        combined = price_result.get("combined_odds")

        if combined:
            result["combined_odds"] = combined
            result["meets_minimum"] = float(combined) >= min_odds
            if not result["meets_minimum"]:
                result["error"] = f"Odds {combined} below minimum {min_odds}"

        return result

    except Exception as e:
        result["error"] = str(e)
        return result


# ─── CSB Resolution (SGM + Multi) ─────────────────────────────────────────────

import time as _time

_sport_props_cache = {}
_matches_cache = {}
_CACHE_TTL = 300  # 5 minutes — TAB opens player markets gradually, stale cache causes false failures


def _flatten_sgm_props(sgm_data):
    """Flatten nested SGM response into a flat list of propositions."""
    all_props = []

    def _extract(obj, market_name="", depth=0):
        if depth > 8:
            return
        if isinstance(obj, dict):
            current_market = obj.get("betOption", obj.get("title", market_name))
            if "propositions" in obj and isinstance(obj["propositions"], list):
                for p in obj["propositions"]:
                    all_props.append({
                        "propositionId": p.get("numberId", p.get("id")),
                        "name": p.get("name", ""),
                        "selectionName": p.get("name", ""),
                        "marketName": current_market,
                        "returnWin": p.get("returnWin", 0),
                        "line": str(p.get("line", "")),
                    })
                return
            if "numberId" in obj or "propositionId" in obj:
                all_props.append({
                    "propositionId": obj.get("numberId", obj.get("propositionId")),
                    "name": obj.get("name", ""),
                    "selectionName": obj.get("selectionName", obj.get("name", "")),
                    "marketName": market_name,
                    "returnWin": obj.get("returnWin", 0),
                })
                return
            for k, v in obj.items():
                _extract(v, current_market, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _extract(item, market_name, depth + 1)

    _extract(sgm_data)
    return all_props


def _fetch_match_props(token, proxy_url, match, sport, competition):
    """Fetch SGM props for a single match. Used by thread pool for SGM resolution."""
    try:
        sgm_id = match.get("match_name_url", match["match_id"])
        sgm_data = get_sgm_propositions(token, sgm_id, proxy_url, sport, competition)
        props = _flatten_sgm_props(sgm_data)
        match_name = match.get("match_name", "")
        for p in props:
            p["_match_name"] = match_name
        logger.info(f"Fetched {len(props)} SGM props for {match_name}")
        return props
    except Exception as e:
        logger.warning(f"SGM props fetch failed for {match.get('match_name', '?')}: {e}")
        return []


def _fetch_match_regular_props(token, proxy_url, match, sport, competition):
    """Fetch regular match market props (tab-info-service). Has ALL player props, not just SGM subset.
    Used for cross-game Multi resolution."""
    try:
        match_id = match.get("match_name_url", match["match_id"])
        data = get_match_markets(token, match_id, proxy_url, sport, competition)
        match_name = match.get("match_name", "")
        props = []
        markets = data.get("markets", [])
        for mkt in markets:
            bet_option = mkt.get("betOption", "")
            for p in mkt.get("propositions", []):
                if not p.get("isOpen"):
                    continue
                props.append({
                    "propositionId": p.get("number", p.get("id")),
                    "name": p.get("name", ""),
                    "selectionName": p.get("name", ""),
                    "marketName": bet_option,
                    "returnWin": p.get("returnWin", 0),
                    "line": "",
                    "_match_name": match_name,
                })
        logger.info(f"Fetched {len(props)} regular props for {match_name}")
        return props
    except Exception as e:
        logger.warning(f"Regular props fetch failed for {match.get('match_name', '?')}: {e}")
        return []


def _get_all_sport_props(token, proxy_url, sport, competition, force_refresh=False, use_regular=True):
    """Get all propositions across all matches for a sport, with caching.
    Uses thread pool to fetch matches in parallel for speed.

    use_regular=True: fetch from regular match markets (all player props — for Multis)
    use_regular=False: fetch from SGM endpoint only (for SGM resolution)
    """
    from concurrent.futures import ThreadPoolExecutor

    cache_key = f"{sport}:{competition}:{'reg' if use_regular else 'sgm'}"
    now = _time.time()
    if not force_refresh and cache_key in _sport_props_cache:
        cached = _sport_props_cache[cache_key]
        if now - cached["ts"] < _CACHE_TTL:
            logger.info(f"Using cached props for {cache_key} ({len(cached['props'])} props)")
            return cached["props"]

    if force_refresh:
        logger.info(f"Force-refreshing props for {cache_key}")

    matches = get_matches(token, proxy_url, sport, competition)
    _matches_cache[f"{sport}:{competition}"] = {"matches": matches, "ts": now}
    fetch_fn = _fetch_match_regular_props if use_regular else _fetch_match_props
    logger.info(f"Fetching {'regular' if use_regular else 'SGM'} props for {len(matches)} {competition} matches in parallel...")

    all_props = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(fetch_fn, token, proxy_url, m, sport, competition)
            for m in matches
        ]
        for f in futures:
            all_props.extend(f.result())

    _sport_props_cache[cache_key] = {"props": all_props, "ts": now}
    logger.info(f"Cached {len(all_props)} props for {cache_key}")
    return all_props


def resolve_csb_bet(
    token: str,
    proxy_url: str,
    bet_type: str,
    game_id: str,
    bet_description: str,
    stake: str,
    min_odds: float,
    sport: str = "AFL Football",
    competition: str = "AFL",
) -> dict:
    """
    Resolve a CSB bet (SGM or Multi) to TAB propositions.

    SGM: uses game_id to find the specific match, resolves within it.
    Multi: scans all matches for the sport to find each player across games.
    """
    result = {
        "bet_type": bet_type,
        "game_id": game_id,
        "description": bet_description,
        "resolved": False,
        "match_name": None,
        "propositions": [],
        "combined_odds": None,
        "meets_minimum": False,
        "matched_odds": [],
        "error": None,
    }

    try:
        leg_descriptions = [l.strip() for l in bet_description.split("/")]

        if bet_type.upper() == "SGM" and game_id:
            # SGM: resolve within a single match (use cached matches if available)
            mcache_key = f"{sport}:{competition}"
            mcached = _matches_cache.get(mcache_key)
            if mcached and _time.time() - mcached["ts"] < _CACHE_TTL:
                matches = mcached["matches"]
            else:
                matches = get_matches(token, proxy_url, sport, competition)
                _matches_cache[mcache_key] = {"matches": matches, "ts": _time.time()}
            match = _find_match(matches, game_id)
            if not match:
                result["error"] = f"Match not found: {game_id}"
                return result
            result["match_name"] = match.get("match_name")
            sgm_id = match.get("match_name_url", match["match_id"])
            sgm_data = get_sgm_propositions(token, sgm_id, proxy_url, sport, competition)
            all_props = _flatten_sgm_props(sgm_data)
        else:
            # Multi: scan all matches using regular markets (has ALL player props, not just SGM subset)
            all_props = _get_all_sport_props(token, proxy_url, sport, competition, use_regular=True)

        if not all_props:
            # Empty all_props is usually a transient scraper/cache hiccup
            # rather than a real "TAB has zero markets" condition. Force a
            # fresh fetch once before bailing so the user doesn't see the
            # error for a momentary blip.
            if bet_type.upper() != "SGM":
                logger.info(f"No propositions on first fetch for {sport}/{competition} — force-refreshing")
                all_props = _get_all_sport_props(
                    token, proxy_url, sport, competition,
                    force_refresh=True, use_regular=True,
                )
            if not all_props:
                result["error"] = "No propositions found"
                return result

        resolved_props = []
        matched_odds = []
        failed_leg = None
        for leg_desc in leg_descriptions:
            leg = _parse_leg_description(leg_desc)
            if leg.get("team_line"):
                prop = _match_team_line(leg, all_props)
            else:
                prop = _match_proposition(leg, all_props)
            if prop:
                odds_val = prop.get("returnWin", 0)
                resolved_props.append({
                    "propositionId": prop.get("propositionId"),
                    "odds": str(odds_val),
                    "name": f"{prop.get('selectionName', prop.get('name', ''))} — {prop.get('marketName', '')}",
                    "match_name": prop.get("_match_name", ""),
                })
                matched_odds.append(float(odds_val) if odds_val else 0)
            else:
                failed_leg = leg['raw']
                break

        # Retry with fresh data if a leg failed and we were using cached props
        if failed_leg and bet_type.upper() != "SGM":
            cache_key = f"{sport}:{competition}"
            was_cached = cache_key in _sport_props_cache
            if was_cached:
                logger.info(f"Leg '{failed_leg}' failed with cached props — retrying with fresh data")
                all_props = _get_all_sport_props(token, proxy_url, sport, competition, force_refresh=True, use_regular=True)
                if all_props:
                    resolved_props = []
                    matched_odds = []
                    failed_leg = None
                    for leg_desc in leg_descriptions:
                        leg = _parse_leg_description(leg_desc)
                        if leg.get("team_line"):
                            prop = _match_team_line(leg, all_props)
                        else:
                            prop = _match_proposition(leg, all_props)
                        if prop:
                            odds_val = prop.get("returnWin", 0)
                            resolved_props.append({
                                "propositionId": prop.get("propositionId"),
                                "odds": str(odds_val),
                                "name": f"{prop.get('selectionName', prop.get('name', ''))} — {prop.get('marketName', '')}",
                                "match_name": prop.get("_match_name", ""),
                            })
                            matched_odds.append(float(odds_val) if odds_val else 0)
                        else:
                            failed_leg = leg['raw']
                            break

        if failed_leg:
            result["error"] = f"Could not match: {failed_leg}"
            return result

        result["propositions"] = resolved_props
        result["matched_odds"] = matched_odds
        result["resolved"] = True

        if bet_type.upper() == "SGM" and game_id:
            # SGM: use TAB pricing service for combined odds. Retry once on
            # empty response — TAB's pricing service has a brief re-pricing
            # window where it returns no odds, then resolves on a single retry.
            props_for_price = [{"propositionId": p["propositionId"], "odds": p["odds"]} for p in resolved_props]
            price_result = price_check(token, props_for_price, stake, "SAME_GAME_MULTI", proxy_url)
            combined = price_result.get("combined_odds")
            if not combined:
                logger.info(f"Price check returned no odds on first attempt — retrying once")
                _time.sleep(0.5)
                price_result = price_check(token, props_for_price, stake, "SAME_GAME_MULTI", proxy_url)
                combined = price_result.get("combined_odds")
            if combined:
                result["combined_odds"] = combined
                result["meets_minimum"] = float(combined) >= min_odds
            else:
                result["error"] = "Price check returned no odds"
        else:
            # Multi: combined odds = product of individual leg odds
            combined = 1.0
            for odds in matched_odds:
                combined *= odds
            result["combined_odds"] = f"{combined:.2f}"
            result["meets_minimum"] = combined >= min_odds

        if not result["meets_minimum"] and not result.get("error"):
            result["error"] = f"Odds {result['combined_odds']} below minimum {min_odds}"

        return result

    except Exception as e:
        result["error"] = str(e)
        return result

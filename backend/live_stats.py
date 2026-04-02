"""
Live AFL player stats from Footywire.
Scrapes live_stats page for real-time disposal counts during games.
"""
import logging
import re
import time
from typing import Optional

from curl_cffi.requests import Session

logger = logging.getLogger("live_stats")

PROXY = "http://FuTUVMvrSTa9cYM8:XZbc7POb6z75bzCb_country-au@geo.iproyal.com:12321"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# Column mapping for Footywire live stats table
# [Number, Name, Kicks, Handballs, Disposals, Marks, Goals, Behinds, Tackles, Hitouts, FreesFor, FreesAgainst]
STAT_COLUMNS = {
    0: "number",
    1: "name",
    2: "kicks",
    3: "handballs",
    4: "disposals",
    5: "marks",
    6: "goals",
    7: "behinds",
    8: "tackles",
    9: "hitouts",
    10: "frees_for",
    11: "frees_against",
}

# Cache
_cache = {}
CACHE_TTL = 60  # 1 minute for live data


def get_live_stats(match_id: int) -> dict:
    """Get live player stats for a Footywire match.

    Returns dict with:
        - players: list of {name, number, team, disposals, kicks, handballs, marks, goals, tackles, ...}
        - timestamp: when data was fetched
        - match_id: the footywire match ID
    """
    cache_key = f"live:{match_id}"
    if cache_key in _cache:
        cached_time, cached_data = _cache[cache_key]
        if time.time() - cached_time < CACHE_TTL:
            return cached_data

    session = Session(impersonate="chrome131")
    session.proxies = {"http": PROXY, "https": PROXY}

    try:
        url = f"https://www.footywire.com/afl/footy/live_stats?mid={match_id}"
        resp = session.get(url, headers={"User-Agent": UA}, timeout=15)

        if resp.status_code != 200:
            return {"players": [], "error": f"HTTP {resp.status_code}", "match_id": match_id}

        html = resp.text

        # Parse team names from h3 or title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html)
        title = title_match.group(1) if title_match else ""

        # Find team sections — Footywire splits by team
        # Look for team indicators in the HTML structure
        team_sections = re.split(r'<tr[^>]*class="[^"]*tbtotal[^"]*"[^>]*>', html)

        # Parse all player rows
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
        players = []
        current_team = "Team 1"
        team_count = 0

        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) >= 12:
                clean = [re.sub(r'<[^>]+>', '', c).strip().replace('&nbsp;', '') for c in cells]

                # Check if this is a team total row
                if clean[1] and ('total' in clean[1].lower() or clean[1].startswith('Total')):
                    team_count += 1
                    current_team = f"Team {team_count + 1}"
                    continue

                # Check if it's a player row (has name + numbers)
                if clean[1] and not clean[1].isdigit() and len(clean[1]) > 1:
                    try:
                        stats = {}
                        for idx, col_name in STAT_COLUMNS.items():
                            if idx < len(clean):
                                if col_name in ("name", "number"):
                                    stats[col_name] = clean[idx]
                                else:
                                    try:
                                        stats[col_name] = int(clean[idx]) if clean[idx] else 0
                                    except ValueError:
                                        stats[col_name] = 0

                        name = stats.get("name", "")
                        if name and len(name) > 2 and name not in ("No", "Yes", "Total", "Totals") and not name.isdigit():
                            stats["team_idx"] = team_count
                            players.append(stats)
                    except Exception:
                        continue

        # Try to assign team names from player names or page content
        result = {
            "players": sorted(players, key=lambda p: -p.get("disposals", 0)),
            "timestamp": time.time(),
            "match_id": match_id,
            "title": title,
            "total_players": len(players),
        }

        _cache[cache_key] = (time.time(), result)
        return result

    except Exception as e:
        logger.error(f"Live stats error: {e}")
        return {"players": [], "error": str(e), "match_id": match_id}
    finally:
        session.close()


def find_current_match_id() -> Optional[int]:
    """Find the Footywire match ID for the current/next AFL match.
    Scrapes the Footywire homepage for live match links."""
    session = Session(impersonate="chrome131")
    session.proxies = {"http": PROXY, "https": PROXY}
    try:
        resp = session.get("https://www.footywire.com/afl/footy/ft_match_list",
                          headers={"User-Agent": UA}, timeout=15)
        # Find live_stats links
        matches = re.findall(r'live_stats\?mid=(\d+)', resp.text)
        if matches:
            return int(matches[0])
        return None
    except:
        return None
    finally:
        session.close()

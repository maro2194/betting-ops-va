"""
VPS-side client for the Token Farm service (Mini PC).

The token farm handles browser-dependent auth:
  - Sportsbet: patchright + Kasada bypass → JWT tokens
  - bet365: Camoufox → session cookies

Communication: REST API over WireGuard VPN tunnel.
Farm URL configured via TOKEN_FARM_URL env var.
"""
import asyncio
import logging
import os
import re
import time

import httpx

logger = logging.getLogger(__name__)

FARM_URL = os.getenv("TOKEN_FARM_URL", "http://172.16.1.6:9000")
FARM_API_KEY = os.getenv("TOKEN_FARM_API_KEY", "botops-farm-2026")
FARM_TIMEOUT = 120  # Browser logins with proxy + geoip can take 60-90 seconds


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {FARM_API_KEY}",
        "Content-Type": "application/json",
    }


# ─── Sportsbet ───────────────────────────────────────────────────────────────

async def sportsbet_login(email: str, password: str, proxy_url: str | None = None) -> dict:
    """Request browser-based Sportsbet login from the token farm.

    Returns: {success, access_token, refresh_token, customer_id, account_number, expires_at}
    """
    try:
        payload = {"email": email, "password": password}
        if proxy_url:
            payload["proxy_url"] = proxy_url
        async with httpx.AsyncClient(timeout=FARM_TIMEOUT) as client:
            resp = await client.post(
                f"{FARM_URL}/auth/sportsbet/login",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm returned HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        data.setdefault("success", True)
        return data
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout (browser login took too long)"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Token farm error: {e}"}


async def sportsbet_get_promos(email: str, password: str, proxy_url: str | None = None) -> dict:
    """Fetch Sportsbet promotions via the token farm browser.

    The farm logs in via patchright and intercepts vouchers/freebets/promos
    responses from SB's frontend — ensures Kasada cookies are present.

    Returns unified promo structure:
      {success, boost_tokens, bonus_back_tokens, deposit_match_tokens, promos, redeemed}
    """
    try:
        payload = {"email": email, "password": password}
        if proxy_url:
            payload["proxy_url"] = proxy_url
        async with httpx.AsyncClient(timeout=FARM_TIMEOUT) as client:
            resp = await client.post(
                f"{FARM_URL}/auth/sportsbet/promos",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {
                "success": False,
                "error": f"Farm returned HTTP {resp.status_code}: {resp.text[:200]}",
                "boost_tokens": 0,
                "bonus_back_tokens": 0,
                "deposit_match_tokens": 0,
                "promos": [],
                "redeemed": [],
            }
        data = resp.json()
        data.setdefault("success", True)
        return data
    except httpx.ConnectError:
        return {
            "success": False,
            "error": "Token farm unreachable — check WireGuard tunnel",
            "boost_tokens": 0,
            "bonus_back_tokens": 0,
            "deposit_match_tokens": 0,
            "promos": [],
            "redeemed": [],
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Token farm promos error: {e}",
            "boost_tokens": 0,
            "bonus_back_tokens": 0,
            "deposit_match_tokens": 0,
            "promos": [],
            "redeemed": [],
        }


async def sportsbet_refresh(refresh_token: str, proxy_url: str | None = None) -> dict:
    """Refresh Sportsbet token via the farm (no browser needed, but routes through farm)."""
    try:
        payload = {"refresh_token": refresh_token}
        if proxy_url:
            payload["proxy_url"] = proxy_url
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{FARM_URL}/auth/sportsbet/refresh",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Refresh failed: HTTP {resp.status_code}"}
        data = resp.json()
        data.setdefault("success", True)
        return data
    except Exception as e:
        return {"success": False, "error": f"Token farm refresh error: {e}"}


# ─── PointsBet ───────────────────────────────────────────────────────────────

async def pointsbet_login(email: str, password: str, proxy_url: str | None = None) -> dict:
    """Request browser-based PointsBet login from the token farm."""
    try:
        payload = {"email": email, "password": password}
        if proxy_url:
            payload["proxy_url"] = proxy_url
        async with httpx.AsyncClient(timeout=FARM_TIMEOUT) as client:
            resp = await client.post(
                f"{FARM_URL}/auth/pointsbet/login",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm returned HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        data.setdefault("success", True)
        return data
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout (PointsBet browser login took too long)"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Token farm error: {e}"}


# ─── bet365 ──────────────────────────────────────────────────────────────────

async def bet365_get_sessions() -> dict:
    """Get active bet365 sessions from the token farm."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{FARM_URL}/auth/bet365/status",
                headers=_headers(),
            )
        if resp.status_code == 200:
            return resp.json()
        return {"active_sessions": 0, "sessions": {}}
    except Exception:
        return {"active_sessions": 0, "sessions": {}}


async def bet365_get_or_login(username: str, password: str, proxy_url: str | None = None) -> dict:
    """Reuse an existing alive AND logged-in bet365 session, or login fresh."""
    status = await bet365_get_sessions()
    sessions = status.get("sessions", {})
    for sid, info in sessions.items():
        if info.get("alive") and info.get("username") == username:
            # Verify session is actually logged in (not just alive browser)
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        f"{FARM_URL}/bet365/debug/{sid}",
                        headers=_headers(),
                    )
                if resp.status_code == 200:
                    debug = resp.json()
                    page_text = debug.get("text", "")
                    if "My Bets" in page_text and "Log In" not in page_text:
                        logger.info(f"bet365: reusing logged-in session {sid} for {username}")
                        return {"success": True, "session_id": sid, "reused": True}
                    else:
                        logger.info(f"bet365: session {sid} alive but logged out, skipping")
            except Exception as e:
                logger.warning(f"bet365: debug check failed for {sid}: {e}")
    logger.info(f"bet365: no logged-in session for {username}, doing fresh login")
    return await bet365_login(username, password, proxy_url=proxy_url)


async def bet365_login(username: str, password: str, proxy_url: str | None = None) -> dict:
    """Request Camoufox-based bet365 login from the token farm.

    Returns session data (cookies, tokens) for bet365 API calls.
    """
    try:
        payload = {"username": username, "password": password}
        if proxy_url:
            payload["proxy_url"] = proxy_url
        async with httpx.AsyncClient(timeout=FARM_TIMEOUT) as client:
            resp = await client.post(
                f"{FARM_URL}/auth/bet365/login",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm returned HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        data.setdefault("success", True)
        return data
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout (Camoufox login took too long)"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Token farm error: {e}"}


async def bet365_place_bet(session_id: str, bet_payload: dict) -> dict:
    """Send bet placement instruction to token farm (browser-based for bet365).

    bet365 has no REST betting API — placement is browser-automated.
    The token farm's Camoufox browser executes the bet.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{FARM_URL}/bet365/place-bet",
                headers=_headers(),
                json={"session_id": session_id, "bet": bet_payload},
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm bet365 place failed: HTTP {resp.status_code}"}
        return resp.json()
    except Exception as e:
        return {"success": False, "error": f"bet365 placement error: {e}"}


async def bet365_megaboost(session_id: str, sport: str, match_team: str, stake: float = 20, boost_index: int = 0) -> dict:
    """Place a Mega Boost on bet365 via Token Farm browser."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{FARM_URL}/bet365/megaboost",
                headers=_headers(),
                json={
                    "session_id": session_id,
                    "sport": sport,
                    "match_team": match_team,
                    "stake": stake,
                    "boost_index": boost_index,
                },
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm megaboost failed: HTTP {resp.status_code}: {resp.text[:200]}"}
        return resp.json()
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout — megaboost placement took too long"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Megaboost error: {e}"}


async def bet365_click(session_id: str, text: str, index: int = 0, exact: bool = True) -> dict:
    """Click a text element on the current bet365 page."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{FARM_URL}/bet365/click",
                headers=_headers(),
                json={"session_id": session_id, "text": text, "index": index, "exact": exact},
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Click failed: HTTP {resp.status_code}"}
        return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


async def bet365_navigate(session_id: str, url: str) -> dict:
    """Navigate a bet365 session to a URL."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{FARM_URL}/bet365/navigate",
                headers=_headers(),
                json={"session_id": session_id, "url": url},
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Navigate failed: HTTP {resp.status_code}"}
        return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def _is_odds(s: str) -> bool:
    """Check if a string looks like decimal odds (e.g. '8.50', '15.00', '1.91')."""
    if "." not in s:
        return False
    if s.startswith("+") or s.startswith("-"):
        return False
    if s.startswith("O ") or s.startswith("U "):
        return False
    try:
        v = float(s)
        return 1.01 <= v <= 10000
    except (ValueError, TypeError):
        return False


def _parse_boosts_match_page(page_text: str, match_team: str = "") -> list[dict]:
    """Parse boosts from a match page using '$X stake returns' markers (reverse scan)."""
    lines = page_text.split("\n")
    boosts = []
    match_name = ""
    for i, ln in enumerate(lines):
        s = ln.strip()
        if " v " in s and not any(kw in s.lower() for kw in ["to score", "to record", "result", "points", "disposals", "to win"]):
            match_name = s
            break
        if s == "V" and i >= 1 and i + 1 < len(lines):
            prev = lines[i - 1].strip()
            nxt = lines[i + 1].strip()
            if prev and nxt and not _is_odds(prev) and not _is_odds(nxt) and not prev.isdigit():
                match_name = f"{prev} v {nxt}"
                break

    for i, ln in enumerate(lines):
        s = ln.strip()
        if not (s.startswith("$") and "stake returns" in s):
            continue
        returns_match = re.search(r"\$[\d.]+ stake returns \$([\d.]+)", s)
        returns_val = returns_match.group(0) if returns_match else s
        j = i - 1
        boosted_odds = ""
        original_odds = ""
        while j >= 0:
            c = lines[j].strip()
            j -= 1
            if not c:
                continue
            if _is_odds(c):
                if not boosted_odds:
                    boosted_odds = c
                elif not original_odds:
                    original_odds = c
                    break
            elif c.isdigit():
                continue
            else:
                break
        if not original_odds or not boosted_odds:
            continue
        text_lines = []
        k = j
        while k >= 0:
            c = lines[k].strip()
            k -= 1
            if not c:
                continue
            if c.startswith("$"):
                break
            if _is_odds(c):
                break
            if c.isdigit():
                continue
            if c.startswith("+") or c.startswith("-") or c.startswith("O ") or c.startswith("U "):
                break
            if c.startswith("View ") and "more leg" in c:
                continue
            text_lines.insert(0, c)
        if not text_lines:
            continue
        title = text_lines[0]
        legs = text_lines[1:]
        description = " / ".join(legs) if legs else title
        if match_team:
            mt = match_team.lower()
            text_block = (match_name + " " + title + " " + description).lower()
            if mt not in text_block:
                continue
        boosts.append({
            "index": len(boosts),
            "type": "BET_BOOST",
            "title": title,
            "match": match_name,
            "date": "",
            "description": description,
            "legs": legs,
            "original_odds": original_odds,
            "boosted_odds": boosted_odds,
            "stake": 20,
            "returns": returns_val,
        })
    return boosts


def _parse_boosts_from_text(page_text: str, match_team: str = "", match_page: bool = False) -> list[dict]:
    """Parse boost cards from bet365 page text.

    On Bet Boost tabs (match_page=False): forward scan for title + legs + odds pair.
    On match pages (match_page=True): reverse scan from '$X stake returns' markers.
    """
    if match_page:
        return _parse_boosts_match_page(page_text, match_team)

    lines = page_text.split("\n")
    boosts = []
    current_match = ""
    current_date = ""
    skip_nav = {"Main", "SGM +", "More", "Futures", "Ladder", "Bet", "Boost",
                "Trending", "Most Used", "A-Z", "Upcoming", "Racing", "All Sports",
                "In-Play", "My Bets", "Offers", "Next To Jump"}
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line in skip_nav:
            i += 1
            continue
        if " v " in line and not any(kw in line.lower() for kw in ["to score", "to record", "result", "points", "disposals", "to win"]):
            current_match = line
            i += 1
            if i < len(lines) and re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s", lines[i].strip()):
                current_date = lines[i].strip()
                i += 1
            continue
        if re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s", line):
            current_date = line
            i += 1
            continue
        if _is_odds(line):
            i += 1
            continue
        if line.startswith("View ") and "more leg" in line:
            i += 1
            continue
        if line.startswith("$"):
            i += 1
            continue
        title = line
        text_lines = [title]
        j = i + 1
        while j < len(lines):
            nxt = lines[j].strip()
            if not nxt:
                j += 1
                continue
            if _is_odds(nxt) and j + 1 < len(lines) and _is_odds(lines[j + 1].strip()):
                original_odds = nxt
                boosted_odds = lines[j + 1].strip()
                legs = text_lines[1:]
                description = " / ".join(legs) if legs else title
                boost = {
                    "index": len(boosts),
                    "type": "BET_BOOST",
                    "title": title,
                    "match": current_match,
                    "date": current_date,
                    "description": description,
                    "legs": legs,
                    "original_odds": original_odds,
                    "boosted_odds": boosted_odds,
                    "stake": 20,
                    "returns": "",
                }
                if match_team:
                    mt = match_team.lower()
                    text_block = (current_match + " " + title + " " + description).lower()
                    if mt not in text_block:
                        i = j + 2
                        break
                boosts.append(boost)
                i = j + 2
                break
            if " v " in nxt and not any(kw in nxt.lower() for kw in ["to score", "to record", "result", "points", "disposals", "to win"]):
                i = j
                break
            if nxt.startswith("View ") and "more leg" in nxt:
                j += 1
                continue
            if nxt.startswith("$"):
                j += 1
                continue
            text_lines.append(nxt)
            j += 1
        else:
            i = j
    return boosts


async def bet365_scan_boosts(session_id: str, sport: str = "HOME", match_team: str = "") -> dict:
    """List available boosts on bet365. For sport pages, navigates to the Bet Boost tab."""
    try:
        if sport and sport.upper() != "HOME":
            await bet365_navigate(session_id, "https://www.bet365.com.au/#/HO/")
            await asyncio.sleep(2)
            sport_map = {"AFL": "AFL", "NRL": "NRL", "NBA": "NBA", "UFC": "MMA"}
            click_text = sport_map.get(sport.upper(), sport)
            use_exact = click_text in ("AFL", "NRL", "NBA", "MMA")
            result = await bet365_click(session_id, click_text, index=0, exact=use_exact)
            if not result.get("clicked"):
                return {"success": False, "error": f"Could not click {click_text} on homepage", "boosts": []}
            await asyncio.sleep(2)
            page_text = result.get("text", "")
            is_match_page = False
            boost_click = await bet365_click(session_id, "Bet Boost", index=0, exact=False)
            if boost_click.get("clicked"):
                await asyncio.sleep(1)
                boost_click2 = await bet365_click(session_id, "Bet Boost", index=0, exact=False)
                page_text = boost_click2.get("text", "") or boost_click.get("text", "") or page_text
            else:
                page_text = boost_click.get("text", "") or page_text
                is_match_page = True
            if not page_text:
                return {"success": False, "error": "No page text after navigation", "boosts": []}
            logger.info(f"Boost scan page text ({len(page_text)} chars, match_page={is_match_page}): {page_text[:1000]}")
            boosts = _parse_boosts_from_text(page_text, match_team, match_page=is_match_page)
            logger.info(f"Parsed {len(boosts)} boosts from page text")
            return {"success": True, "boosts": boosts, "count": len(boosts), "page_text_preview": page_text[:500]}
        else:
            params = {}
            if match_team:
                params["match_team"] = match_team
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(
                    f"{FARM_URL}/bet365/list-boosts/{session_id}",
                    headers=_headers(),
                    params=params,
                )
            if resp.status_code != 200:
                return {"success": False, "error": f"Farm scan-boosts failed: HTTP {resp.status_code}: {resp.text[:200]}"}
            return resp.json()
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout — boost scan took too long"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Scan boosts error: {e}"}


async def bet365_megaboost_all(sport: str, match_team: str, stake: float = 20, boost_index: int = 0,
                                accounts: list[str] | None = None) -> dict:
    """Place megaboost across all configured bet365 accounts via Token Farm.

    This fires all accounts in parallel on the token farm.
    Timeout is long because 5 parallel browser sessions take time.
    """
    try:
        payload = {
            "sport": sport,
            "match_team": match_team,
            "stake": stake,
            "boost_index": boost_index,
        }
        if accounts:
            payload["accounts"] = accounts
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{FARM_URL}/bet365/megaboost-all",
                headers=_headers(),
                json=payload,
            )
        if resp.status_code != 200:
            return {"success": False, "error": f"Farm megaboost-all failed: HTTP {resp.status_code}: {resp.text[:200]}"}
        return resp.json()
    except httpx.TimeoutException:
        return {"success": False, "error": "Token farm timeout — megaboost-all took too long (>5min)"}
    except httpx.ConnectError:
        return {"success": False, "error": "Token farm unreachable — check WireGuard tunnel"}
    except Exception as e:
        return {"success": False, "error": f"Megaboost-all error: {e}"}


# ─── Health ──────────────────────────────────────────────────────────────────

async def health() -> dict:
    """Check token farm health and status of managed sessions."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{FARM_URL}/health", headers=_headers())
        if resp.status_code != 200:
            return {"status": "error", "detail": f"HTTP {resp.status_code}"}
        return resp.json()
    except httpx.ConnectError:
        return {"status": "unreachable", "detail": "Token farm unreachable"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

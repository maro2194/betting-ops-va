"""
TAB Tokens — SGM Saver token discovery + multi-account execution.

Uses existing BotOps TAB auth (Bearer tokens from session_manager).
Calls TAB promotions API to fetch savers, authenticated pricing for decoTokens.
"""
import csv
import io
import json
import logging
import re
import time
import uuid

from curl_cffi.requests import Session

logger = logging.getLogger(__name__)

# TAB Promotions API
PROMO_BASE = "https://api.beta.tab.com.au/v1/tab-promotions-service"
PRICING_AUTH_URL = "https://api.beta.tab.com.au/v1/pricing-service/accounts/{account}/enquiry"
MATCHES_URL = "https://api.beta.tab.com.au/v1/tab-info-service/sports/{sport}/competitions/{competition}/matches?jurisdiction=QLD"
SGM_MARKETS_URL = "https://api.beta.tab.com.au/v1/bff-sports/sports/{sport}/competitions/{competition}/matches/{match_id}/same-game-multi?jurisdiction=QLD&platform=web&version=2&loggedIn=true"
BETSLIP_URL = "https://webapi.tab.com.au/v1/tab-betting-service/accounts/{account}/betslip?jurisdiction=QLD"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"


def _bearer_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Origin": "https://www.tab.com.au",
        "Referer": "https://www.tab.com.au/",
    }


def _tabcorp_headers(legacy_token: str) -> dict:
    """TabcorpAuth header — required for promotions-service (Bearer/ROPC tokens rejected)."""
    return {
        "TabcorpAuth": legacy_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Origin": "https://www.tab.com.au",
        "Referer": "https://www.tab.com.au/",
    }


def _betslip_headers(legacy_token: str) -> dict:
    return {
        "TabcorpAuth": legacy_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Origin": "https://www.tab.com.au",
        "Referer": "https://www.tab.com.au/",
    }


def _make_session(proxy_url: str | None = None) -> Session:
    s = Session(impersonate="chrome142")
    if proxy_url:
        s.proxies = {"http": proxy_url, "https": proxy_url}
    return s


# ── Saver Token Fetching ──────────────────────────────────────────

def get_sgm_savers(token: str, account_number: str, proxy_url: str | None = None, legacy_token: str | None = None) -> list[dict]:
    """Fetch SGM saver tokens for an account. Uses legacy TabcorpAuth if available."""
    url = f"{PROMO_BASE}/accounts/{account_number}/bet-tokens"
    s = _make_session(proxy_url)
    try:
        headers = _tabcorp_headers(legacy_token) if legacy_token else _bearer_headers(token)
        resp = s.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.warning("Bet tokens failed for %s: %d", account_number, resp.status_code)
            return []

        data = resp.json()
        savers = []
        for offer in data.get("exclusiveOffers", []):
            if offer.get("type") != "SGM":
                continue
            for group in offer.get("tokenGroups", []):
                for tk in group.get("betTokens", []):
                    if tk.get("promotionType") != "SAVER" or tk.get("type") != "SPORTS":
                        continue
                    savers.append({
                        "offer_name": offer.get("name", ""),
                        "match": tk.get("usageRestrictions", ""),
                        "max_reward": tk.get("maxReward", 0),
                        "valid_till": tk.get("validTill", ""),
                        "token_group_id": tk.get("tokenGroupId", ""),
                        "event_url": tk.get("eventUrl", ""),
                        "remaining": tk.get("remainingNumberOfBetTokens", 0),
                    })
        return savers
    except Exception as e:
        logger.error("get_sgm_savers error for %s: %s", account_number, e)
        return []
    finally:
        s.close()


# ── Match & Proposition Resolution ────────────────────────────────

def get_matches(token: str, sport: str, competition: str, proxy_url: str | None = None) -> list[dict]:
    url = MATCHES_URL.format(sport=sport, competition=competition)
    logger.info("get_matches: url=%s proxy=%s", url, (proxy_url or "none")[:50])
    s = _make_session(proxy_url)
    try:
        resp = s.get(url, headers={"Accept": "application/json", "User-Agent": UA}, timeout=10)
        logger.info("get_matches: status=%s content_type=%s len=%d", resp.status_code, resp.headers.get("content-type", "?"), len(resp.text))
        if resp.status_code != 200:
            logger.warning("get_matches: non-200 response: %s", resp.text[:200])
            return []
        matches = resp.json().get("matches", [])
        logger.info("get_matches: found %d matches: %s", len(matches), [m.get("name", "?") for m in matches[:5]])
        return matches
    except Exception as e:
        logger.error("get_matches: exception: %s", e)
        return []
    finally:
        s.close()


def find_match(matches: list[dict], match_name: str) -> dict | None:
    name_lower = match_name.lower().strip()
    for m in matches:
        m_name = m.get("name", "")
        if name_lower in m_name.lower() or m_name.lower() in name_lower:
            return m
        contestants = [c.get("name", "").lower() for c in m.get("contestants", [])]
        parts = re.split(r'\s+v\s+|\s+vs\s+', name_lower)
        if len(parts) == 2 and all(any(p.strip() in c for c in contestants) for p in parts):
            return m
    return None


def get_sgm_markets(token: str, match: dict, sport: str, competition: str, proxy_url: str | None = None) -> list[dict]:
    """Get all markets for a match. Uses public _links.markets endpoint (no auth needed)."""
    s = _make_session(proxy_url)
    try:
        # Primary: use match _links.markets (public, reliable)
        markets_url = match.get("_links", {}).get("markets", "")
        if markets_url:
            resp = s.get(markets_url, headers={"Accept": "application/json", "User-Agent": UA}, timeout=15, params={"jurisdiction": "QLD"})
            if resp.status_code == 200:
                markets = resp.json().get("markets", [])
                logger.info("Got %d markets for %s", len(markets), match.get("name", "?"))
                return markets

        # Fallback: bff-sports SGM endpoint
        match_id = match.get("id") or match.get("name", "").replace(" ", "")
        url = SGM_MARKETS_URL.format(sport=sport, competition=competition, match_id=match_id)
        resp = s.get(url, headers=_bearer_headers(token), timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("markets", data.get("categories", []))

        logger.warning("get_sgm_markets failed for %s: %d", match.get("name"), resp.status_code)
        return []
    except Exception as e:
        logger.error("get_sgm_markets error: %s", e)
        return []
    finally:
        s.close()


SHORT_LEG_TYPES = {
    "pyot": {"keywords": ["pick your own total"], "direction": "under"},
    "pyol": {"keywords": ["pick your own line"], "direction": "opposition"},
    "pyot 1st half": {"keywords": ["pick your own total", "1st half"], "direction": "under"},
    "pyol 1st half": {"keywords": ["pick your own line", "1st half"], "direction": "opposition"},
    "under points": {"keywords": ["pick your own total"], "direction": "under"},
    "opposition line": {"keywords": ["pick your own line"], "direction": "opposition"},
    "under points 1st half": {"keywords": ["pick your own total", "1st half"], "direction": "under"},
    "opposition line 1st half": {"keywords": ["pick your own line", "1st half"], "direction": "opposition"},
}


def auto_pick_short_leg(short_type: str, markets: list, match: dict, tryscorer_team: str | None = None) -> dict:
    """
    Auto-pick the safest (lowest odds >= $1.10) proposition for a short leg type.

    short_type: "PYOT", "PYOL", "PYOT 1st Half", "PYOL 1st Half", etc.
    tryscorer_team: If provided, PYOL picks the OPPOSITE team's + line.
                    e.g. if tryscorer is from "Penrith", pick "Bulldogs +X.X"
    """
    st = short_type.strip().lower()
    config = SHORT_LEG_TYPES.get(st)
    if not config:
        return {"error": f"Unknown short leg type: {short_type}", "leg_description": short_type}

    keywords = config["keywords"]
    direction = config["direction"]

    # Figure out opposition team for PYOL
    contestants = match.get("contestants", [])
    contestant_names = [c.get("name", "").lower() for c in contestants]
    opposition_team = None
    if tryscorer_team and direction == "opposition":
        ts = tryscorer_team.lower()
        # Find which contestant the tryscorer belongs to, then pick the OTHER one
        # Handle TAB abbreviations: "Pen"=Penrith, "Bdg"=Bulldogs, "Man"=Manly, etc.
        tryscorer_contestant = None
        for c in contestant_names:
            # Match: abbreviation is prefix, substring, or first letter matches + >50% chars match
            if c.startswith(ts) or ts in c:
                tryscorer_contestant = c
                break
            # Fuzzy: check if abbreviation chars appear in order in the team name
            ci = 0
            for ch in ts:
                idx = c.find(ch, ci)
                if idx >= 0:
                    ci = idx + 1
                else:
                    break
            else:
                tryscorer_contestant = c
                break
        # Opposition = the OTHER team
        for c in contestant_names:
            if c != tryscorer_contestant:
                opposition_team = c
                break
        logger.info("PYOL opposition: tryscorer=%s -> team=%s -> opposition=%s", tryscorer_team, tryscorer_contestant, opposition_team)

    best = None
    best_odds = 999
    best_market = ""
    fallback = None
    fallback_odds = 999
    fallback_market = ""

    for market in markets:
        bet_option = market.get("betOption", "").lower()

        # Check all keywords match the market name
        if not all(kw in bet_option for kw in keywords):
            continue

        # For non-1st-half types, skip 1st half markets
        if "1st half" not in st and "1st half" in bet_option:
            continue

        for prop in market.get("propositions", []):
            if prop.get("bettingStatus", "") != "Open":
                continue

            odds = float(prop.get("returnWin", 0))
            if odds < 1.10:
                continue

            name = prop.get("name", "").lower()

            # For "under" direction: only pick Under propositions
            if direction == "under" and "under" not in name:
                continue

            # For "opposition" direction: pick the + line for the opposite team from tryscorer
            if direction == "opposition":
                if "+" not in name:
                    continue
                # If we know opposition team, prefer their line
                if opposition_team and opposition_team not in name:
                    # Track as fallback (any + line)
                    if odds < fallback_odds:
                        fallback = prop
                        fallback_odds = odds
                        fallback_market = market.get("betOption", "")
                    continue

            if odds < best_odds:
                best_odds = odds
                best = prop
                best_market = market.get("betOption", "")

    # Fallback: if no opposition-specific line found, use any + line
    if not best and fallback:
        logger.info("PYOL fallback: no opposition line found, using any + line: %s", fallback.get("name"))
        best = fallback
        best_odds = fallback_odds
        best_market = fallback_market

    if not best:
        return {"error": f"No valid prop found for {short_type} (>= $1.10)", "leg_description": short_type}

    return {
        "proposition_id": int(best.get("id", best.get("numberId"))),
        "name": best.get("name", ""),
        "odds": float(best.get("returnWin", 0)),
        "market": best_market,
        "status": best.get("bettingStatus", "Unknown"),
        "leg_description": f"[AUTO] {short_type}: {best.get('name', '')} @ ${float(best.get('returnWin', 0)):.2f}",
    }


def resolve_proposition(leg_desc: str, markets: list, match: dict, tryscorer_team: str | None = None) -> dict:
    """Resolve a leg description to a proposition ID + odds.
    tryscorer_team: team name of the tryscorer (used for PYOL opposition logic).
    """
    leg_desc = leg_desc.strip()

    # Auto-pick short leg types (PYOT, PYOL, etc.)
    if leg_desc.strip().lower() in SHORT_LEG_TYPES:
        return auto_pick_short_leg(leg_desc, markets, match, tryscorer_team=tryscorer_team)

    # Direct prop ID
    if leg_desc.isdigit():
        prop_id = int(leg_desc)
        for market in markets:
            for prop in market.get("propositions", []):
                pid = int(prop.get("id", prop.get("numberId", 0)))
                if pid == prop_id:
                    return {"proposition_id": pid, "name": prop.get("name", ""), "odds": float(prop.get("returnWin", 0)), "market": market.get("betOption", ""), "status": prop.get("bettingStatus", "Unknown"), "leg_description": leg_desc}
        return {"error": f"Prop ID {prop_id} not found", "leg_description": leg_desc}

    desc_lower = leg_desc.lower()

    # Head To Head
    if "head to head" in desc_lower or "h2h" in desc_lower:
        team = desc_lower.replace("head to head", "").replace("h2h", "").strip()
        for market in markets:
            if market.get("betOption", "").lower() in ("head to head", "match result"):
                for prop in market.get("propositions", []):
                    if team in prop.get("name", "").lower():
                        return {"proposition_id": int(prop.get("id", prop.get("numberId"))), "name": prop.get("name", ""), "odds": float(prop.get("returnWin", 0)), "market": market.get("betOption", ""), "status": prop.get("bettingStatus", "Unknown"), "leg_description": leg_desc}

    # Try scorer — solo player only (skip "Either Player" / combo markets)
    if "try" in desc_lower or "score" in desc_lower:
        player = desc_lower.replace("to score a try", "").replace("anytime try", "").replace("to score 2+ tries", "").strip()
        is_2plus = "2+" in desc_lower
        # Only match solo player markets — NOT "Either Player" combos
        if is_2plus:
            allowed_markets = ["to score 2+ tries"]
            blocked_markets = ["either", "combo", "first", "last"]
        else:
            allowed_markets = ["to score a try", "anytime try scorer"]
            blocked_markets = ["either", "combo", "2+", "first try", "last try"]

        player_parts = player.split()
        last_name = player_parts[-1].lower() if player_parts else player.lower()

        for market in markets:
            bo = market.get("betOption", "").lower()
            if not any(am in bo for am in allowed_markets):
                continue
            # Skip combo/either markets
            if any(bm in bo for bm in blocked_markets):
                continue
            for prop in market.get("propositions", []):
                prop_name = prop.get("name", "").lower()
                # Must be a single player prop (no "/" which indicates combo)
                if "/" in prop_name:
                    continue
                prop_clean = prop_name.split("(")[0].strip()
                if last_name in prop_clean:
                    return {"proposition_id": int(prop.get("id", prop.get("numberId"))), "name": prop.get("name", ""), "odds": float(prop.get("returnWin", 0)), "market": market.get("betOption", ""), "status": prop.get("bettingStatus", "Unknown"), "leg_description": leg_desc}

    # Line: "Bulldogs +16.5"
    line_match = re.match(r'^(.+?)\s*([+-]\d+\.?\d*)$', leg_desc.strip())
    if line_match:
        team, line_val = line_match.group(1).strip().lower(), line_match.group(2)
        for market in markets:
            if any(k in market.get("betOption", "").lower() for k in ("line", "handicap", "pick your own")):
                for prop in market.get("propositions", []):
                    pn = prop.get("name", "").lower()
                    if team in pn and line_val in pn:
                        return {"proposition_id": int(prop.get("id", prop.get("numberId"))), "name": prop.get("name", ""), "odds": float(prop.get("returnWin", 0)), "market": market.get("betOption", ""), "status": prop.get("bettingStatus", "Unknown"), "leg_description": leg_desc}

    # Total: "Under 54.5", "Over 40.5 Pick Your Own Total", "Over 18.5 1st Half Pick Your Own Total"
    total_match = re.match(r'^(under|over)\s+(\d+\.?\d*)', desc_lower)
    if total_match:
        direction, value = total_match.group(1), total_match.group(2)
        is_first_half = "1st half" in desc_lower or "first half" in desc_lower
        for market in markets:
            bo = market.get("betOption", "").lower()
            if not any(k in bo for k in ("total", "pick your own")):
                continue
            # Filter by half if specified
            if is_first_half and "1st half" not in bo and "first half" not in bo:
                continue
            if not is_first_half and ("1st half" in bo or "first half" in bo):
                continue
            for prop in market.get("propositions", []):
                pn = prop.get("name", "").lower()
                if direction in pn and value in pn:
                    return {"proposition_id": int(prop.get("id", prop.get("numberId"))), "name": prop.get("name", ""), "odds": float(prop.get("returnWin", 0)), "market": market.get("betOption", ""), "status": prop.get("bettingStatus", "Unknown"), "leg_description": leg_desc}

    # Generic fallback
    for market in markets:
        for prop in market.get("propositions", []):
            if desc_lower in prop.get("name", "").lower():
                return {"proposition_id": int(prop.get("id", prop.get("numberId"))), "name": prop.get("name", ""), "odds": float(prop.get("returnWin", 0)), "market": market.get("betOption", ""), "status": prop.get("bettingStatus", "Unknown"), "leg_description": leg_desc}

    return {"error": f"Could not resolve: {leg_desc}", "leg_description": leg_desc}


# ── SGM Pricing with decoTokens ───────────────────────────────────

def get_sgm_price(token: str, account_number: str, legs: list[dict], proxy_url: str | None = None, legacy_token: str | None = None) -> dict:
    """Get SGM price from authenticated pricing endpoint (returns decoTokens for savers).
    Uses Bearer for pricing (it works), TabcorpAuth for promotions."""
    url = PRICING_AUTH_URL.format(account=account_number)
    # Deduplicate propositions — TAB returns 502 if same prop ID appears twice
    seen_ids = set()
    unique_props = []
    for leg in legs:
        pid = leg["proposition_id"]
        if pid not in seen_ids:
            seen_ids.add(pid)
            unique_props.append({"type": "WIN", "propositionId": pid})
    payload = {
        "clientDetails": {"jurisdiction": "QLD", "channel": "web"},
        "bets": [{
            "type": "FIXED_ODDS",
            "legs": [{
                "type": "SAME_GAME_MULTI",
                "propositions": unique_props,
            }],
        }],
        "returnValidationMatrix": True,
    }
    s = _make_session(proxy_url)
    try:
        # Authenticated pricing requires TabcorpAuth (legacy token), NOT Bearer
        auth_method = "TabcorpAuth" if legacy_token else "Bearer"
        prop_ids = [p["propositionId"] for p in payload["bets"][0]["legs"][0]["propositions"]]
        logger.info("Pricing for %s using %s (legacy_len=%s) props=%s", account_number, auth_method, len(legacy_token or ""), prop_ids)
        headers = _tabcorp_headers(legacy_token) if legacy_token else _bearer_headers(token)
        resp = s.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code != 200:
            body_preview = resp.text[:300] if resp.text else "empty"
            logger.error("Pricing failed %s for account %s: %s", resp.status_code, account_number, body_preview)
            return {"error": f"Pricing failed: {resp.status_code}", "combined_odds": None, "deco_tokens": []}

        data = resp.json()
        bet = data.get("bets", [{}])[0]
        status = bet.get("status", "unknown")
        if status != "ok":
            return {"error": f"Pricing status: {status}", "combined_odds": None, "deco_tokens": []}

        leg = bet.get("legs", [{}])[0]
        combined = leg.get("odds", {}).get("decimal")
        deco_tokens = data.get("decoTokens", [])
        leg_deco = leg.get("decoToken")
        bet_token_results = bet.get("betTokenResults", [])

        return {
            "combined_odds": combined,
            "deco_tokens": deco_tokens,
            "leg_deco_token": leg_deco,
            "bet_token_results": bet_token_results,
            "has_saver": any(
                tk.get("promotionType") == "SAVER"
                for btr in bet_token_results
                for tg in btr.get("tokenGroups", [])
                for tk in tg.get("betTokens", [])
            ),
        }
    except Exception as e:
        return {"error": str(e), "combined_odds": None, "deco_tokens": []}
    finally:
        s.close()


# ── Bet Placement ─────────────────────────────────────────────────

def place_sgm_with_saver(
    legacy_token: str, account_number: str,
    legs: list[dict], stake: float, combined_odds: str,
    deco_tokens: list[str], leg_deco_token: str | None,
    proxy_url: str | None = None,
    token_group_id: str | None = None,
) -> dict:
    """Place an SGM bet with saver decoTokens + tokenGroupId."""
    sgm_leg = {
        "type": "SAME_GAME_MULTI",
        "odds": combined_odds,
        "propositions": [
            {"type": "WIN", "propositionId": leg["proposition_id"], "odds": f"{leg['odds']:.2f}"}
            for leg in legs
        ],
    }
    if leg_deco_token:
        sgm_leg["decoToken"] = leg_deco_token

    bet = {
        "type": "FIXED_ODDS",
        "stake": f"${stake:.2f}",
        "legs": [sgm_leg],
        "enableToteGuarantee": False,
        "enableMultiplier": False,
        "source": "sports.betting.match",
    }
    # tokenGroupId activates the saver — this is the critical field
    if token_group_id:
        bet["tokenGroupId"] = token_group_id

    payload = {
        "transactionId": str(uuid.uuid4()),
        "bets": [bet],
    }
    if deco_tokens:
        payload["decoTokens"] = deco_tokens

    url = BETSLIP_URL.format(account=account_number)
    s = _make_session(proxy_url)
    try:
        logger.info("Placing SGM bet: acct=%s stake=$%.2f odds=%s deco=%d legs=%d",
                     account_number, stake, combined_odds, len(deco_tokens), len(legs))
        resp = s.post(url, headers=_betslip_headers(legacy_token), json=payload, timeout=15)
        logger.info("Betslip response: %d %s", resp.status_code, resp.text[:500])

        if resp.status_code == 201:
            data = resp.json()
            bet_data = data.get("bets", [{}])[0]
            bet_id = bet_data.get("betId")
            tsn = bet_data.get("ticketSerialNumber", bet_data.get("tsn"))
            # Check for errors in 201 response (TAB returns 201 even for some rejections)
            errors = data.get("errors", []) + bet_data.get("errors", [])
            if errors:
                error_msg = "; ".join(e.get("message", e.get("code", str(e))) for e in errors)
                logger.warning("SGM bet rejected despite 201: %s", error_msg)
                return {"success": False, "error": error_msg, "status_code": 201}
            logger.info("SGM bet placed! acct=%s bet_id=%s tsn=%s", account_number, bet_id, tsn)
            return {"success": True, "bet_id": bet_id, "tsn": tsn, "status_code": 201, "details": data}
        else:
            logger.warning("SGM bet failed: acct=%s status=%d %s", account_number, resp.status_code, resp.text[:300])
            return {"success": False, "error": resp.text[:300], "status_code": resp.status_code}
    except Exception as e:
        logger.error("SGM bet exception: acct=%s %s", account_number, e)
        return {"success": False, "error": str(e)}
    finally:
        s.close()


# ── CSV Parsing ───────────────────────────────────────────────────

def parse_bets_csv(csv_content: str) -> list[dict]:
    """
    Parse SGM bets from CSV. Supports two formats:

    Format 1 (explicit legs):
        group,match,leg1,leg2,leg3[,sport,competition]

    Format 2 (tryscorer + auto-pick short legs):
        account,match,tryscorer,short1,short2[,sport,competition]

    Returns list of {group, match, legs, sport, competition}.
    """
    lines = csv_content.strip().split("\n")
    # Auto-add header if first line looks like data (starts with a group letter, not a column name)
    if lines and not any(h in lines[0].lower() for h in ["account", "group", "match", "tryscorer", "leg1"]):
        # Detect format: if 8 columns assume tryscorer format, else explicit legs
        cols = lines[0].split(",")
        if len(cols) >= 8:
            lines.insert(0, "account,match,tryscorer,market,short1,short2,sport,competition")
        elif len(cols) >= 6:
            lines.insert(0, "group,match,leg1,leg2,leg3,sport,competition")
        else:
            lines.insert(0, "group,match,leg1,leg2,leg3")

    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    bets = []
    for row in reader:
        # Detect format by column names
        has_tryscorer = "tryscorer" in row
        group = (row.get("account") or row.get("group", "A")).strip()
        match = (row.get("match") or row.get("game", "")).strip()
        sport = row.get("sport", "Rugby League").strip()
        competition = row.get("competition", "NRL").strip()

        if not match:
            continue

        if has_tryscorer:
            # Format 2: tryscorer + short legs
            tryscorer = row.get("tryscorer", "").strip()
            market = row.get("market", "").strip()
            if not tryscorer:
                continue

            # Build tryscorer leg description
            if market and "2+" in market:
                try_leg = f"{tryscorer} To Score 2+ Tries"
            elif market and "try" in market.lower():
                try_leg = f"{tryscorer} To Score a Try"
            else:
                try_leg = f"{tryscorer} To Score a Try"

            # Collect short legs — split on " & " since each column can contain multiple legs
            short1 = row.get("short1", row.get("short_leg_1", "")).strip()
            short2 = row.get("short2", row.get("short_leg_2", "")).strip()
            legs = [try_leg]
            for s in [short1, short2]:
                if not s:
                    continue
                for part in s.split(" & "):
                    part = part.strip()
                    if part:
                        legs.append(part)
        else:
            # Format 1: explicit legs
            legs = []
            for key in sorted(row.keys()):
                if key.startswith("leg") and key[3:].isdigit():
                    val = row[key].strip()
                    if val:
                        legs.append(val)

        if len(legs) < 3:
            continue
        bets.append({"group": group, "match": match, "legs": legs, "sport": sport, "competition": competition})
    return bets

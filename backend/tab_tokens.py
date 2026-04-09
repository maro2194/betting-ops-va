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

def get_sgm_savers(token: str, account_number: str, proxy_url: str | None = None) -> list[dict]:
    """Fetch SGM saver tokens for an account."""
    url = f"{PROMO_BASE}/accounts/{account_number}/bet-tokens"
    s = _make_session(proxy_url)
    try:
        resp = s.get(url, headers=_bearer_headers(token), timeout=15)
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
    s = _make_session(proxy_url)
    try:
        resp = s.get(url, headers={"Accept": "application/json", "User-Agent": UA}, timeout=15)
        if resp.status_code != 200:
            return []
        return resp.json().get("matches", [])
    except Exception:
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
    """Get SGM-eligible markets for a match."""
    match_id = match.get("id") or match.get("name", "").replace(" ", "")
    # Try the bff-sports SGM endpoint first
    url = SGM_MARKETS_URL.format(sport=sport, competition=competition, match_id=match_id)
    s = _make_session(proxy_url)
    try:
        resp = s.get(url, headers=_bearer_headers(token), timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("markets", data.get("categories", []))

        # Fallback: get markets from match _links
        markets_url = match.get("_links", {}).get("markets", "")
        if markets_url:
            resp = s.get(markets_url, headers={"Accept": "application/json", "User-Agent": UA}, timeout=15, params={"jurisdiction": "QLD"})
            if resp.status_code == 200:
                return resp.json().get("markets", [])
        return []
    except Exception as e:
        logger.error("get_sgm_markets error: %s", e)
        return []
    finally:
        s.close()


def resolve_proposition(leg_desc: str, markets: list, match: dict) -> dict:
    """Resolve a leg description to a proposition ID + odds."""
    leg_desc = leg_desc.strip()

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

    # Try scorer
    if "try" in desc_lower or "score" in desc_lower:
        player = desc_lower.replace("to score a try", "").replace("anytime try", "").replace("to score 2+ tries", "").strip()
        market_name = "To Score 2+ Tries" if "2+" in desc_lower else "To Score a Try"
        for market in markets:
            if market_name.lower() in market.get("betOption", "").lower():
                for prop in market.get("propositions", []):
                    prop_clean = prop.get("name", "").lower().split("(")[0].strip()
                    if player in prop_clean or prop_clean in player:
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

    # Total: "Under 54.5"
    total_match = re.match(r'^(under|over)\s+(\d+\.?\d*)$', desc_lower)
    if total_match:
        direction, value = total_match.group(1), total_match.group(2)
        for market in markets:
            if any(k in market.get("betOption", "").lower() for k in ("total", "pick your own")):
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

def get_sgm_price(token: str, account_number: str, legs: list[dict], proxy_url: str | None = None) -> dict:
    """Get SGM price from authenticated pricing endpoint (returns decoTokens for savers)."""
    url = PRICING_AUTH_URL.format(account=account_number)
    payload = {
        "clientDetails": {"jurisdiction": "QLD", "channel": "web"},
        "bets": [{
            "type": "FIXED_ODDS",
            "legs": [{
                "type": "SAME_GAME_MULTI",
                "propositions": [
                    {"type": "WIN", "propositionId": leg["proposition_id"]}
                    for leg in legs
                ],
            }],
        }],
        "returnValidationMatrix": True,
    }
    s = _make_session(proxy_url)
    try:
        resp = s.post(url, headers=_bearer_headers(token), json=payload, timeout=15)
        if resp.status_code != 200:
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
) -> dict:
    """Place an SGM bet with saver decoTokens."""
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

    payload = {
        "bets": [{
            "type": "FIXED_ODDS",
            "betType": "WIN",
            "subType": "SAME_GAME_MULTI",
            "stake": f"{stake:.2f}",
            "legs": [sgm_leg],
        }],
        "transactionId": str(uuid.uuid4()),
    }
    if deco_tokens:
        payload["decoTokens"] = deco_tokens

    url = BETSLIP_URL.format(account=account_number)
    s = _make_session(proxy_url)
    try:
        resp = s.post(url, headers=_betslip_headers(legacy_token), json=payload, timeout=15)
        if resp.status_code == 201:
            data = resp.json()
            bet_id = data.get("bets", [{}])[0].get("betId")
            return {"success": True, "bet_id": bet_id, "status_code": 201}
        else:
            return {"success": False, "error": resp.text[:300], "status_code": resp.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        s.close()


# ── CSV Parsing ───────────────────────────────────────────────────

def parse_bets_csv(csv_content: str) -> list[dict]:
    """Parse SGM bets from CSV. Returns list of {group, match, legs, sport, competition}."""
    reader = csv.DictReader(io.StringIO(csv_content.strip()))
    bets = []
    for row in reader:
        group = row.get("group", "A").strip()
        match = row.get("match", "").strip()
        sport = row.get("sport", "Rugby League").strip()
        competition = row.get("competition", "NRL").strip()
        if not match:
            continue
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

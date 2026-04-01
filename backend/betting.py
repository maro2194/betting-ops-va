"""
TAB Betting API - Balance, SGM markets, pricing, bet placement, history.
Uses curl_cffi for TLS fingerprinting (standard requests gets blocked).

Auth model:
- Auth0 ROPC token (Bearer): used for api.beta.tab.com.au (pricing, matches, SGM markets)
- Legacy TabcorpAuth token: used for webapi.tab.com.au (betslip, history, account ops)
  Obtained via POST /v1/account-service/tab/authenticate
"""
import uuid
import logging
from typing import Optional

from curl_cffi.requests import Session

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"

# TAB API endpoints
AUTHENTICATE_URL = "https://webapi.tab.com.au/v1/account-service/tab/authenticate"
ACCOUNT_LIST_URL = "https://webapi.tab.com.au/v1/account-service/tab/customers/{customer_id}/account-list?channel=TABCOMAU"
BETSLIP_URL = "https://webapi.tab.com.au/v1/tab-betting-service/accounts/{account}/betslip?jurisdiction=QLD"
MY_BETS_URL = "https://webapi.tab.com.au/v1/account-service/tab/accounts/{account}/my-bets"
SGM_MARKETS_URL = "https://api.beta.tab.com.au/v1/bff-sports/sports/{sport}/competitions/{competition}/matches/{match_id}/same-game-multi?jurisdiction=QLD&platform=web&version=2&loggedIn=true"
PRICING_URL = "https://api.beta.tab.com.au/v1/pricing-service/enquiry"
MATCHES_URL = "https://api.beta.tab.com.au/v1/tab-info-service/sports/{sport}/competitions/{competition}/matches?jurisdiction=QLD"


def _make_session(proxy_url: Optional[str] = None) -> Session:
    """Create a curl_cffi session with proxy."""
    session = Session(impersonate="chrome142")
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
    return session


def _api_beta_headers(token: str) -> dict:
    """Headers for api.beta.tab.com.au (Bearer auth)."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Origin": "https://www.tab.com.au",
        "Referer": "https://www.tab.com.au/",
    }


def _webapi_headers(legacy_token: str) -> dict:
    """Headers for webapi.tab.com.au (TabcorpAuth)."""
    return {
        "TabcorpAuth": legacy_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Origin": "https://www.tab.com.au",
        "Referer": "https://www.tab.com.au/",
    }


# ─── Legacy Authentication ──────────────────────────────────────────────────

def legacy_authenticate(account_number: str, password: str, proxy_url: Optional[str] = None) -> dict:
    """Authenticate with TAB's legacy endpoint to get a TabcorpAuth token.
    This token is required for betslip, history, and other webapi.tab.com.au endpoints.
    Returns dict with legacy_token, account_number, customer_id, jurisdiction, balance."""
    session = _make_session(proxy_url)
    try:
        resp = session.post(AUTHENTICATE_URL, json={
            "accountNumber": int(account_number),
            "password": password,
            "channel": "TABCOMAU",
        }, headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }, timeout=15)

        logger.info(f"Legacy authenticate: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            auth = data.get("authentication", {})
            legacy_token = auth.get("token", "")
            if not legacy_token:
                raise Exception("No token in authenticate response")

            details = data.get("accountDetails", {})
            return {
                "legacy_token": legacy_token,
                "account_number": str(data.get("accountNumber", account_number)),
                "customer_id": str(data.get("customerId", "")),
                "jurisdiction": data.get("jurisdiction", ""),
                "account_balance": details.get("accountBalance", ""),
                "withdrawal_balance": details.get("withdrawalBalance", ""),
            }
        elif resp.status_code == 401:
            raise Exception("Legacy auth failed: invalid credentials")
        else:
            raise Exception(f"Legacy auth failed: {resp.status_code} - {resp.text[:300]}")
    finally:
        session.close()


# ─── Account Info & Balance ──────────────────────────────────────────────────

def get_account_info(token: str, customer_id: str, proxy_url: Optional[str] = None) -> dict:
    """Get account info (including real account number and balance) from customer ID.
    Uses the account-list endpoint which works with standard ROPC tokens."""
    session = _make_session(proxy_url)
    try:
        url = ACCOUNT_LIST_URL.format(customer_id=customer_id)
        resp = session.get(url, headers=_api_beta_headers(token), timeout=15)
        logger.info(f"Account list response: {resp.status_code} {resp.text[:300]}")
        if resp.status_code == 200:
            data = resp.json()
            accounts = data.get("accounts", [])
            if not accounts:
                raise Exception("No accounts found for this customer")
            acct = accounts[0]
            return {
                "account_number": str(acct.get("accountNumber", "")),
                "account_balance": acct.get("accountBalance", "$0.00"),
                "withdrawal_balance": acct.get("withdrawalBalance", "$0.00"),
                "jurisdiction": acct.get("accountJurisdiction", ""),
                "email_verified": data.get("emailVerified", False),
            }
        elif resp.status_code == 500 and "INVALID_CHANNEL" in resp.text:
            raise Exception("Account list failed: missing channel parameter")
        elif resp.status_code == 401:
            raise Exception("Token expired or invalid (401)")
        else:
            raise Exception(f"Account list failed: {resp.status_code} - {resp.text[:200]}")
    finally:
        session.close()


def get_balance(token: str, customer_id: str, proxy_url: Optional[str] = None) -> dict:
    """Get account balance via the account-list endpoint.
    Note: customer_id param is the customerId from JWT (not the account number)."""
    info = get_account_info(token, customer_id, proxy_url)
    return {
        "account_balance": info["account_balance"],
        "withdrawal_balance": info["withdrawal_balance"],
    }


# ─── Matches & SGM ──────────────────────────────────────────────────────────

def get_matches(token: str, proxy_url: Optional[str] = None,
                sport: str = "AFL Football", competition: str = "AFL") -> list:
    """Get upcoming matches for a sport/competition."""
    session = _make_session(proxy_url)
    session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        url = MATCHES_URL.format(sport=sport.replace(" ", "%20"), competition=competition)
        resp = session.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("matches", data.get("events", []))
            else:
                items = []

            matches = []
            for m in items:
                match_id = m.get("id", m.get("matchId", m.get("name", "")))
                match_name = m.get("matchName", m.get("name", str(match_id)))
                start_time = m.get("startTime", m.get("advertisedStartTime", ""))
                matches.append({
                    "match_id": str(match_id),
                    "match_name": match_name,
                    "match_name_url": match_name.replace(" ", "%20"),
                    "competition": competition,
                    "start_time": start_time,
                })
            return matches
        else:
            logger.warning(f"Matches fetch failed: {resp.status_code}")
            return []
    finally:
        session.close()


def get_match_markets(token: str, match_id: str, proxy_url: Optional[str] = None,
                      sport: str = "AFL Football", competition: str = "AFL") -> dict:
    """Get regular match markets (H2H, Line, Totals, etc.) from tab-info-service.
    match_id can be short ID (MiavPhi) or full name (Miami v Philadelphia)."""
    session = _make_session(proxy_url)
    session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        url = f"https://api.beta.tab.com.au/v1/tab-info-service/sports/{sport.replace(' ', '%20')}/competitions/{competition}/matches/{match_id.replace(' ', '%20')}?jurisdiction=QLD"
        resp = session.get(url, timeout=15)

        # If 404 with short ID, look up full match name
        if resp.status_code == 404 and " " not in match_id and "%20" not in match_id:
            matches = get_matches(token, proxy_url, sport, competition)
            for m in matches:
                if m["match_id"] == match_id:
                    url = f"https://api.beta.tab.com.au/v1/tab-info-service/sports/{sport.replace(' ', '%20')}/competitions/{competition}/matches/{m['match_name_url']}?jurisdiction=QLD"
                    resp = session.get(url, timeout=15)
                    break

        if resp.status_code == 200:
            return resp.json()
        else:
            raise Exception(f"Match markets fetch failed: {resp.status_code} - {resp.text[:300]}")
    finally:
        session.close()


def get_sgm_propositions(token: str, match_id: str, proxy_url: Optional[str] = None,
                         sport: str = "AFL Football", competition: str = "AFL") -> dict:
    """Get SGM propositions for a match."""
    session = _make_session(proxy_url)
    session.headers.update(_api_beta_headers(token))
    try:
        url = SGM_MARKETS_URL.format(
            sport=sport.replace(" ", "%20"),
            competition=competition,
            match_id=match_id,
        )
        resp = session.get(url, timeout=15)

        if resp.status_code == 404 and " " not in match_id and "%20" not in match_id:
            matches = get_matches(token, proxy_url, sport, competition)
            for m in matches:
                if m["match_id"] == match_id:
                    url = SGM_MARKETS_URL.format(
                        sport=sport.replace(" ", "%20"),
                        competition=competition,
                        match_id=m["match_name_url"],
                    )
                    resp = session.get(url, timeout=15)
                    break

        if resp.status_code == 200:
            return resp.json()
        else:
            raise Exception(f"SGM markets fetch failed: {resp.status_code} - {resp.text[:300]}")
    finally:
        session.close()


# ─── Pricing ─────────────────────────────────────────────────────────────────

def price_check(token: str, propositions: list[dict], stake: str = "10.00",
                bet_type: str = "SAME_GAME_MULTI", proxy_url: Optional[str] = None) -> dict:
    """Get combined odds from pricing service (api.beta, Bearer auth)."""
    session = _make_session(proxy_url)
    session.headers.update(_api_beta_headers(token))
    try:
        props_payload = []
        for p in propositions:
            props_payload.append({
                "type": "WIN",
                "propositionId": p["propositionId"] if isinstance(p.get("propositionId"), int) else int(p.get("propositionId", p.get("proposition_id", 0))),
                "odds": f"{float(p['odds']):.2f}",
            })

        payload = {
            "uuid": str(uuid.uuid4()),
            "clientVersion": "1",
            "clientDetails": {"channel": "TABCOMAU", "jurisdiction": "QLD"},
            "bets": [{
                "type": "FIXED_ODDS",
                "stake": f"{float(stake):.2f}",
                "legs": [{
                    "type": bet_type,
                    "propositions": props_payload,
                }],
                "enableToteGuarantee": False,
                "enableMultiplier": False,
                "source": "sports.betting.match",
            }],
        }

        resp = session.post(PRICING_URL, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            try:
                combined_odds = data["bets"][0]["legs"][0]["odds"]["decimal"]
            except (KeyError, IndexError):
                combined_odds = None

            return {
                "combined_odds": str(combined_odds) if combined_odds else None,
                "raw_response": data,
            }
        else:
            raise Exception(f"Price check failed: {resp.status_code} - {resp.text[:300]}")
    finally:
        session.close()


# ─── Bet Placement ───────────────────────────────────────────────────────────

def _extract_ticket(data: dict) -> str | None:
    """Extract ticket/confirmation number from TAB bet response."""
    ticket = (
        data.get("ticketNumber") or data.get("ticket")
        or data.get("transactionId") or data.get("id")
        or data.get("receiptNumber") or data.get("confirmationNumber")
    )
    if not ticket:
        bets = data.get("bets", [])
        if bets and isinstance(bets, list):
            b = bets[0] if isinstance(bets[0], dict) else {}
            ticket = (
                b.get("ticketNumber") or b.get("ticket")
                or b.get("ticketSerialNumber") or b.get("betId") or b.get("id")
            )
    if not ticket:
        import json as _json
        import re
        raw = _json.dumps(data)
        m = re.search(r'"(?:ticket\w*|betId|receipt\w*|confirmation\w*)":\s*"?(\d{8,})"?', raw, re.IGNORECASE)
        if m:
            ticket = m.group(1)
    return str(ticket) if ticket else None


def place_sgm_bet(legacy_token: str, account_number: str, propositions: list[dict],
                  combined_odds: str, stake: str, proxy_url: Optional[str] = None) -> dict:
    """Place an SGM bet using legacy TabcorpAuth token."""
    session = _make_session(proxy_url)
    session.headers.update(_webapi_headers(legacy_token))
    try:
        props_payload = []
        for p in propositions:
            props_payload.append({
                "type": "WIN",
                "propositionId": int(p.get("propositionId", p.get("proposition_id", 0))),
                "odds": f"{float(p['odds']):.2f}",
            })

        payload = {
            "bets": [{
                "type": "FIXED_ODDS",
                "betType": "WIN",
                "stake": f"{float(stake):.2f}",
                "legs": [{
                    "type": "SAME_GAME_MULTI",
                    "odds": f"{float(combined_odds):.2f}",
                    "propositions": props_payload,
                }],
            }],
            "transactionId": str(uuid.uuid4()),
        }

        url = BETSLIP_URL.format(account=account_number)
        resp = session.post(url, json=payload, timeout=15)

        logger.info(f"Place SGM response: {resp.status_code} {resp.text[:500]}")

        if resp.status_code == 201:
            data = resp.json()
            # TAB returns 201 even for rejected bets — check for errors
            top_errors = data.get("errors", [])
            bet_errors = []
            for b in data.get("bets", []):
                bet_errors.extend(b.get("errors", []))
            all_errors = top_errors + bet_errors
            if all_errors:
                error_msg = "; ".join(e.get("message", e.get("code", str(e))) for e in all_errors)
                return {"success": False, "error": error_msg, "details": data}
            return {
                "success": True,
                "ticket_number": _extract_ticket(data),
                "account_balance": data.get("accountBalance"),
                "details": data,
            }
        elif resp.status_code == 200:
            data = resp.json()
            failures = data.get("betFailures", [])
            error_msg = "; ".join(f.get("reason", str(f)) for f in failures) if failures else "Bet rejected"
            return {"success": False, "error": error_msg, "details": data}
        else:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
    finally:
        session.close()


def place_multi_bet(legacy_token: str, account_number: str, legs: list[dict],
                    stake: str, proxy_url: Optional[str] = None) -> dict:
    """Place a cross-game multi bet using legacy TabcorpAuth token."""
    session = _make_session(proxy_url)
    session.headers.update(_webapi_headers(legacy_token))
    try:
        legs_payload = []
        for leg in legs:
            legs_payload.append({
                "type": "WIN",
                "propositionId": int(leg.get("propositionId", leg.get("proposition_id", 0))),
                "odds": f"{float(leg['odds']):.2f}",
            })

        payload = {
            "bets": [{
                "type": "FIXED_ODDS",
                "betType": "WIN",
                "stake": f"{float(stake):.2f}",
                "legs": legs_payload,
            }],
            "transactionId": str(uuid.uuid4()),
        }

        url = BETSLIP_URL.format(account=account_number)
        resp = session.post(url, json=payload, timeout=15)

        logger.info(f"Place multi response: {resp.status_code} {resp.text[:500]}")

        if resp.status_code == 201:
            data = resp.json()
            # TAB returns 201 even for rejected bets — check for errors
            top_errors = data.get("errors", [])
            bet_errors = []
            for b in data.get("bets", []):
                bet_errors.extend(b.get("errors", []))
            all_errors = top_errors + bet_errors
            if all_errors:
                error_msg = "; ".join(e.get("message", e.get("code", str(e))) for e in all_errors)
                return {"success": False, "error": error_msg, "details": data}
            return {
                "success": True,
                "ticket_number": _extract_ticket(data),
                "account_balance": data.get("accountBalance"),
                "details": data,
            }
        elif resp.status_code == 200:
            data = resp.json()
            failures = data.get("betFailures", [])
            error_msg = "; ".join(f.get("reason", str(f)) for f in failures) if failures else "Bet rejected"
            return {"success": False, "error": error_msg, "details": data}
        else:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
    finally:
        session.close()


# ─── Bet History & Results ───────────────────────────────────────────────────

def get_my_bets(legacy_token: str, account_number: str, proxy_url: Optional[str] = None,
                count: int = 20, status: str = "ALL") -> dict:
    """Get bet history via the my-bets API endpoint.
    Returns parsed list of bets with status, stake, odds, legs, etc."""
    session = _make_session(proxy_url)
    session.headers.update(_webapi_headers(legacy_token))
    try:
        url = f"{MY_BETS_URL.format(account=account_number)}?count={count}&status={status}"
        resp = session.get(url, timeout=15)

        logger.info(f"My bets response: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            transactions = data.get("transactions", [])
            bets = []
            for tx in transactions:
                legs = []
                for leg in tx.get("legs", []):
                    legs.append(leg.get("eventName", ""))

                bets.append({
                    "type": tx.get("betTypeDetails", "Unknown"),
                    "status": tx.get("status", "Unknown"),
                    "stake": tx.get("stake", "$0.00"),
                    "odds": tx.get("odds", "0"),
                    "payout": tx.get("return", "$0.00"),
                    "description": tx.get("eventNameSummary", tx.get("eventName", "")),
                    "timestamp": tx.get("transactionTime", ""),
                    "event_date": tx.get("eventDate", ""),
                    "tsn": tx.get("tsn", ""),
                    "transaction_ref": tx.get("transactionReference"),
                    "is_bonus_bet": tx.get("isBonusBet", False),
                    "is_cash_out": tx.get("isCashOut", False),
                    "legs": legs,
                })

            next_link = data.get("_links", {}).get("next", "")
            return {
                "bets": bets,
                "has_more": bool(next_link),
                "next_url": next_link,
            }
        elif resp.status_code == 401:
            raise Exception("Legacy token expired or invalid (401)")
        else:
            raise Exception(f"My bets failed: {resp.status_code} - {resp.text[:300]}")
    finally:
        session.close()

"""
TAB platform client for multi-bookie allocation framework.

Wraps the existing login.py and betting.py modules — does NOT replace them.
CSB, Expload, and all existing TAB flows continue to use those modules directly.
This client is only used by the allocation CSV processor (csv_processor.py).
"""
import logging
import time
import uuid

from .base import PlatformClient

logger = logging.getLogger(__name__)


class TabClient(PlatformClient):
    """TAB platform client — wraps existing login/betting modules for allocation."""

    async def login(self, email: str, password: str, proxy_url: str, brand_config: dict) -> dict:
        """Login to TAB — always does a fresh login for reliable token+proxy pairing."""
        try:
            # Always do a fresh login to ensure token and proxy are paired correctly
            from login import browser_login
            from betting import legacy_authenticate, get_account_info

            result = await browser_login(email, password, proxy_url)
            if not result.get("token"):
                return {"success": False, "error": f"TAB login failed: {result.get('error', 'no token')}"}

            access_token = result["token"]
            customer_id = result.get("account_number", "")  # browser_login returns custId as account_number

            # Resolve real account number from customer ID
            account_number = ""
            try:
                info = get_account_info(access_token, customer_id, proxy_url)
                account_number = info.get("account_number", customer_id)
            except Exception as e:
                logger.warning(f"TAB account info failed: {e}")
                account_number = customer_id

            # Legacy auth for bet placement
            legacy_token = ""
            if account_number:
                try:
                    leg_result = legacy_authenticate(account_number, password, proxy_url)
                    legacy_token = leg_result.get("legacy_token", "")
                except Exception as e:
                    logger.warning(f"TAB legacy auth failed: {e}")

            return {
                "success": True,
                "platform": "tab",
                "brand": "tab",
                "access_token": access_token,
                "legacy_token": legacy_token,
                "account_number": account_number,
                "customer_id": customer_id,
                "email": email,
                "password": password,
                "proxy_url": proxy_url,
                "expires_at": time.time() + 28800,
            }
        except Exception as e:
            logger.error(f"TAB login error: {e}")
            return {"success": False, "error": f"TAB login failed: {e}"}

    def is_session_valid(self, session: dict) -> bool:
        return time.time() < (session.get("expires_at", 0) - 300)

    async def find_race(self, session: dict, track: str, race_number: int) -> dict | None:
        """Find a TAB race by track name and race number."""
        try:
            from betting import _make_session, _api_beta_headers

            access_token = session.get("access_token")
            proxy_url = session.get("proxy_url")

            # TAB API is geo-restricted — MUST use AU proxy
            s = _make_session(proxy_url)
            s.headers.update(_api_beta_headers(access_token))

            # TAB racing endpoint — NO bearer auth needed, just proxy for geo
            # (same as get_matches in betting.py — public endpoint via AU proxy)
            s2 = _make_session(proxy_url)
            s2.headers.update({"Accept": "application/json", "User-Agent": "Mozilla/5.0"})

            url = "https://api.beta.tab.com.au/v1/tab-info-service/racing/dates/today/meetings?jurisdiction=NSW"
            meetings = []
            try:
                resp = s2.get(url, timeout=15)
                ct = resp.headers.get("content-type", "")
                logger.info(f"TAB racing API: HTTP {resp.status_code} CT={ct[:30]}")
                if "html" in ct:
                    logger.error(f"TAB racing returned HTML (geo-blocked?)")
                elif resp.status_code == 200:
                    import json as _json
                    data = _json.loads(resp.text)
                    meetings = data.get("meetings", [])
                    logger.info(f"TAB racing: {len(meetings)} meetings found")
                else:
                    logger.error(f"TAB racing error: {resp.text[:200]}")
            except Exception as e:
                logger.error(f"TAB racing request failed: {e}")
            finally:
                s2.close()

            s.close()

            if not meetings:
                logger.error(f"TAB find_race: no meetings found (proxy={bool(proxy_url)})")
                return None

            track_lower = track.lower().strip()
            logger.info(f"TAB find_race: searching {len(meetings)} meetings for '{track}' R{race_number}")

            for meeting in meetings:
                meeting_name = (meeting.get("meetingName", "") or "").lower()
                venue_name = (meeting.get("venueMnemonic", "") or "").lower()

                if track_lower in meeting_name or meeting_name in track_lower or track_lower == venue_name:
                    logger.info(f"TAB: matched meeting '{meeting.get('meetingName')}'")
                    for race in meeting.get("races", []):
                        if race.get("raceNumber") == race_number:
                            return {
                                "event_id": race.get("raceNumber"),
                                "track": meeting.get("meetingName", track),
                                "race_number": race_number,
                                "meeting_date": meeting.get("meetingDate"),
                                "venue": meeting.get("venueMnemonic"),
                                "race_type": meeting.get("raceType", "R"),
                                "sell_code": race.get("sellCode", {}).get("meetingCode"),
                                "race_status": race.get("raceStatus"),
                                "_race_data": race,
                            }

            # Log what meetings were available for debugging
            available = [m.get("meetingName", "?") for m in meetings[:20]]
            logger.warning(f"TAB: track '{track}' not found. Available: {available}")
            return None
        except Exception as e:
            logger.error(f"TAB find_race error: {e}")
            return None

    async def get_runners(self, session: dict, race_info: dict) -> list[dict]:
        """Get runners with odds from TAB race data."""
        try:
            from betting import _make_session, _api_beta_headers

            access_token = session.get("access_token")
            proxy_url = session.get("proxy_url")

            race_data = race_info.get("_race_data", {})
            race_link = None
            # Check for _links in race data (sometimes a direct URL)
            links = race_data.get("_links", {})
            if isinstance(links, dict):
                for lk, lv in links.items():
                    href = lv.get("href", "") if isinstance(lv, dict) else (lv if isinstance(lv, str) else "")
                    if href and "races" in href and href.startswith("http"):
                        race_link = href
                        break

            if not race_link:
                # Build URL from meeting info — format: /meetings/RACETYPE/VENUE/races/NUM
                venue = race_info.get("venue", "")
                race_num = race_info.get("race_number", 0)
                meeting_date = race_info.get("meeting_date", "")
                race_type = race_info.get("race_type", "R")
                race_link = f"https://api.beta.tab.com.au/v1/tab-info-service/racing/dates/{meeting_date}/meetings/{race_type}/{venue}/races/{race_num}?jurisdiction=NSW"

            # TAB race detail is also public via AU proxy (no Bearer needed)
            logger.info(f"TAB get_runners: fetching {race_link[:80]}")
            s = _make_session(proxy_url)
            s.headers.update({"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            resp = s.get(race_link, timeout=15)
            s.close()

            logger.info(f"TAB get_runners: HTTP {resp.status_code}")
            if resp.status_code != 200:
                logger.warning(f"TAB get_runners: HTTP {resp.status_code} for {race_link[:80]}")
                return []

            data = resp.json()
            runners = []

            raw_runners = data.get("runners", [])
            logger.info(f"TAB get_runners: {len(raw_runners)} raw runners, keys={list(data.keys())[:10]}")

            for r in raw_runners:
                if r.get("scratched"):
                    continue
                fixed = r.get("fixedOdds", {})
                prop_id = fixed.get("propositionId") or fixed.get("propositionNumber", 0)
                win_odds = fixed.get("returnWin", 0)

                # Also check for parimutuel odds as fallback
                pari_win = r.get("parimutuel", {}).get("returnWin", 0)

                if prop_id and win_odds:
                    runners.append({
                        "id": prop_id,
                        "name": r.get("runnerName", ""),
                        "number": r.get("runnerNumber", 0),
                        "odds": win_odds,
                        "price_num": None,
                        "price_den": None,
                    })
                elif not fixed and pari_win:
                    # No fixed odds available, log it
                    logger.info(f"TAB: runner {r.get('runnerName')} has pari odds={pari_win} but no fixedOdds")

            if not runners and raw_runners:
                # Log first runner's structure for debugging
                sample = raw_runners[0]
                logger.warning(f"TAB: 0 runners with fixed odds. Sample keys: {list(sample.keys())}, fixedOdds: {sample.get('fixedOdds')}")

            logger.info(f"TAB get_runners: returning {len(runners)} runners with fixed odds")
            return runners
        except Exception as e:
            logger.error(f"TAB get_runners error: {e}")
            return []

    async def place_bet(self, session: dict, race_info: dict, runner: dict,
                        stake: float, stake_type: str, brand_config: dict) -> dict:
        """Place a racing bet on TAB via the existing betslip endpoint."""
        try:
            from betting import _make_session, _webapi_headers

            legacy_token = session.get("legacy_token")
            account_number = session.get("account_number")
            proxy_url = session.get("proxy_url")

            logger.info(f"TAB place_bet: acct={account_number} legacy={'YES' if legacy_token else 'NO'} proxy={'YES' if proxy_url else 'NO'} prop={runner.get('id')} odds={runner.get('odds')}")

            if not legacy_token:
                return {"success": False, "error": "No TAB legacy token"}
            if not account_number:
                return {"success": False, "error": "No TAB account number"}

            prop_id = runner.get("id")
            odds = runner.get("odds", 0)
            if not prop_id:
                return {"success": False, "error": "No proposition ID"}

            bet_type = "WIN" if stake_type.lower() in ("win", "w", "cash") else "PLACE"

            payload = {
                "bets": [{
                    "type": "FIXED_ODDS",
                    "betType": bet_type,
                    "stake": f"{stake:.2f}",
                    "legs": [{
                        "type": bet_type,
                        "propositionId": int(prop_id),
                        "odds": f"{float(odds):.2f}",
                    }],
                }],
                "transactionId": str(uuid.uuid4()),
            }

            s = _make_session(proxy_url)
            s.headers.update(_webapi_headers(legacy_token))
            url = f"https://webapi.tab.com.au/v1/tab-betting-service/accounts/{account_number}/betslip?jurisdiction=QLD"
            resp = s.post(url, json=payload, timeout=15)
            s.close()

            logger.info(f"TAB place_bet: HTTP {resp.status_code}")

            if resp.status_code == 201:
                data = resp.json()
                errors = data.get("errors", [])
                for b in data.get("bets", []):
                    errors.extend(b.get("errors", []))
                if errors:
                    msg = "; ".join(e.get("message", str(e)) for e in errors)
                    return {"success": False, "error": msg, "raw": data}

                # Extract ticket number
                ticket = None
                for b in data.get("bets", []):
                    for l in b.get("legs", []):
                        for p in l.get("propositions", []):
                            if p.get("ticketNumber"):
                                ticket = p["ticketNumber"]
                                break

                return {
                    "success": True,
                    "bet_id": ticket or "",
                    "receipt": ticket or "",
                    "stake": stake,
                    "balance": data.get("accountBalance"),
                    "raw": data,
                }
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}

        except Exception as e:
            logger.error(f"TAB place_bet error: {e}")
            return {"success": False, "error": f"TAB placement failed: {e}"}

    async def get_balances(self, session: dict) -> dict:
        bal = await self.get_balance(session)
        return {"cash": bal, "bonus": 0.0}

    async def get_balance(self, session: dict) -> float:
        try:
            from betting import get_balance as tab_balance
            access_token = session.get("access_token")
            customer_id = session.get("customer_id")
            proxy_url = session.get("proxy_url")
            result = tab_balance(access_token, customer_id, proxy_url)
            if isinstance(result, dict):
                bal_str = result.get("account_balance", result.get("accountBalance", "0"))
                # Strip $ and parse
                return float(str(bal_str).replace("$", "").replace(",", "") or "0")
            return float(result)
        except Exception as e:
            logger.error(f"TAB balance error: {e}")
            return 0.0

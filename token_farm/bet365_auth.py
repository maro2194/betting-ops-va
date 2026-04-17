"""
bet365 browser-based auth and bet placement via Camoufox.

bet365 has no REST API for betting — everything is browser-automated.
The token farm maintains persistent Camoufox browser sessions.
"""
import asyncio
import json
import logging
import os
import re
import uuid
from urllib.parse import urlparse, unquote

logger = logging.getLogger("token-farm.bet365")

# Persistent browser sessions: session_id -> browser/page objects
_browser_sessions: dict[str, dict] = {}


def _build_cfox_kwargs(proxy_url: str | None) -> dict:
    """Build Camoufox launch kwargs with optional proxy."""
    kwargs = {"headless": True}
    if proxy_url:
        p = urlparse(proxy_url)
        kwargs["proxy"] = {
            "server": f"{p.scheme}://{p.hostname}:{p.port}",
            **({"username": unquote(p.username)} if p.username else {}),
            **({"password": unquote(p.password)} if p.password else {}),
        }
        kwargs["geoip"] = True
    return kwargs


async def bet365_browser_login(username: str, password: str, proxy_url: str | None = None) -> dict:
    """Login to bet365 via Camoufox browser.

    Maintains a persistent browser session for subsequent bet placement.
    Returns: {success, session_id, balance, ...}
    """
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        return {"success": False, "error": "camoufox not installed"}

    session_id = str(uuid.uuid4())[:8]
    cfox_kwargs = _build_cfox_kwargs(proxy_url)

    try:
        browser = await AsyncCamoufox(**cfox_kwargs).__aenter__()
        page = await browser.new_page()

        await page.goto("https://www.bet365.com.au/", timeout=60000)
        await page.wait_for_timeout(8000)

        # Accept cookies if consent overlay is present — try multiple selectors
        for cookie_sel in [
            page.get_by_text("Accept All", exact=True),
            page.locator("button[class*='ccm']"),
            page.locator("button[class*='Accept']"),
            page.locator(".rcc-7c"),
        ]:
            try:
                if await cookie_sel.count() > 0:
                    await cookie_sel.first.click(timeout=5000)
                    logger.info("bet365: accepted cookies")
                    await page.wait_for_timeout(3000)
                    break
            except Exception:
                continue

        # Click "Log In" header button — retry up to 3 times with waits
        clicked = False
        for attempt in range(3):
            login_btns = page.get_by_text("Log In", exact=True)
            for i in range(await login_btns.count()):
                try:
                    if await login_btns.nth(i).is_visible():
                        await login_btns.nth(i).click()
                        clicked = True
                        logger.info("bet365: clicked Log In header")
                        break
                except Exception:
                    continue
            if clicked:
                break
            logger.info(f"bet365: Log In not visible yet, retry {attempt + 1}/3")
            await page.wait_for_timeout(3000)

        if not clicked:
            logger.warning("bet365: no visible Log In button found after retries")

        await page.wait_for_timeout(3000)

        # Fill username — try placeholder-based (stable across redesigns), then class-based
        username_filled = False
        for sel in [
            "input[placeholder='Username or email address']",
            "input[placeholder*='Username']",
            "input[placeholder*='username']",
            ".lms-StandardLogin_Username input",
            "input.slm2-c7",
        ]:
            el = page.locator(sel)
            if await el.count() > 0 and await el.first.is_visible():
                await el.first.fill(username)
                username_filled = True
                logger.info(f"bet365: filled username via {sel}")
                break

        if not username_filled:
            await browser.__aexit__(None, None, None)
            return {"success": False, "error": "bet365 login form not found — username input missing"}

        await page.wait_for_timeout(500)

        # Fill password
        pass_input = page.locator("input[type='password']")
        if await pass_input.count() > 0:
            await pass_input.first.fill(password)
            logger.info("bet365: filled password")
        else:
            await browser.__aexit__(None, None, None)
            return {"success": False, "error": "bet365 password input not found"}

        await page.wait_for_timeout(500)

        # Click submit — find the "Log In" button inside the form (last visible one)
        login_btns = page.get_by_text("Log In", exact=True)
        count = await login_btns.count()
        submitted = False
        for i in range(count - 1, -1, -1):
            try:
                if await login_btns.nth(i).is_visible():
                    await login_btns.nth(i).click()
                    submitted = True
                    logger.info(f"bet365: clicked submit (button {i})")
                    break
            except Exception:
                continue

        if not submitted:
            # Fallback: try class-based selectors
            for sel in [".lms-LoginButton", "button[class*='LoginButton']"]:
                el = page.locator(sel)
                if await el.count() > 0:
                    await el.first.click()
                    submitted = True
                    break

        # Wait for balance to appear (takes 12-20s after login click)
        # Login button disappears quickly but balance loads much later
        balance = 0.0
        logged_in = False
        for attempt in range(12):
            await page.wait_for_timeout(3000)

            # Check Balance elements for a dollar amount
            try:
                bal_els = await page.query_selector_all("div[class*='Balance_Value'], div[class*='Balance']")
                for el in bal_els:
                    text = await el.text_content()
                    if text:
                        m = re.search(r'\d[\d,.]+', text)
                        if m:
                            val = float(m.group().replace(",", ""))
                            if val > 0:
                                balance = val
                                logged_in = True
                                logger.info(f"bet365: balance found on attempt {attempt + 1}: ${balance}")
                                break
                if logged_in:
                    break
            except Exception:
                pass

        # If no balance found but login button is gone, still count as logged in
        if not logged_in:
            try:
                login_btns = page.get_by_text("Log In", exact=True)
                login_visible = False
                for i in range(await login_btns.count()):
                    if await login_btns.nth(i).is_visible():
                        login_visible = True
                        break
                if not login_visible:
                    logged_in = True
                    logger.warning("bet365: logged in but balance not found (may be $0)")
            except Exception:
                pass

        if not logged_in:
            await browser.__aexit__(None, None, None)
            return {"success": False, "error": "bet365 login failed — timed out"}

        # Store persistent session
        _browser_sessions[session_id] = {
            "browser": browser,
            "page": page,
            "username": username,
        }

        logger.info(f"bet365: login success for {username}, balance=${balance}, session={session_id}")
        return {
            "success": True,
            "session_id": session_id,
            "balance": balance,
            "username": username,
        }

    except Exception as e:
        logger.error(f"bet365 login failed for {username}: {e}")
        return {"success": False, "error": f"Camoufox login failed: {e}"}


async def bet365_place_browser_bet(session_id: str, bet_payload: dict) -> dict:
    """Place a bet on bet365 using a persistent browser session.

    bet_payload keys:
        track        str   e.g. "Eagle Farm"
        race_number  int   e.g. 3
        runner_name  str   e.g. "Gram"
        runner_number int  e.g. 5
        stake        float e.g. 1.0
        stake_type   str   "win" or "place"
    """
    session = _browser_sessions.get(session_id)
    if not session:
        return {"success": False, "error": f"No active session: {session_id}", "session_id": session_id}

    page = session["page"]
    track: str = bet_payload.get("track", "")
    race_number: int = int(bet_payload.get("race_number", 0))
    runner_name: str = bet_payload.get("runner_name", "")
    runner_number: int = int(bet_payload.get("runner_number", 0))
    stake: float = float(bet_payload.get("stake", 0.0))
    stake_type: str = bet_payload.get("stake_type", "win").lower()

    def _norm(s: str) -> str:
        """Normalise string for fuzzy matching."""
        return re.sub(r"\s+", " ", s or "").strip().lower()

    track_norm = _norm(track)

    logger.info(
        f"bet365 place_bet: session={session_id} track={track} race={race_number} "
        f"runner={runner_number}/{runner_name} stake=${stake} type={stake_type}"
    )

    try:
        # ------------------------------------------------------------------
        # Step 1: Navigate to racing section
        # ------------------------------------------------------------------
        logger.info("bet365 place_bet: navigating to racing section")
        await page.goto("https://www.bet365.com.au/#/AC/B19/", timeout=60000)
        await page.wait_for_timeout(4000)

        # ------------------------------------------------------------------
        # Step 2: Find and click the track/venue
        # ------------------------------------------------------------------
        logger.info(f"bet365 place_bet: searching for venue '{track}'")
        venue_clicked = False

        # Strategy A: match by link/button text
        for attempt in range(3):
            all_links = await page.query_selector_all("a, button, span[class*='rcl'], div[class*='rcl']")
            for el in all_links:
                try:
                    text = await el.text_content()
                    if text and _norm(text) == track_norm:
                        if await el.is_visible():
                            await el.click()
                            venue_clicked = True
                            logger.info(f"bet365 place_bet: clicked venue '{track}' (attempt {attempt + 1})")
                            break
                except Exception:
                    continue
            if venue_clicked:
                break
            await page.wait_for_timeout(2000)

        # Strategy B: partial match if exact fails
        if not venue_clicked:
            all_links = await page.query_selector_all("a, button, span, div")
            for el in all_links:
                try:
                    text = await el.text_content()
                    if text and track_norm in _norm(text) and len(_norm(text)) < len(track_norm) + 20:
                        if await el.is_visible():
                            await el.click()
                            venue_clicked = True
                            logger.info(f"bet365 place_bet: clicked venue via partial match: '{text.strip()}'")
                            break
                except Exception:
                    continue

        if not venue_clicked:
            return {
                "success": False,
                "error": f"Could not find venue '{track}' on bet365 racing page",
                "session_id": session_id,
            }

        await page.wait_for_timeout(3000)

        # ------------------------------------------------------------------
        # Step 3: Find and click the race number tab
        # ------------------------------------------------------------------
        logger.info(f"bet365 place_bet: looking for race number {race_number}")
        race_clicked = False

        # Race tabs are typically labelled "R1", "R2", ... or just "1", "2", ...
        race_patterns = [str(race_number), f"R{race_number}", f"Race {race_number}"]
        for pattern in race_patterns:
            tabs = page.get_by_text(pattern, exact=True)
            count = await tabs.count()
            for i in range(count):
                try:
                    tab = tabs.nth(i)
                    if await tab.is_visible():
                        await tab.click()
                        race_clicked = True
                        logger.info(f"bet365 place_bet: clicked race tab '{pattern}'")
                        break
                except Exception:
                    continue
            if race_clicked:
                break

        # Fallback: class-based tab selectors
        if not race_clicked:
            for sel in [
                f"[class*='racetab'] >> text={race_number}",
                f"[class*='RaceTab'] >> text={race_number}",
                f"[class*='tab'] >> text=R{race_number}",
            ]:
                try:
                    el = page.locator(sel)
                    if await el.count() > 0 and await el.first.is_visible():
                        await el.first.click()
                        race_clicked = True
                        logger.info(f"bet365 place_bet: clicked race via selector '{sel}'")
                        break
                except Exception:
                    continue

        if not race_clicked:
            return {
                "success": False,
                "error": f"Could not find race {race_number} for venue '{track}'",
                "session_id": session_id,
            }

        await page.wait_for_timeout(3000)

        # Check for suspended / closed race
        page_text = await page.evaluate("document.body.innerText")
        page_text_norm = _norm(page_text)
        for bad_phrase in ["race closed", "suspended", "no longer available", "event not found"]:
            if bad_phrase in page_text_norm:
                return {
                    "success": False,
                    "error": f"Race unavailable: '{bad_phrase}' detected on page",
                    "session_id": session_id,
                }

        # ------------------------------------------------------------------
        # Step 4: Find the runner row and click the appropriate odds button
        # ------------------------------------------------------------------
        logger.info(f"bet365 place_bet: looking for runner {runner_number}/{runner_name}")
        runner_norm = _norm(runner_name)
        odds_clicked = False
        odds_value = 0.0

        # Locate all runner rows; try multiple container strategies
        # bet365 horse racing: each runner row contains number, name, and price buttons
        runner_selectors = [
            "[class*='rcl-ParticipantFixedOddsOnly']",
            "[class*='rcl-Participant']",
            "[class*='Participant']",
            "[class*='runner']",
            "[class*='Runner']",
            "tr",  # table-based layouts
        ]

        for row_sel in runner_selectors:
            rows = await page.query_selector_all(row_sel)
            if not rows:
                continue

            for row in rows:
                try:
                    row_text = await row.text_content() or ""
                    row_text_norm = _norm(row_text)

                    # Match by runner number and/or name
                    num_match = str(runner_number) in row_text.split()[:3] or row_text.lstrip().startswith(str(runner_number))
                    name_match = runner_norm in row_text_norm

                    if not (num_match or name_match):
                        continue

                    logger.info(f"bet365 place_bet: found runner row: '{row_text.strip()[:80]}'")

                    # Within this row, find win/place price buttons
                    # bet365 typically has 2 price buttons: Win then Place
                    price_btns = await row.query_selector_all(
                        "[class*='price'], [class*='Price'], [class*='odds'], [class*='Odds'], "
                        "[class*='btn'], button, [role='button']"
                    )

                    if stake_type == "win":
                        target_idx = 0  # first price button = win
                    else:
                        target_idx = 1  # second price button = place

                    # Collect visible price buttons
                    visible_btns = []
                    for btn in price_btns:
                        try:
                            if await btn.is_visible():
                                visible_btns.append(btn)
                        except Exception:
                            continue

                    if len(visible_btns) > target_idx:
                        btn = visible_btns[target_idx]
                        btn_text = (await btn.text_content() or "").strip()
                        # Attempt to parse displayed odds
                        m = re.search(r'\d+\.?\d*', btn_text)
                        if m:
                            odds_value = float(m.group())
                        await btn.click()
                        odds_clicked = True
                        logger.info(
                            f"bet365 place_bet: clicked {stake_type} odds button (idx={target_idx}), "
                            f"display='{btn_text}', parsed_odds={odds_value}"
                        )
                        break
                    elif visible_btns:
                        # Take whatever is available
                        btn = visible_btns[0]
                        btn_text = (await btn.text_content() or "").strip()
                        m = re.search(r'\d+\.?\d*', btn_text)
                        if m:
                            odds_value = float(m.group())
                        await btn.click()
                        odds_clicked = True
                        logger.info(
                            f"bet365 place_bet: clicked fallback odds button, display='{btn_text}'"
                        )
                        break

                except Exception as exc:
                    logger.debug(f"bet365 place_bet: row parse error: {exc}")
                    continue

            if odds_clicked:
                break

        if not odds_clicked:
            return {
                "success": False,
                "error": (
                    f"Could not find runner {runner_number}/{runner_name} or its odds button "
                    f"in race {race_number} at {track}"
                ),
                "session_id": session_id,
            }

        await page.wait_for_timeout(2500)

        # ------------------------------------------------------------------
        # Step 5: Enter the stake in the bet slip
        # ------------------------------------------------------------------
        logger.info(f"bet365 place_bet: entering stake ${stake}")
        stake_entered = False

        stake_input_selectors = [
            "input[placeholder*='Stake']",
            "input[placeholder*='stake']",
            "input[placeholder*='Amount']",
            "input[class*='stake']",
            "input[class*='Stake']",
            "input[class*='betslip']",
            "input[class*='BetSlip']",
            "[class*='BetSlip'] input[type='text']",
            "[class*='betslip'] input[type='text']",
            "[class*='slip'] input",
        ]

        for sel in stake_input_selectors:
            try:
                el = page.locator(sel)
                if await el.count() > 0 and await el.first.is_visible():
                    await el.first.triple_click()  # select all existing content
                    await el.first.fill(str(stake))
                    stake_entered = True
                    logger.info(f"bet365 place_bet: stake entered via '{sel}'")
                    break
            except Exception:
                continue

        # Fallback: find any visible text/number input inside a bet slip panel
        if not stake_entered:
            inputs = await page.query_selector_all("input[type='text'], input[type='number'], input:not([type])")
            for inp in inputs:
                try:
                    if await inp.is_visible():
                        parent_html = await page.evaluate(
                            "(el) => el.closest('[class]') ? el.closest('[class]').className : ''", inp
                        )
                        if any(kw in (parent_html or "").lower() for kw in ["slip", "bet", "stake", "basket"]):
                            await inp.triple_click()
                            await inp.fill(str(stake))
                            stake_entered = True
                            logger.info("bet365 place_bet: stake entered via fallback input scan")
                            break
                except Exception:
                    continue

        if not stake_entered:
            return {
                "success": False,
                "error": "Could not find stake input in bet slip",
                "session_id": session_id,
            }

        await page.wait_for_timeout(2000)

        # Check for bet slip errors before placing (e.g. odds changed, max stake)
        slip_text = ""
        for slip_sel in ["[class*='BetSlip']", "[class*='betslip']", "[class*='slip']"]:
            try:
                slip_els = await page.query_selector_all(slip_sel)
                for el in slip_els:
                    t = await el.text_content()
                    if t:
                        slip_text += " " + t
            except Exception:
                continue

        slip_text_norm = _norm(slip_text)
        for error_phrase in ["odds have changed", "price changed", "suspended", "maximum stake", "not available"]:
            if error_phrase in slip_text_norm:
                return {
                    "success": False,
                    "error": f"Bet slip error before placing: '{error_phrase}' detected",
                    "session_id": session_id,
                }

        # ------------------------------------------------------------------
        # Step 6: Click "Place Bet" / "Place Bets"
        # ------------------------------------------------------------------
        logger.info("bet365 place_bet: clicking Place Bet")
        place_clicked = False

        for btn_text_pattern in ["Place Bet", "Place Bets", "Place bet", "Place bets", "PLACE BET"]:
            try:
                btn = page.get_by_text(btn_text_pattern, exact=True)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    place_clicked = True
                    logger.info(f"bet365 place_bet: clicked '{btn_text_pattern}'")
                    break
            except Exception:
                continue

        # Fallback: class-based selectors
        if not place_clicked:
            for sel in [
                "[class*='PlaceBet']",
                "[class*='place-bet']",
                "[class*='placeBet']",
                "button[class*='confirm']",
                "button[class*='submit']",
            ]:
                try:
                    el = page.locator(sel)
                    if await el.count() > 0 and await el.first.is_visible():
                        await el.first.click()
                        place_clicked = True
                        logger.info(f"bet365 place_bet: clicked Place Bet via selector '{sel}'")
                        break
                except Exception:
                    continue

        if not place_clicked:
            return {
                "success": False,
                "error": "Could not find Place Bet button in bet slip",
                "session_id": session_id,
            }

        await page.wait_for_timeout(3000)

        # ------------------------------------------------------------------
        # Step 7: Check for confirmation / receipt
        # ------------------------------------------------------------------
        logger.info("bet365 place_bet: checking for confirmation")
        page_text_after = await page.evaluate("document.body.innerText")
        page_text_after_norm = _norm(page_text_after)

        # Check for post-placement errors
        for error_phrase in ["odds have changed", "price changed", "bet not placed", "error placing", "suspended"]:
            if error_phrase in page_text_after_norm:
                return {
                    "success": False,
                    "error": f"Bet placement failed: '{error_phrase}' detected after submit",
                    "session_id": session_id,
                }

        # Look for receipt / confirmation indicators
        receipt_number = ""
        for receipt_pattern in [
            r'receipt[:\s#]*([A-Z0-9\-]+)',
            r'reference[:\s#]*([A-Z0-9\-]+)',
            r'bet\s+id[:\s#]*([A-Z0-9\-]+)',
            r'confirmation[:\s#]*([A-Z0-9\-]+)',
            r'#([A-Z0-9]{6,})',
        ]:
            m = re.search(receipt_pattern, page_text_after, re.IGNORECASE)
            if m:
                receipt_number = m.group(1)
                logger.info(f"bet365 place_bet: receipt found: {receipt_number}")
                break

        confirmed = any(
            phrase in page_text_after_norm
            for phrase in ["bet placed", "bet confirmed", "receipt", "thank you", "your bet"]
        )

        if not confirmed and not receipt_number:
            # Ambiguous — the page may have refreshed; treat as possible success but warn
            logger.warning("bet365 place_bet: no clear confirmation found; bet may or may not have been placed")
            return {
                "success": True,
                "bet_id": receipt_number or "unknown",
                "receipt": receipt_number or "unconfirmed",
                "stake": stake,
                "odds": odds_value,
                "warning": "No explicit confirmation text found; check your bet history",
                "session_id": session_id,
            }

        logger.info(
            f"bet365 place_bet: SUCCESS — receipt={receipt_number} stake=${stake} odds={odds_value} "
            f"runner={runner_number}/{runner_name} {stake_type}"
        )
        return {
            "success": True,
            "bet_id": receipt_number or "confirmed",
            "receipt": receipt_number or "confirmed",
            "stake": stake,
            "odds": odds_value,
            "session_id": session_id,
        }

    except Exception as exc:
        logger.error(f"bet365 place_bet: unhandled exception: {exc}")
        return {
            "success": False,
            "error": f"Unhandled error during bet placement: {exc}",
            "session_id": session_id,
        }


async def bet365_place_megaboost(session_id: str, sport: str, match_team: str, stake: float = 20, boost_index: int = 0) -> dict:
    """Place a Mega Boost / Bet Boost on a match using persistent browser session.

    Args:
        session_id: From bet365_browser_login
        sport: "AFL", "NRL", etc.
        match_team: Team name to find the match
        stake: Bet amount
        boost_index: 0 = first boost (usually Mega Boost)
    """
    session = _browser_sessions.get(session_id)
    if not session:
        return {"success": False, "error": f"No active session: {session_id}"}

    page = session["page"]
    logger.info(f"bet365 megaboost: session={session_id} sport={sport} team={match_team} stake=${stake}")

    try:
        await page.goto("https://www.bet365.com.au/#/HO/", timeout=30000)
        await page.wait_for_timeout(4000)

        # Dismiss cookie consent if present (can reappear after navigation)
        try:
            dismissed = await page.evaluate("""() => {
                const buttons = document.querySelectorAll('button, div[role="button"], a');
                for (const btn of buttons) {
                    const text = (btn.textContent || '').trim();
                    if (text === 'Accept All' || text === 'Accept Cookies') {
                        btn.click();
                        return true;
                    }
                }
                const ccm = document.querySelector('[class*="ccm"] button, [class*="cookie"] button');
                if (ccm) { ccm.click(); return true; }
                return false;
            }""")
            if dismissed:
                logger.info("bet365 megaboost: dismissed cookie consent via JS")
                await page.wait_for_timeout(2000)
        except Exception as e:
            logger.debug(f"bet365 megaboost: cookie dismiss attempt: {e}")

        # If sport is HOME, stay on homepage and scan for boosts directly
        if sport.upper() == "HOME":
            logger.info("bet365 megaboost: scanning homepage for boosts")
            # Scroll down to make sure boost cards are loaded
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 400)")
                await page.wait_for_timeout(800)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(1000)
        else:
            # Navigate to sport → match → Popular tab
            sport_map = {"AFL": "Australian Rules", "NRL": "Rugby League", "NBA": "Basketball"}
            sport_name = sport_map.get(sport.upper(), sport)
            logger.info(f"bet365 megaboost: navigating to {sport.upper()} ({sport_name})")

            sport_clicked = False
            for name in [sport.upper(), sport_name]:
                try:
                    links = page.get_by_text(name, exact=True)
                    count = await links.count()
                    for i in range(count):
                        if await links.nth(i).is_visible():
                            await links.nth(i).click()
                            sport_clicked = True
                            logger.info(f"bet365 megaboost: clicked '{name}' in sidebar")
                            break
                    if sport_clicked:
                        break
                except Exception:
                    continue

            await page.wait_for_timeout(4000)

            # If we landed on a category page, click into the competition
            body_text = await page.evaluate("document.body.innerText")
            if "Competitions" in body_text and "Featured" in body_text:
                logger.info("bet365 megaboost: on category page, clicking into competition...")
                for comp_name in [sport.upper(), f"{sport.upper()} Premiership", "AFL", "NRL", "Premiership"]:
                    try:
                        comp = page.get_by_text(comp_name, exact=True)
                        if await comp.count() > 0:
                            for i in range(await comp.count()):
                                if await comp.nth(i).is_visible():
                                    await comp.nth(i).click()
                                    logger.info(f"bet365 megaboost: clicked competition '{comp_name}'")
                                    await page.wait_for_timeout(3000)
                                    break
                            break
                    except Exception:
                        continue

            # Find and click the match
            logger.info(f"bet365 megaboost: looking for match with '{match_team}'")
            match_team_lower = match_team.lower()
            match_clicked = False

            for scroll in range(5):
                if scroll > 0:
                    await page.evaluate("window.scrollBy(0, 500)")
                    await page.wait_for_timeout(1000)

                body = await page.evaluate("document.body.innerText")
                if match_team_lower in body.lower():
                    el = page.get_by_text(re.compile(re.escape(match_team), re.IGNORECASE)).first
                    try:
                        if await el.is_visible(timeout=3000):
                            await el.click()
                            match_clicked = True
                            logger.info(f"bet365 megaboost: clicked match '{match_team}'")
                            break
                    except Exception:
                        pass

            if not match_clicked:
                return {"success": False, "error": f"Match with '{match_team}' not found"}

            await page.wait_for_timeout(4000)

            # Make sure Popular tab is selected (boosts are there)
            try:
                popular = page.get_by_text("Popular", exact=True)
                if await popular.count() > 0 and await popular.first.is_visible():
                    await popular.first.click()
                    await page.wait_for_timeout(2000)
            except Exception:
                pass

        # Find Mega Boost / Bet Boost cards and click
        logger.info(f"bet365 megaboost: searching for boost cards...")

        boost_result = await page.evaluate(r"""
            (targetIndex) => {
                const boosts = [];
                const seen = new Set();
                const pattern = /\$\d+\s+stake\s+returns\s+\$/i;

                // Find elements whose innerText contains the returns pattern
                // Wider limits to catch both BET BOOST (smaller cards) and MEGA BOOST (larger featured cards)
                const allEls = document.querySelectorAll('*');

                for (const el of allEls) {
                    const text = el.innerText || '';
                    if (text.length < 30 || text.length > 800) continue;
                    if (!pattern.test(text)) continue;
                    if (!el.offsetParent) continue;

                    // Size check: wider range to catch MEGA BOOST cards (can be up to 900px)
                    const elRect = el.getBoundingClientRect();
                    if (elRect.width < 100 || elRect.width > 1000) continue;
                    if (elRect.height < 60 || elRect.height > 600) continue;

                    // Dedup by position
                    const cardId = Math.round(elRect.x) + ',' + Math.round(elRect.y);
                    if (seen.has(cardId)) continue;
                    seen.add(cardId);

                    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
                    const title = lines[0] || '';

                    // Find odds values: leaf elements with just "X.XX" text
                    const oddsEls = [];
                    const candidates = el.querySelectorAll('span, div, button, a');
                    for (const c of candidates) {
                        const ct = (c.textContent || '').trim();
                        if (/^\d+\.\d{2}$/.test(ct) && c.children.length === 0 && c.offsetParent) {
                            const r = c.getBoundingClientRect();
                            if (r.width > 10 && r.width < 150 && r.height > 8 && r.height < 60) {
                                oddsEls.push({ text: ct, val: parseFloat(ct), el: c });
                            }
                        }
                    }

                    if (oddsEls.length < 1) continue;

                    oddsEls.sort((a, b) => b.val - a.val);
                    const boosted = oddsEls[0];
                    const original = oddsEls.length >= 2 ? oddsEls[oddsEls.length - 1] : null;

                    const isMega = text.toUpperCase().includes('MEGA') ||
                                   el.innerHTML.toUpperCase().includes('MEGA');

                    // Find clickable target: walk up from boosted odds to button/anchor
                    let clickTarget = boosted.el;
                    let walkEl = boosted.el;
                    for (let i = 0; i < 8; i++) {
                        if (!walkEl) break;
                        const tag = walkEl.tagName?.toLowerCase();
                        const role = walkEl.getAttribute?.('role');
                        const cursor = window.getComputedStyle(walkEl).cursor;
                        if (tag === 'button' || tag === 'a' || role === 'button' || cursor === 'pointer') {
                            clickTarget = walkEl;
                            break;
                        }
                        walkEl = walkEl.parentElement;
                    }

                    const markerId = 'boost-target-' + boosts.length;
                    clickTarget.setAttribute('data-botops-boost', markerId);

                    const returnsMatch = text.match(/\$(\d+)\s+stake\s+returns\s+\$([\d,.]+)/i);

                    boosts.push({
                        type: isMega ? 'MEGA_BOOST' : 'BET_BOOST',
                        title: title.substring(0, 60),
                        original_odds: original ? original.text : '',
                        odds: boosted.text,
                        markerId: markerId,
                        returns: returnsMatch ? returnsMatch[0] : '',
                    });
                }

                // Sort: MEGA first
                boosts.sort((a, b) => {
                    if (a.type !== b.type) return a.type === 'MEGA_BOOST' ? -1 : 1;
                    return 0;
                });

                if (targetIndex < boosts.length)
                    return { found: true, boost: boosts[targetIndex], all: boosts.map(b => ({type: b.type, odds: b.odds, title: b.title, returns: b.returns})) };
                return { found: false, all: boosts.map(b => ({type: b.type, odds: b.odds, title: b.title})), count: boosts.length };
            }
        """, boost_index)

        if not boost_result.get("found"):
            all_boosts = boost_result.get("all", [])
            logger.warning(f"bet365 megaboost: boost #{boost_index} not found. Available: {all_boosts}")
            return {
                "success": False,
                "error": f"Boost #{boost_index} not found. {len(all_boosts)} boosts detected: {all_boosts}",
            }

        boost = boost_result["boost"]
        marker_id = boost.get("markerId", "")
        logger.info(f"bet365 megaboost: found {boost['type']} '{boost.get('title','')}' odds={boost['odds']} marker={marker_id}")

        # Step 5: Click the boost odds — use the tagged element, with multiple fallback strategies
        betslip_opened = False

        # Count stake inputs BEFORE clicking to detect new ones appearing
        pre_click_inputs = await page.locator("input[type='tel'], input[type='text'], input[type='number'], input:not([type])").count()

        # Strategy A: Click the tagged element via Playwright locator
        if marker_id:
            try:
                target = page.locator(f"[data-botops-boost='{marker_id}']")
                if await target.count() > 0 and await target.first.is_visible():
                    await target.first.click(timeout=5000)
                    logger.info("bet365 megaboost: clicked via data-botops-boost attribute")
                    await page.wait_for_timeout(3000)
            except Exception as e:
                logger.warning(f"bet365 megaboost: tagged element click failed: {e}")

        # Check if betslip opened — look for NEW input fields or Place Bet button
        post_click_inputs = await page.locator("input[type='tel'], input[type='text'], input[type='number'], input:not([type])").count()
        place_btn_visible = False
        for btn_text in ["Place Bet", "Place Bets"]:
            try:
                btn = page.get_by_text(btn_text, exact=True)
                if await btn.count() > 0 and await btn.first.is_visible():
                    place_btn_visible = True
                    break
            except Exception:
                continue

        if post_click_inputs > pre_click_inputs or place_btn_visible:
            betslip_opened = True
            logger.info(f"bet365 megaboost: betslip detected (inputs: {pre_click_inputs}->{post_click_inputs}, place_btn={place_btn_visible})")

        # Strategy B: JS click on the tagged element
        if not betslip_opened and marker_id:
            try:
                await page.evaluate(f"document.querySelector('[data-botops-boost=\"{marker_id}\"]')?.click()")
                logger.info("bet365 megaboost: JS click on tagged element")
                await page.wait_for_timeout(3000)
                post_inputs = await page.locator("input[type='tel'], input[type='text'], input[type='number'], input:not([type])").count()
                for btn_text in ["Place Bet", "Place Bets"]:
                    btn = page.get_by_text(btn_text, exact=True)
                    if await btn.count() > 0 and await btn.first.is_visible():
                        place_btn_visible = True
                        break
                if post_inputs > pre_click_inputs or place_btn_visible:
                    betslip_opened = True
                    logger.info("bet365 megaboost: betslip opened via JS click")
            except Exception as e:
                logger.warning(f"bet365 megaboost: JS click failed: {e}")

        # Strategy C: dispatch pointer events (mousedown/mouseup/click) to simulate real click
        if not betslip_opened and marker_id:
            try:
                await page.evaluate("""(markerId) => {
                    const el = document.querySelector('[data-botops-boost="' + markerId + '"]');
                    if (!el) return;
                    for (const evtName of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                        el.dispatchEvent(new PointerEvent(evtName, {bubbles: true, cancelable: true, view: window}));
                    }
                }""", marker_id)
                logger.info("bet365 megaboost: dispatched pointer events on tagged element")
                await page.wait_for_timeout(3000)
                post_inputs = await page.locator("input[type='tel'], input[type='text'], input[type='number'], input:not([type])").count()
                for btn_text in ["Place Bet", "Place Bets"]:
                    btn = page.get_by_text(btn_text, exact=True)
                    if await btn.count() > 0 and await btn.first.is_visible():
                        place_btn_visible = True
                        break
                if post_inputs > pre_click_inputs or place_btn_visible:
                    betslip_opened = True
                    logger.info("bet365 megaboost: betslip opened via pointer events")
            except Exception as e:
                logger.warning(f"bet365 megaboost: pointer event dispatch failed: {e}")

        # Strategy D: scroll element into view and use mouse coordinates as last resort
        if not betslip_opened and marker_id:
            try:
                coords = await page.evaluate("""(markerId) => {
                    const el = document.querySelector('[data-botops-boost="' + markerId + '"]');
                    if (!el) return null;
                    el.scrollIntoView({block: 'center'});
                    const r = el.getBoundingClientRect();
                    return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                }""", marker_id)
                if coords:
                    await page.wait_for_timeout(500)
                    await page.mouse.click(coords["x"], coords["y"])
                    logger.info(f"bet365 megaboost: mouse click at ({coords['x']},{coords['y']}) after scroll")
                    await page.wait_for_timeout(3000)
                    post_inputs = await page.locator("input[type='tel'], input[type='text'], input[type='number'], input:not([type])").count()
                    for btn_text in ["Place Bet", "Place Bets"]:
                        btn = page.get_by_text(btn_text, exact=True)
                        if await btn.count() > 0 and await btn.first.is_visible():
                            place_btn_visible = True
                            break
                    if post_inputs > pre_click_inputs or place_btn_visible:
                        betslip_opened = True
                        logger.info("bet365 megaboost: betslip opened via scroll + mouse click")
            except Exception as e:
                logger.warning(f"bet365 megaboost: scroll+mouse click failed: {e}")

        if not betslip_opened:
            # Take a screenshot for debugging
            try:
                await page.screenshot(path="/tmp/b365_megaboost_fail.png")
            except Exception:
                pass
            body_text = await page.evaluate("document.body.innerText")
            return {
                "success": False,
                "error": "Betslip did not open after clicking boost (tried 4 strategies)",
                "boost": {k: v for k, v in boost.items() if k != "markerId"},
                "page_snippet": body_text[:1500],
            }

        # Step 6: Enter stake
        logger.info(f"bet365 megaboost: entering stake ${stake}")

        # Diagnostic: log all inputs on the page
        input_diag = await page.evaluate("""() => {
            const results = [];
            const inputs = document.querySelectorAll('input, textarea, [contenteditable]');
            for (const inp of inputs) {
                const rect = inp.getBoundingClientRect();
                const style = window.getComputedStyle(inp);
                results.push({
                    tag: inp.tagName,
                    type: inp.type || '',
                    placeholder: inp.placeholder || '',
                    cls: (inp.className || '').substring(0, 80),
                    visible: inp.offsetParent !== null,
                    display: style.display,
                    w: Math.round(rect.width),
                    h: Math.round(rect.height),
                    value: inp.value || '',
                });
            }
            return results;
        }""")
        logger.info(f"bet365 megaboost: found {len(input_diag)} inputs: {input_diag}")

        stake_entered = False
        stake_str = str(int(stake))

        # bet365 uses a contenteditable div + hidden input for stake entry
        # The contenteditable div must be clicked and typed into like a real user
        # Just setting the hidden input value via JS doesn't trigger the framework

        # Strategy A: click the visible stake div, type the value
        stake_div_sel = "[class*='StakeBox_StakeValue']"
        try:
            stake_div = page.locator(stake_div_sel)
            count = await stake_div.count()
            logger.info(f"bet365 megaboost: StakeBox_StakeValue count={count}")
            if count > 0:
                # Find the first visible one
                for idx in range(count):
                    if await stake_div.nth(idx).is_visible():
                        await stake_div.nth(idx).click()
                        logger.info(f"bet365 megaboost: clicked stake div (idx={idx})")
                        await page.wait_for_timeout(500)
                        # Select all and delete any existing content
                        await page.keyboard.press("Control+a")
                        await page.keyboard.press("Backspace")
                        await page.wait_for_timeout(200)
                        # Type stake digit by digit
                        await page.keyboard.type(stake_str, delay=80)
                        await page.wait_for_timeout(500)
                        # Tab out to trigger blur/validation
                        await page.keyboard.press("Tab")
                        await page.wait_for_timeout(1000)
                        stake_entered = True
                        logger.info(f"bet365 megaboost: stake '{stake_str}' typed into contenteditable div")
                        break
        except Exception as e:
            logger.warning(f"bet365 megaboost: contenteditable type failed: {e}")

        # Strategy B: click the hidden input's label area and type
        if not stake_entered:
            try:
                # Try clicking the stake box container and typing
                for sel in ["[class*='StakeBox']", "[class*='stakeBox']"]:
                    el = page.locator(sel)
                    if await el.count() > 0 and await el.first.is_visible():
                        await el.first.click()
                        await page.wait_for_timeout(500)
                        await page.keyboard.type(stake_str, delay=80)
                        await page.keyboard.press("Tab")
                        await page.wait_for_timeout(1000)
                        stake_entered = True
                        logger.info(f"bet365 megaboost: stake typed via StakeBox container click")
                        break
            except Exception as e:
                logger.warning(f"bet365 megaboost: StakeBox click+type failed: {e}")

        # Strategy C: standard input selectors (fallback)
        if not stake_entered:
            for sel in [
                "input[placeholder*='Stake']", "input[type='tel']",
                "input[class*='stake']", "[class*='slip'] input",
            ]:
                try:
                    el = page.locator(sel)
                    if await el.count() > 0 and await el.first.is_visible():
                        await el.first.triple_click()
                        await el.first.fill(stake_str)
                        stake_entered = True
                        logger.info(f"bet365 megaboost: stake entered via '{sel}'")
                        break
                except Exception:
                    continue

        if not stake_entered:
            return {"success": False, "error": "Could not enter stake"}

        await page.wait_for_timeout(2000)

        # Step 7: Click Place Bet
        logger.info("bet365 megaboost: clicking Place Bet")
        place_clicked = False
        for btn_text in ["Place Bet", "Place Bets", "Place bet", "PLACE BET"]:
            try:
                btn = page.get_by_text(btn_text, exact=True)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    place_clicked = True
                    logger.info(f"bet365 megaboost: clicked '{btn_text}'")
                    break
            except Exception:
                continue

        if not place_clicked:
            return {"success": False, "error": "Place Bet button not found"}

        await page.wait_for_timeout(4000)

        # Step 8: Check confirmation
        body = await page.evaluate("document.body.innerText")
        body_lower = body.lower()

        # Check for errors
        for err in ["odds have changed", "price changed", "suspended", "maximum stake", "not available"]:
            if err in body_lower:
                # Try to accept odds change
                if "accept" in body_lower:
                    try:
                        accept = page.get_by_text("Accept", exact=False)
                        if await accept.count() > 0:
                            await accept.first.click()
                            await page.wait_for_timeout(3000)
                            body = await page.evaluate("document.body.innerText")
                            body_lower = body.lower()
                            break
                    except Exception:
                        pass
                return {"success": False, "error": f"Bet placement error: '{err}'"}

        confirmed = any(p in body_lower for p in ["bet placed", "bet confirmed", "receipt", "thank you", "your bet"])

        receipt = ""
        m = re.search(r'(?:receipt|reference|bet\s+id)[:\s#]*([A-Z0-9\-]+)', body, re.IGNORECASE)
        if m:
            receipt = m.group(1)

        logger.info(f"bet365 megaboost: result confirmed={confirmed} receipt={receipt}")

        return {
            "success": True if confirmed or receipt else False,
            "boost_type": boost["type"],
            "odds": boost["odds"],
            "stake": stake,
            "receipt": receipt or ("confirmed" if confirmed else ""),
            "error": "" if (confirmed or receipt) else "No clear confirmation",
        }

    except Exception as e:
        logger.error(f"bet365 megaboost error: {e}")
        return {"success": False, "error": str(e)}


async def bet365_place_custom_sgm(session_id: str, selections: list[dict], stake: float = 25, apply_promo: bool = True) -> dict:
    """Place a custom SGM by clicking individual market selections.

    selections: [{"player": "Patrick Voss", "odds": "13.00"}, {"player": "Josh Treacy", "odds": "7.50"}]
    The function finds each player's odds on the page and clicks them to build an SGM.
    """
    session = _browser_sessions.get(session_id)
    if not session:
        return {"success": False, "error": f"No active session: {session_id}"}

    page = session["page"]
    logger.info(f"bet365 custom_sgm: session={session_id} selections={selections} stake=${stake}")

    try:
        # Step 0: Navigate to SGM → Goalscorer → Goalscorer sub-tab
        # This shows First/Last columns with interactive odds
        try:
            # Click SGM tab
            sgm_tab = page.get_by_text("SGM", exact=True)
            count = await sgm_tab.count()
            for i in range(count):
                el = sgm_tab.nth(i)
                if await el.is_visible():
                    bbox = await el.bounding_box()
                    if bbox and bbox["y"] < 300:
                        await el.click()
                        logger.info("bet365 custom_sgm: clicked SGM tab")
                        await page.wait_for_timeout(3000)
                        break

            # Click "Goalscorer" sub-tab (the LAST one — first is section header, last is sub-tab)
            # Sub-tabs: "Multi Scorer" | "Goalscorer" — we want the Goalscorer sub-tab
            # which shows First/Last columns
            gs_els = page.get_by_text("Goalscorer", exact=True)
            count = await gs_els.count()
            logger.info(f"bet365 custom_sgm: found {count} 'Goalscorer' elements")
            # Click the last visible one (sub-tab, not section header)
            for i in range(count - 1, -1, -1):
                el = gs_els.nth(i)
                if await el.is_visible():
                    await el.click()
                    logger.info(f"bet365 custom_sgm: clicked Goalscorer element (idx={i} of {count})")
                    await page.wait_for_timeout(3000)
                    break

        except Exception as e:
            logger.warning(f"bet365 custom_sgm: SGM/Goalscorer navigation failed: {e}")

        # Step 1: Click each selection's odds on the page
        for sel_idx, sel in enumerate(selections):
            player = sel["player"]
            target_odds = sel.get("odds", "")
            logger.info(f"bet365 custom_sgm: looking for {player} @ {target_odds}")

            # Index-based approach: find the player's position in the list,
            # then click the corresponding odds in the First column
            marker = f"sgm-sel-{sel_idx}"
            found = await page.evaluate(r"""(args) => {
                const player = args.player.toLowerCase();
                const marker = args.marker;

                // Step 1: Find all player name elements in the goalscorer table
                // Player names are leaf text elements matching known player patterns
                const allEls = document.querySelectorAll('*');
                const playerEls = [];
                const seenNames = new Set();

                for (const el of allEls) {
                    const text = (el.textContent || '').trim();
                    // Player names: 2+ words, first letter caps, no numbers only
                    if (text.length > 4 && text.length < 40 && /^[A-Z][a-z]/.test(text) && text.includes(' ')) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && r.height < 30 && el.children.length === 0) {
                            const name = text.toLowerCase();
                            if (!seenNames.has(name)) {
                                seenNames.add(name);
                                playerEls.push({el, name, y: r.y + r.height/2});
                            }
                        }
                    }
                }

                // Sort by vertical position (top to bottom)
                playerEls.sort((a, b) => a.y - b.y);

                // Find our target player's index
                let playerIndex = -1;
                for (let i = 0; i < playerEls.length; i++) {
                    if (playerEls[i].name === player || playerEls[i].name.includes(player)) {
                        playerIndex = i;
                        break;
                    }
                }
                if (playerIndex === -1) {
                    return {found: false, error: 'Player not found: ' + args.player,
                            players: playerEls.map(p => p.name).slice(0, 15)};
                }

                // Step 2: Find the "First" column header to get its x-position
                let firstColX = null;
                for (const el of allEls) {
                    const text = (el.textContent || '').trim();
                    if (text === 'First' && el.children.length === 0) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            firstColX = r.x + r.width / 2;
                            break;
                        }
                    }
                }

                // Step 3: Find all odds elements in the First column area
                const firstColOdds = [];
                for (const el of allEls) {
                    const text = (el.textContent || '').trim();
                    if (!/^\d+\.\d{2,3}$/.test(text)) continue;
                    if (el.children.length > 0) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5 || r.width > 80 || r.height > 30) continue;
                    // Must be near the First column x-position (within 50px)
                    if (firstColX !== null && Math.abs(r.x + r.width/2 - firstColX) < 50) {
                        firstColOdds.push({el, text, y: r.y + r.height/2});
                    }
                }

                // Sort by vertical position
                firstColOdds.sort((a, b) => a.y - b.y);

                // Click the odds at the same index as our player
                if (playerIndex >= firstColOdds.length) {
                    return {found: false, error: 'Not enough odds in First column for index ' + playerIndex,
                            playerCount: playerEls.length, oddsCount: firstColOdds.length};
                }

                const target = firstColOdds[playerIndex];
                target.el.setAttribute('data-botops-sgm', marker);
                target.el.scrollIntoView({block: 'center'});
                const r2 = target.el.getBoundingClientRect();
                return {found: true, odds: target.text, player: args.player, index: playerIndex,
                        w: Math.round(r2.width), h: Math.round(r2.height)};
            }""", {"player": player, "odds": target_odds, "marker": marker})

            if not found.get("found"):
                return {"success": False, "error": found.get("error", f"Could not find {player}"), "step": "selection"}

            # Click via mouse coordinates (most reliable for bet365's React handlers)
            await page.wait_for_timeout(500)
            coords = await page.evaluate(r"""(m) => {
                const el = document.querySelector('[data-botops-sgm="' + m + '"]');
                if (!el) return null;
                el.scrollIntoView({block: 'center'});
                const r = el.getBoundingClientRect();
                return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), w: r.width, h: r.height};
            }""", marker)

            if not coords:
                return {"success": False, "error": f"Tagged element not found for {player}", "step": "selection"}

            await page.wait_for_timeout(300)
            await page.mouse.click(coords["x"], coords["y"])
            logger.info(f"bet365 custom_sgm: mouse-clicked {player} @ {found.get('odds')} at ({coords['x']},{coords['y']}) size={coords['w']}x{coords['h']}")
            await page.wait_for_timeout(2000)

        # Step 2: Wait for betslip to update with all selections
        await page.wait_for_timeout(2000)

        # Step 3: Look for and apply promo/saver ("USE STAKE BACK BET" / bonus bet back)
        if apply_promo:
            logger.info("bet365 custom_sgm: looking for promo/saver option")
            promo_applied = False

            # Strategy A: Playwright text-based click (most reliable for bet365)
            for promo_text in ["USE STAKE BACK", "Stake Back", "STAKE BACK BET", "Bonus Bet Back"]:
                try:
                    promo_el = page.get_by_text(promo_text, exact=False)
                    count = await promo_el.count()
                    for i in range(count):
                        if await promo_el.nth(i).is_visible():
                            await promo_el.nth(i).click()
                            promo_applied = True
                            logger.info(f"bet365 custom_sgm: promo applied via Playwright: '{promo_text}' (idx={i})")
                            await page.wait_for_timeout(1500)
                            break
                    if promo_applied:
                        break
                except Exception as e:
                    logger.debug(f"bet365 custom_sgm: promo text '{promo_text}' failed: {e}")

            # Strategy B: Find via JS and click with mouse coordinates
            if not promo_applied:
                promo_info = await page.evaluate(r"""() => {
                    const allEls = document.querySelectorAll('*');
                    for (const el of allEls) {
                        const text = (el.textContent || '').trim();
                        if (/(USE.*STAKE.*BACK|STAKE.*BACK.*BET|Bonus.*Bet.*Back)/i.test(text)) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 10 && r.height > 10 && r.width < 400) {
                                el.scrollIntoView({block: 'center'});
                                const r2 = el.getBoundingClientRect();
                                return {x: Math.round(r2.x + r2.width/2), y: Math.round(r2.y + r2.height/2), text: text.substring(0, 80)};
                            }
                        }
                    }
                    return null;
                }""")
                if promo_info:
                    await page.wait_for_timeout(300)
                    await page.mouse.click(promo_info["x"], promo_info["y"])
                    promo_applied = True
                    logger.info(f"bet365 custom_sgm: promo applied via mouse: '{promo_info['text']}'")
                    await page.wait_for_timeout(1500)

            if not promo_applied:
                body = await page.evaluate("document.body.innerText")
                logger.warning(f"bet365 custom_sgm: NO promo found. Betslip text: {body[-600:]}")
                return {"success": False, "error": "STAKE BACK BET promo not found — bet NOT placed", "step": "promo"}

        # Step 4: Enter stake (same contenteditable approach as megaboost)
        logger.info(f"bet365 custom_sgm: entering stake ${stake}")
        stake_str = str(int(stake))
        stake_entered = False

        stake_div = page.locator("[class*='StakeBox_StakeValue']")
        count = await stake_div.count()
        if count > 0:
            for idx in range(count):
                if await stake_div.nth(idx).is_visible():
                    await stake_div.nth(idx).click()
                    await page.wait_for_timeout(500)
                    await page.keyboard.press("Control+a")
                    await page.keyboard.press("Backspace")
                    await page.wait_for_timeout(200)
                    await page.keyboard.type(stake_str, delay=80)
                    await page.keyboard.press("Tab")
                    await page.wait_for_timeout(1000)
                    stake_entered = True
                    logger.info(f"bet365 custom_sgm: stake entered")
                    break

        if not stake_entered:
            return {"success": False, "error": "Could not enter stake", "step": "stake"}

        # Step 5: Read the betslip state before placing (for user review)
        betslip_text = await page.evaluate(r"""() => {
            // Find betslip area and extract text
            const slipEls = document.querySelectorAll('[class*="betslip" i], [class*="BetSlip" i], [class*="slip" i]');
            let text = '';
            for (const el of slipEls) {
                if (el.offsetParent && el.getBoundingClientRect().width > 100) {
                    text += el.innerText + '\n';
                }
            }
            if (!text) text = document.body.innerText;
            return text.substring(0, 1500);
        }""")

        # Step 6: Click Place Bet
        logger.info("bet365 custom_sgm: clicking Place Bet")
        place_clicked = False
        for btn_text in ["Place Bet", "Place Bets", "Place bet", "PLACE BET"]:
            try:
                btn = page.get_by_text(btn_text, exact=True)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    place_clicked = True
                    logger.info(f"bet365 custom_sgm: clicked '{btn_text}'")
                    break
            except Exception:
                continue

        if not place_clicked:
            return {"success": False, "error": "Place Bet button not found", "step": "place", "betslip": betslip_text}

        await page.wait_for_timeout(4000)

        # Step 7: Check confirmation
        body = await page.evaluate("document.body.innerText")
        body_lower = body.lower()

        for err in ["odds have changed", "price changed", "suspended", "maximum stake", "not available"]:
            if err in body_lower:
                if "accept" in body_lower:
                    try:
                        accept = page.get_by_text("Accept", exact=False)
                        if await accept.count() > 0:
                            await accept.first.click()
                            await page.wait_for_timeout(3000)
                            body = await page.evaluate("document.body.innerText")
                            body_lower = body.lower()
                            break
                    except Exception:
                        pass
                return {"success": False, "error": f"Bet error: '{err}'", "step": "confirm"}

        confirmed = any(p in body_lower for p in ["bet placed", "bet confirmed", "receipt", "thank you", "your bet"])
        receipt = ""
        m = re.search(r'(?:receipt|reference|bet\s+id)[:\s#]*([A-Z0-9\-]+)', body, re.IGNORECASE)
        if m:
            receipt = m.group(1)

        return {
            "success": confirmed or bool(receipt),
            "receipt": receipt or ("confirmed" if confirmed else ""),
            "stake": stake,
            "selections": selections,
            "promo_applied": apply_promo,
            "error": "" if (confirmed or receipt) else "No clear confirmation",
        }

    except Exception as e:
        logger.error(f"bet365 custom_sgm error: {e}")
        return {"success": False, "error": str(e)}


async def bet365_close_session(session_id: str):
    """Close a persistent browser session."""
    session = _browser_sessions.pop(session_id, None)
    if session and session.get("browser"):
        try:
            await session["browser"].__aexit__(None, None, None)
            logger.info(f"bet365: closed session {session_id}")
        except Exception as e:
            logger.warning(f"bet365: error closing session {session_id}: {e}")


async def _megaboost_single_account(account: dict, sport: str, match_team: str, stake: float, boost_index: int) -> dict:
    """Login, place megaboost, close browser for a single account."""
    username = account["username"]
    password = account["password"]
    proxy_url = account.get("proxy_url")

    result = {"username": username, "success": False, "error": ""}

    try:
        # Login
        login = await bet365_browser_login(username, password, proxy_url=proxy_url)
        if not login.get("success"):
            result["error"] = f"Login failed: {login.get('error', 'unknown')}"
            return result

        session_id = login["session_id"]
        result["balance_before"] = login.get("balance", 0)

        # Place megaboost
        placement = await bet365_place_megaboost(session_id, sport, match_team, stake, boost_index)
        result.update({
            "success": placement.get("success", False),
            "boost_type": placement.get("boost_type", ""),
            "odds": placement.get("odds", ""),
            "receipt": placement.get("receipt", ""),
            "error": placement.get("error", ""),
        })

        # Close browser to free resources
        await bet365_close_session(session_id)

    except Exception as e:
        result["error"] = f"Unhandled error: {e}"
        logger.error(f"bet365 megaboost_all: {username} failed: {e}")

    return result


async def bet365_megaboost_all(sport: str, match_team: str, stake: float = 20, boost_index: int = 0,
                                account_filter: list[str] | None = None) -> dict:
    """Place megaboost across all configured accounts in parallel.

    Args:
        sport: "NRL", "AFL", "HOME", etc.
        match_team: Team name to find the match (ignored if sport=HOME)
        stake: Bet amount per account
        boost_index: Which boost to place (0 = first/Mega Boost)
        account_filter: Optional list of usernames to include (None = all)
    """
    # Load accounts
    config_path = os.path.join(os.path.dirname(__file__), "bet365_accounts.json")
    try:
        with open(config_path) as f:
            accounts = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"Could not load accounts: {e}"}

    # Filter if requested
    if account_filter:
        filter_lower = [a.lower() for a in account_filter]
        accounts = [a for a in accounts if a["username"].lower() in filter_lower]

    if not accounts:
        return {"success": False, "error": "No accounts to process"}

    logger.info(f"bet365 megaboost_all: {len(accounts)} accounts, sport={sport}, team={match_team}, stake=${stake}")

    # Run accounts sequentially (1 at a time — Camoufox is too heavy for concurrent runs)
    semaphore = asyncio.Semaphore(1)

    async def throttled(acc):
        async with semaphore:
            return await _megaboost_single_account(acc, sport, match_team, stake, boost_index)

    tasks = [throttled(acc) for acc in accounts]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    account_results = []
    for r in results:
        if isinstance(r, Exception):
            account_results.append({"success": False, "error": str(r)})
        else:
            account_results.append(r)

    succeeded = sum(1 for r in account_results if r.get("success"))
    failed = len(account_results) - succeeded

    logger.info(f"bet365 megaboost_all: done — {succeeded}/{len(account_results)} succeeded")

    return {
        "success": succeeded > 0,
        "total": len(account_results),
        "succeeded": succeeded,
        "failed": failed,
        "results": account_results,
    }


async def bet365_list_boosts(session_id: str) -> dict:
    """List all visible boost cards on the bet365 homepage."""
    session = _browser_sessions.get(session_id)
    if not session:
        return {"success": False, "error": f"No active session: {session_id}"}

    page = session["page"]
    try:
        await page.goto("https://www.bet365.com.au/#/HO/", timeout=30000)
        await page.wait_for_timeout(4000)

        # Dismiss cookies
        try:
            await page.evaluate("""() => {
                const buttons = document.querySelectorAll('button, div[role="button"], a');
                for (const btn of buttons) {
                    if ((btn.textContent || '').trim() === 'Accept All') { btn.click(); return; }
                }
            }""")
            await page.wait_for_timeout(2000)
        except Exception:
            pass

        # Scroll to load all cards
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 400)")
            await page.wait_for_timeout(800)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(1000)

        boosts = await page.evaluate(r"""() => {
            const boosts = [];
            const seen = new Set();
            const pattern = /\$\d+\s+stake\s+returns\s+\$/i;

            const allEls = document.querySelectorAll('*');

            for (const el of allEls) {
                const text = el.innerText || '';
                if (text.length < 30 || text.length > 400) continue;
                if (!pattern.test(text)) continue;
                if (!el.offsetParent) continue;

                const elRect = el.getBoundingClientRect();
                if (elRect.width < 150 || elRect.width > 500) continue;
                if (elRect.height < 80 || elRect.height > 500) continue;

                const cardId = Math.round(elRect.x) + ',' + Math.round(elRect.y);
                if (seen.has(cardId)) continue;
                seen.add(cardId);

                const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
                const title = lines[0] || '';

                const oddsEls = [];
                const candidates = el.querySelectorAll('span, div, button, a');
                for (const c of candidates) {
                    const ct = (c.textContent || '').trim();
                    if (/^\d+\.\d{2}$/.test(ct) && c.children.length === 0 && c.offsetParent) {
                        const r = c.getBoundingClientRect();
                        if (r.width > 10 && r.width < 150 && r.height > 8 && r.height < 60) {
                            oddsEls.push({ text: ct, val: parseFloat(ct) });
                        }
                    }
                }

                const isMega = text.toUpperCase().includes('MEGA') ||
                               el.innerHTML.toUpperCase().includes('MEGA');

                oddsEls.sort((a, b) => b.val - a.val);
                const returnsMatch = text.match(/\$(\d+)\s+stake\s+returns\s+\$([\d,.]+)/i);

                boosts.push({
                    index: boosts.length,
                    type: isMega ? 'MEGA_BOOST' : 'BET_BOOST',
                    title: title.substring(0, 80),
                    description: lines.slice(0, 5).join(' | '),
                    original_odds: oddsEls.length >= 2 ? oddsEls[oddsEls.length - 1].text : '',
                    boosted_odds: oddsEls.length >= 1 ? oddsEls[0].text : '',
                    stake: returnsMatch ? parseInt(returnsMatch[1]) : 0,
                    returns: returnsMatch ? returnsMatch[0] : '',
                });
            }

            boosts.sort((a, b) => {
                if (a.type !== b.type) return a.type === 'MEGA_BOOST' ? -1 : 1;
                return 0;
            });
            return boosts;
        }""")

        return {"success": True, "boosts": boosts, "count": len(boosts)}

    except Exception as e:
        logger.error(f"bet365 list_boosts error: {e}")
        return {"success": False, "error": str(e)}


async def bet365_status() -> dict:
    """Return status of all active bet365 browser sessions."""
    sessions = {}
    for sid, data in _browser_sessions.items():
        page = data.get("page")
        alive = False
        if page:
            try:
                await page.evaluate("1+1")
                alive = True
            except Exception:
                pass

        sessions[sid] = {
            "username": data.get("username"),
            "alive": alive,
        }

    return {
        "active_sessions": len(_browser_sessions),
        "sessions": sessions,
    }

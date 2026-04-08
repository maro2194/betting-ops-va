"""
bet365 browser-based auth and bet placement via Camoufox.

bet365 has no REST API for betting — everything is browser-automated.
The token farm maintains persistent Camoufox browser sessions.
"""
import logging
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

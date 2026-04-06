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
    """Place a bet on bet365 using a persistent browser session."""
    session = _browser_sessions.get(session_id)
    if not session:
        return {"success": False, "error": f"No active session: {session_id}"}

    # TODO: Implement actual bet placement flow
    return {
        "success": False,
        "error": "bet365 browser placement not yet implemented",
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

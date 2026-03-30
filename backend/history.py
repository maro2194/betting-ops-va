"""
TAB Bet History - Browser-based scraping.
TAB bet history APIs return 404, so we scrape the UI.
Uses Camoufox (required to bypass Akamai on page load).
"""
import asyncio
import os
import sys
import re
import logging
from typing import Optional

try:
    from camoufox.async_api import AsyncCamoufox
except ImportError:
    AsyncCamoufox = None

CAMOUFOX_EXE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "camoufox", "camoufox.exe")

logger = logging.getLogger(__name__)

PROXY_PATTERN = re.compile(r'https?://([^:]+):([^@]+)@([^:]+):(\d+)')


def _parse_proxy(proxy_url: str) -> dict:
    match = PROXY_PATTERN.match(proxy_url)
    if match:
        user, passwd, host, port = match.groups()
        return {"server": f"http://{host}:{port}", "username": user, "password": passwd}
    return {"server": proxy_url}


async def scrape_bet_history(token: str, proxy_url: str, profile_dir: str) -> dict:
    """
    Scrape bet history from TAB UI using browser.
    Returns dict with bets list and balance.
    """
    proxy_dict = _parse_proxy(proxy_url)

    launch_kwargs = dict(
        headless=True,
        proxy=proxy_dict,
        geoip=True,
        persistent_context=True,
        user_data_dir=profile_dir,
    )
    if sys.platform == "win32" and os.path.exists(CAMOUFOX_EXE):
        launch_kwargs["executable_path"] = CAMOUFOX_EXE

    async with AsyncCamoufox(**launch_kwargs) as context:
        page = context.pages[0] if context.pages else await context.new_page()

        # Load TAB homepage
        logger.info("Loading TAB homepage for history scrape...")
        await page.goto("https://www.tab.com.au/", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(8)

        # Check if logged in — multiple methods since globalStore may not hydrate
        logged_in = await page.evaluate("""() => {
            try {
                // Check globalStore
                if (window.globalStore) {
                    const s = window.globalStore.getState();
                    if (s && s.user && s.user.authToken) return true;
                }
                // Check localStorage for auth token
                const acctId = localStorage.getItem('tab.account_id');
                if (acctId) return true;
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    if (k.includes('auth0') && localStorage.getItem(k).includes('access_token')) return true;
                }
                // Check body text
                const t = document.body.innerText;
                if (t.includes('My Account') || t.includes('Balance') || t.includes('Bet Slip')) return true;
                return false;
            } catch(e) { return false; }
        }""")

        if not logged_in:
            raise Exception("Not logged in - please login first")

        # Navigate to bet history
        logger.info("Navigating to bet history...")

        # Try direct URL first (works when logged in via persistent profile)
        await page.goto("https://www.tab.com.au/account/betting-activity", timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(10)

        # Check if we landed on the right page or got redirected to login
        current_url = page.url
        logger.info(f"Bet history URL: {current_url}")

        if "login" in current_url.lower() or "auth" in current_url.lower():
            # Redirected to login — try UI navigation instead
            logger.info("Redirected to login, trying UI navigation...")
            await page.goto("https://www.tab.com.au/", timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(8)

            # Try clicking through UI
            clicked = False
            for sel in ['a:has-text("Betting Activity")', 'a:has-text("My Bets")',
                        'button:has-text("Betting Activity")', '[href*="betting-activity"]']:
                try:
                    link = page.locator(sel).first
                    if await link.is_visible(timeout=3000):
                        await link.click()
                        await asyncio.sleep(10)
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                result = await page.evaluate("""() => {
                    const els = document.querySelectorAll('a, button, [role="button"]');
                    for (const el of els) {
                        const text = el.textContent.toLowerCase();
                        if (text.includes('betting activity') || text.includes('my bets')) {
                            el.click();
                            return {found: true};
                        }
                    }
                    return {found: false};
                }""")
                if result.get("found"):
                    await asyncio.sleep(10)

        # Wait for page to fully render
        await asyncio.sleep(8)

        # Extract bet data from page text
        page_text = await page.evaluate("() => document.body.innerText")
        bets = _parse_bet_text(page_text)

        # Extract balance
        balance = None
        balance_match = re.search(r'balance[:\s]*\$\s*([\d,.]+)', page_text, re.IGNORECASE)
        if balance_match:
            try:
                balance = f"${float(balance_match.group(1).replace(',', '')):.2f}"
            except Exception:
                pass

        return {
            "bets": bets,
            "balance": balance,
        }


def _parse_bet_text(page_text: str) -> list[dict]:
    """Parse bet details from page text."""
    bets = []
    lines = page_text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Look for bet type markers
        is_sgm = line == "Same Game Multi"
        is_multi = line == "Multi" or line == "Fixed Odds Multi"
        is_single = line in ("Fixed Odds", "FO")

        if is_sgm or is_multi or is_single:
            bet_info = {"type": "SGM" if is_sgm else ("Multi" if is_multi else "Single")}

            # Look ahead for details
            for j in range(i + 1, min(i + 30, len(lines))):
                l = lines[j].strip()

                # Status
                if l in ("Won", "WON"):
                    bet_info["status"] = "WON"
                elif l in ("Lost", "LOST"):
                    bet_info["status"] = "LOST"
                elif l in ("Pending", "PENDING", "Open"):
                    bet_info["status"] = "PENDING"

                # Stake
                if l == "Stake" and j + 1 < len(lines):
                    m = re.search(r'\$\s*([\d,.]+)', lines[j + 1].strip())
                    if m:
                        bet_info["stake"] = float(m.group(1).replace(",", ""))

                # Odds
                if l == "Odds" and j + 1 < len(lines):
                    try:
                        bet_info["odds"] = float(lines[j + 1].strip())
                    except ValueError:
                        pass

                # Return/Payout
                if l == "Return" and j + 1 < len(lines):
                    ret = lines[j + 1].strip()
                    if ret != "Nil":
                        m = re.search(r'\$\s*([\d,.]+)', ret)
                        if m:
                            bet_info["payout"] = float(m.group(1).replace(",", ""))

                # Timestamp (various date formats)
                date_match = re.match(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d+\s+\w+', l)
                if date_match:
                    bet_info["timestamp"] = l
                    break

            # Collect leg descriptions between bet type and Stake
            legs = []
            for j in range(i + 1, min(i + 25, len(lines))):
                l = lines[j].strip()
                if l in ("Stake", "Won", "Lost", "Pending", "WON", "LOST", "PENDING", "Open"):
                    break
                # Player prop lines often contain "+" or "Over/Under"
                if l and not l.startswith("Same Game") and not l.startswith("Fixed") and len(l) > 3:
                    legs.append(l)

            if legs:
                bet_info["description"] = " / ".join(legs[:5])
                bet_info["legs"] = [{"description": lg} for lg in legs[:10]]

            if "stake" in bet_info:
                bets.append(bet_info)
                i += 5  # Skip ahead
            else:
                i += 1
        else:
            i += 1

    return bets

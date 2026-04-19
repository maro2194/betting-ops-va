"""Add Podziemski 10+ + Gui Santos 10+ to Neds betslip as an SGM.
Stops BEFORE placing. Captures final odds + screenshot.

Usage: python3 entain_sgm_add_legs.py <brand> <username> <password>
"""
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path
from patchright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("sgm")

CHROME_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36"
GAME_URL = "https://www.neds.com.au/sports/basketball/usa/nba/phoenix-suns-vs-golden-state-warriors/845be513-86e0-42b9-ad9e-6bb61d81ffcf"


async def login(page, base_url, username, password):
    await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(8000)
    for cs in [page.get_by_text("Accept All", exact=True), page.locator("#onetrust-accept-btn-handler")]:
        try:
            if await cs.count() > 0 and await cs.first.is_visible():
                await cs.first.click(timeout=5000)
                await page.wait_for_timeout(2000)
                break
        except Exception:
            continue
    clicked = False
    for _ in range(3):
        for sel in [page.get_by_text("Log in", exact=True), page.get_by_text("Login", exact=True)]:
            try:
                ct = await sel.count()
                for i in range(min(ct, 5)):
                    if await sel.nth(i).is_visible():
                        await sel.nth(i).click(timeout=5000)
                        clicked = True
                        break
            except Exception:
                continue
            if clicked:
                break
        if clicked:
            break
        await page.wait_for_timeout(3000)
    await page.wait_for_timeout(6000)
    await page.locator("#username").wait_for(state="visible", timeout=15000)
    await page.locator("#username").fill(username)
    await page.locator("#password").fill(password)
    await page.wait_for_timeout(1500)
    try:
        async with page.expect_navigation(timeout=45000, wait_until="domcontentloaded"):
            await page.click("#accept")
    except Exception:
        pass
    await page.wait_for_timeout(8000)


async def expand_collapsed_sections(page):
    """Click every 'collapsed' accordion header on the page to expand them."""
    count = await page.evaluate("""() => {
        const headers = document.querySelectorAll('.accordion.collapsed > [data-testid*="accordion"], .accordion.collapsed [class*="accordion__title"], .accordion-markets-nested.collapsed [class*="accordion__title"]');
        let n = 0;
        headers.forEach(h => { try { h.click(); n++; } catch(e) {} });
        return n;
    }""")
    log.info(f"  expanded {count} collapsed sections")


async def click_player_leg(page, market: str, player: str, expected_odds: str) -> bool:
    """Click the odds button for a given player in a given market (e.g. 'To Score 10+ Points')."""
    # Use JS click to bypass pointer-event interception from SGM accordion overlay
    marked = await page.evaluate("""([market, player, odds]) => {
        const all = Array.from(document.querySelectorAll('*'));
        let header = null;
        for (const el of all) {
            if (el.children.length > 0) continue;
            if ((el.textContent || '').trim() === market) { header = el; break; }
        }
        if (!header) return { ok: false, reason: 'market not found' };
        let container = header.parentElement;
        for (let i = 0; i < 15 && container; i++) {
            if ((container.textContent || '').includes(player)) break;
            container = container.parentElement;
        }
        if (!container) return { ok: false, reason: 'player not in market section' };
        let playerEl = null;
        for (const el of container.querySelectorAll('*')) {
            if (el.children.length > 0) continue;
            if ((el.textContent || '').includes(player)) { playerEl = el; break; }
        }
        if (!playerEl) return { ok: false, reason: 'player leaf not found' };
        let row = playerEl.parentElement;
        for (let i = 0; i < 12 && row; i++) {
            const btns = row.querySelectorAll('button');
            for (const b of btns) {
                const bt = (b.textContent || '').trim();
                if (bt === odds) {
                    b.scrollIntoView({block: 'center'});
                    // Try native click (bypasses pointer-events overlay)
                    b.click();
                    return { ok: true, level: i, btnText: bt, clicked: 'js' };
                }
            }
            row = row.parentElement;
        }
        return { ok: false, reason: 'odds button not found' };
    }""", [market, player, expected_odds])

    log.info(f"  JS-clicked '{player}' → {marked}")
    return marked.get("ok", False)


async def main():
    brand, username, password = sys.argv[1:4]
    base_url = f"https://www.{brand}.com.au/"

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        channel="chrome", headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--renderer-process-limit=2",
            "--js-flags=--max-old-space-size=512",
        ])
    ctx = await browser.new_context(user_agent=CHROME_UA,
        viewport={"width": 1280, "height": 900},
        locale="en-AU", timezone_id="Australia/Sydney")
    page = await ctx.new_page()

    try:
        await login(page, base_url, username, password)
        log.info("logged in, navigating to game")

        try:
            await page.goto(GAME_URL, wait_until="commit", timeout=60000)
            log.info("  goto commit done")
        except Exception as e:
            log.error(f"  goto failed: {e!r}")
            raise
        await page.wait_for_timeout(8000)
        log.info(f"  page URL after 8s wait: {page.url[:120]}")

        # Scroll down just enough to trigger "To Score 10+ Points" market to render
        for _ in range(4):
            await page.mouse.wheel(0, 800)
            await page.wait_for_timeout(800)
        await page.wait_for_timeout(1500)

        # Expand any collapsed accordions (SGM section wraps player markets)
        await expand_collapsed_sections(page)
        await page.wait_for_timeout(2000)
        # Scroll more to reveal expanded content
        for _ in range(4):
            await page.mouse.wheel(0, 800)
            await page.wait_for_timeout(600)

        ts = int(time.time())

        # Click Podziemski 10+
        ok1 = await click_player_leg(page, "To Score 10+ Points", "Brandin Podziemski", "1.20")
        await page.wait_for_timeout(2500)

        # Click Gui Santos 10+
        ok2 = await click_player_leg(page, "To Score 10+ Points", "Gui Santos", "1.35")
        await page.wait_for_timeout(2500)

        log.info(f"legs added: Podziemski={ok1}  Gui Santos={ok2}")

        # Screenshot current page (legs added)
        await page.screenshot(path=f"/tmp/sgm_{ts}_after_legs.png", full_page=True)

        # Open betslip — look for a visible betslip panel/button
        # Neds usually has a sidebar betslip already open with count badge
        # Try clicking 'Betslip' button / count
        betslip_found = False
        for sel in [
            page.get_by_text("Betslip", exact=True),
            page.locator("[data-testid*='betslip']"),
            page.locator("button:has-text('Betslip')"),
        ]:
            try:
                ct = await sel.count()
                for i in range(min(ct, 5)):
                    if await sel.nth(i).is_visible():
                        await sel.nth(i).click(timeout=5000)
                        betslip_found = True
                        log.info(f"  clicked betslip via {sel}")
                        break
            except Exception:
                continue
            if betslip_found:
                break

        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"/tmp/sgm_{ts}_betslip.png", full_page=True)

        # Extract betslip text + odds
        body_text = await page.inner_text("body")
        Path(f"/tmp/sgm_{ts}_betslip_body.txt").write_text(body_text[:60000])

        # Look for SGM-specific odds indicator
        # Neds betslip: shows '$X.XX odds' or 'combined odds'
        for patt in [
            r"SGM\s*([\d.]+)",
            r"Same Game Multi[^\d]*([\d.]+)",
            r"Combined Odds[^\d]*([\d.]+)",
            r"Total Odds[^\d]*([\d.]+)",
        ]:
            m = re.findall(patt, body_text, re.IGNORECASE)
            if m:
                log.info(f"  pattern '{patt}' → {m[:3]}")

        log.info(f"screenshots: /tmp/sgm_{ts}_after_legs.png  /tmp/sgm_{ts}_betslip.png")
    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    import traceback
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"TOP-LEVEL EXCEPTION: {e!r}")
        traceback.print_exc()
        sys.exit(1)

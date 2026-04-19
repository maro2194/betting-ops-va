"""Recon: login to Neds, navigate to Phoenix Suns vs Golden State Warriors,
find 10+ points markets for Podziemski + Gui Santos. Dump structure.

Usage: python3 entain_sgm_recon.py <brand> <username> <password>
"""
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from patchright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("recon")

CHROME_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36"


async def login(page, base_url, username, password):
    log.info("loading homepage")
    await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(8000)

    # Cookie consent
    for cs in [
        page.get_by_text("Accept All", exact=True),
        page.get_by_text("Accept", exact=True),
        page.locator("#onetrust-accept-btn-handler"),
    ]:
        try:
            if await cs.count() > 0 and await cs.first.is_visible():
                await cs.first.click(timeout=5000)
                log.info("cookie banner clicked")
                await page.wait_for_timeout(2000)
                break
        except Exception:
            continue

    clicked = False
    for attempt in range(3):
        for sel in [
            page.get_by_text("Log in", exact=True),
            page.get_by_text("Login", exact=True),
            page.locator("button:has-text('Log in')"),
        ]:
            try:
                count = await sel.count()
                for i in range(min(count, 5)):
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

    if not clicked:
        raise RuntimeError("login button not found")

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
    log.info(f"post-login URL: {page.url[:120]}")


async def find_nba_game(page, base_url):
    """Navigate to NBA basketball + find Suns vs Warriors."""
    # Common Entain paths:
    for nba_path in [
        "sports/basketball/usa/nba",
        "sports/basketball",
    ]:
        url = f"{base_url.rstrip('/')}/{nba_path}"
        log.info(f"trying {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)
            if "basketball" in page.url.lower():
                log.info(f"on basketball page: {page.url[:120]}")
                break
        except Exception as e:
            log.warning(f"nav failed: {e}")

    # Search for the matchup in the DOM
    body = await page.inner_text("body")
    has_suns = "suns" in body.lower() or "phoenix" in body.lower()
    has_gsw = "warriors" in body.lower() or "golden state" in body.lower()
    log.info(f"on page — suns:{has_suns}  warriors:{has_gsw}")

    # Try clicking an element with both team names visible
    for team_pat in ["Phoenix Suns", "Suns v Warriors", "Golden State"]:
        loc = page.get_by_text(team_pat)
        count = await loc.count()
        log.info(f"text '{team_pat}' found: {count} elements")
        if count > 0:
            for i in range(min(count, 8)):
                try:
                    if await loc.nth(i).is_visible():
                        log.info(f"  visible at index {i}")
                        break
                except Exception:
                    pass

    # Dump all matchup-like elements (look for VS/v. separator)
    elems = await page.query_selector_all("a[href*='game'], a[href*='event'], a[href*='match']")
    log.info(f"found {len(elems)} game/event/match links")
    for el in elems[:20]:
        try:
            text = (await el.text_content() or "").strip()
            href = await el.get_attribute("href")
            if text and ("suns" in text.lower() or "warriors" in text.lower() or "phoenix" in text.lower() or "golden state" in text.lower()):
                log.info(f"  CANDIDATE: {text[:80]!r} -> {href[:120]}")
        except Exception:
            continue


async def main():
    if len(sys.argv) != 4:
        print("Usage: entain_sgm_recon.py <brand> <username> <password>")
        sys.exit(2)
    brand, username, password = sys.argv[1:4]
    base_url = f"https://www.{brand}.com.au/"

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        channel="chrome", headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(
        user_agent=CHROME_UA,
        viewport={"width": 1920, "height": 1080},
        locale="en-AU", timezone_id="Australia/Sydney",
    )
    page = await context.new_page()

    try:
        await login(page, base_url, username, password)
        await find_nba_game(page, base_url)

        # Screenshot for manual review
        ts = int(time.time())
        shot = f"/tmp/recon_{brand}_{ts}.png"
        await page.screenshot(path=shot, full_page=True)
        log.info(f"screenshot: {shot}")

        # Dump visible text from the page for analysis
        body = await page.inner_text("body")
        dump = f"/tmp/recon_{brand}_{ts}.txt"
        Path(dump).write_text(body[:20000])
        log.info(f"body dump: {dump} ({len(body)} chars)")
    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())

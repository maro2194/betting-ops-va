"""Build an SGM on the Neds Phoenix Suns v Golden State Warriors game.
Legs: 10+ points for Podziemski AND 10+ points for Gui Santos.
Stops BEFORE placing — only extracts/reports odds.

Usage: python3 entain_sgm_build.py <brand> <username> <password>
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
    log.info(f"logged in — URL: {page.url[:120]}")


async def dump_page_labels(page, tag):
    """Dump tab + section labels to help navigate."""
    labels = await page.evaluate("""() => {
        const out = new Set();
        document.querySelectorAll('button, [role="tab"], h1, h2, h3, h4, a').forEach(el => {
            const t = (el.textContent || '').trim();
            if (t && t.length < 50 && t.length > 2) out.add(t);
        });
        return Array.from(out);
    }""")
    log.info(f"--- {tag} labels ({len(labels)}) ---")
    for l in labels:
        log.info(f"    {l!r}")


async def find_player_line(page, player_query: str, line_text: str = "10+") -> dict | None:
    """Scroll to find a row containing the player name AND the line (10+), return odds."""
    # First reveal all content by scrolling to bottom
    for _ in range(8):
        await page.mouse.wheel(0, 600)
        await page.wait_for_timeout(800)
    await page.wait_for_timeout(1500)

    # Find element closest to player name text that also has line_text in nearby siblings
    data = await page.evaluate("""([player, line]) => {
        const all = Array.from(document.querySelectorAll('*'));
        const out = [];
        for (const el of all) {
            if (el.children.length > 0) continue;
            const t = (el.textContent || '').trim();
            if (!t) continue;
            if (t.toLowerCase().includes(player.toLowerCase())) {
                // walk up and look for a container that also contains line_text
                let p = el.parentElement;
                for (let i = 0; i < 10 && p; i++) {
                    const txt = p.textContent || '';
                    if (txt.includes(line)) {
                        // find buttons with odds-like numbers in this container
                        const buttons = p.querySelectorAll('button');
                        const btns = [];
                        for (const b of buttons) {
                            const bt = (b.textContent || '').trim();
                            if (/\\d+\\.\\d+/.test(bt)) btns.push({text: bt, rect: b.getBoundingClientRect()});
                        }
                        out.push({level: i, container_text: txt.slice(0, 300), buttons: btns.map(b => ({text: b.text, x: Math.round(b.rect.x), y: Math.round(b.rect.y)}))});
                        return out;
                    }
                    p = p.parentElement;
                }
            }
        }
        return out;
    }""", [player_query, line_text])
    return data


async def main():
    brand, username, password = sys.argv[1:4]
    base_url = f"https://www.{brand}.com.au/"

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(channel="chrome", headless=True,
        args=["--disable-blink-features=AutomationControlled"])
    ctx = await browser.new_context(user_agent=CHROME_UA,
        viewport={"width": 1920, "height": 1080},
        locale="en-AU", timezone_id="Australia/Sydney")
    page = await ctx.new_page()

    try:
        await login(page, base_url, username, password)

        log.info(f"navigating to game: {GAME_URL}")
        await page.goto(GAME_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)

        # Screenshot top of page
        ts = int(time.time())
        await page.screenshot(path=f"/tmp/sgm_{ts}_01_game_top.png", full_page=False)

        await dump_page_labels(page, "game page")

        # Look for player tabs / sections
        for name in ["Podziemski", "Gui Santos", "Santos", "Player Props", "Player Points", "Same Game Multi", "SGM", "Props"]:
            found = await page.get_by_text(name).count()
            log.info(f"  '{name}' count={found}")

        # Scroll + search for Podziemski near "10+"
        result = await find_player_line(page, "Podziemski", "10+")
        log.info(f"Podziemski/10+ search → {json.dumps(result)[:500] if result else None}")

        result2 = await find_player_line(page, "Santos", "10+")
        log.info(f"Santos/10+ search → {json.dumps(result2)[:500] if result2 else None}")

        # Full-page screenshot for review
        await page.screenshot(path=f"/tmp/sgm_{ts}_02_full.png", full_page=True)

        # Dump text
        body = await page.inner_text("body")
        Path(f"/tmp/sgm_{ts}_body.txt").write_text(body[:80000])
        log.info(f"saved /tmp/sgm_{ts}_02_full.png + body.txt")
    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())

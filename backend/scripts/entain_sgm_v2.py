"""Entain SGM builder v2 — clicks Same Game Multi tab FIRST, then selects legs inside SGM context.
Intercepts network calls to capture SGM pricing/placement API for future integration.

Stops before final place-bet click. Stake not entered.
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
log = logging.getLogger("sgm2")

CHROME_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36"
GAME_URL = "https://www.neds.com.au/sports/basketball/usa/nba/phoenix-suns-vs-golden-state-warriors/845be513-86e0-42b9-ad9e-6bb61d81ffcf"


async def login(page, base_url, username, password):
    await page.goto(base_url, wait_until="commit", timeout=60000)
    await page.wait_for_timeout(8000)
    for cs in [page.get_by_text("Accept All", exact=True), page.locator("#onetrust-accept-btn-handler")]:
        try:
            if await cs.count() > 0 and await cs.first.is_visible():
                await cs.first.click(timeout=5000); await page.wait_for_timeout(2000); break
        except Exception:
            pass
    clicked = False
    for _ in range(3):
        for sel in [page.get_by_text("Log in", exact=True), page.get_by_text("Login", exact=True)]:
            try:
                ct = await sel.count()
                for i in range(min(ct, 5)):
                    if await sel.nth(i).is_visible():
                        await sel.nth(i).click(timeout=5000); clicked = True; break
            except Exception:
                pass
            if clicked: break
        if clicked: break
        await page.wait_for_timeout(3000)
    await page.wait_for_timeout(6000)
    await page.locator("#username").wait_for(state="visible", timeout=15000)
    await page.locator("#username").fill(username)
    await page.locator("#password").fill(password)
    await page.wait_for_timeout(1500)
    try:
        async with page.expect_navigation(timeout=45000, wait_until="commit"):
            await page.click("#accept")
    except Exception:
        pass
    await page.wait_for_timeout(8000)


async def main():
    brand, username, password = sys.argv[1:4]
    base_url = f"https://www.{brand}.com.au/"
    ts = int(time.time())

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(channel="chrome", headless=True,
        args=["--disable-blink-features=AutomationControlled"])
    ctx = await browser.new_context(user_agent=CHROME_UA,
        viewport={"width": 1400, "height": 900},
        locale="en-AU", timezone_id="Australia/Sydney")
    page = await ctx.new_page()

    # Intercept all API calls to find SGM endpoints
    captured = []
    async def on_req(req):
        if any(k in req.url for k in ["sgm", "same-game", "betting", "bet-requests", "/v2/", "/v1/", "markets"]):
            captured.append({"method": req.method, "url": req.url, "headers": dict(req.headers),
                             "post_data": req.post_data if req.method == "POST" else None})
    page.on("request", on_req)

    try:
        log.info("logging in...")
        await login(page, base_url, username, password)
        log.info(f"logged in, URL={page.url[:120]}")

        log.info(f"navigating to game: {GAME_URL[:80]}")
        try:
            await page.goto(GAME_URL, wait_until="commit", timeout=60000)
            log.info("  goto commit done")
        except Exception as e:
            log.error(f"  goto failed: {e!r}")
            raise
        await page.wait_for_timeout(10000)
        log.info(f"  current URL: {page.url[:120]}")
        await page.screenshot(path=f"/tmp/v2_{ts}_01_game.png")

        # Look for SGM tab/button - Neds SGM UI
        log.info("looking for Same Game Multi tab/button")
        sgm_clicked = False
        sgm_selectors = [
            lambda p: p.get_by_text("Same Game Multi", exact=True),
            lambda p: p.get_by_text("Same Game Multi"),
            lambda p: p.locator("[data-testid*='same-game-multi']"),
            lambda p: p.locator("[data-testid*='sgm']"),
            lambda p: p.locator("button:has-text('Same Game Multi')"),
            lambda p: p.locator("[role='tab']:has-text('Same Game Multi')"),
        ]
        for getloc in sgm_selectors:
            try:
                loc = getloc(page)
                ct = await loc.count()
                log.info(f"  selector count={ct}")
                for i in range(min(ct, 5)):
                    try:
                        if await loc.nth(i).is_visible():
                            await loc.nth(i).scroll_into_view_if_needed()
                            await loc.nth(i).click(timeout=5000)
                            sgm_clicked = True
                            log.info(f"  SGM clicked (selector idx {i})")
                            break
                    except Exception as e:
                        log.warning(f"    click try {i} failed: {e!r}")
                        continue
            except Exception:
                continue
            if sgm_clicked: break
        log.info(f"sgm_clicked={sgm_clicked}")

        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"/tmp/v2_{ts}_02_after_sgm_click.png")

        # Dump labels now visible
        labels = await page.evaluate("""() => {
            const out = new Set();
            document.querySelectorAll('button, [role="tab"], h1, h2, h3, h4').forEach(el => {
                const t = (el.textContent || '').trim();
                if (t && t.length < 60 && t.length > 2) out.add(t);
            });
            return Array.from(out);
        }""")
        log.info(f"--- visible labels after SGM click ({len(labels)}) ---")
        for l in sorted(labels):
            log.info(f"    {l!r}")

        # Also dump first 10k chars of body
        body = await page.inner_text("body")
        Path(f"/tmp/v2_{ts}_body.txt").write_text(body[:20000])
        log.info(f"body saved to /tmp/v2_{ts}_body.txt  len={len(body)}")

        # Save captured network calls
        Path(f"/tmp/v2_{ts}_network.json").write_text(json.dumps(captured, indent=2, default=str))
        log.info(f"captured {len(captured)} network calls to /tmp/v2_{ts}_network.json")
    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    import traceback
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"TOP-LEVEL: {e!r}")
        traceback.print_exc()
        sys.exit(1)

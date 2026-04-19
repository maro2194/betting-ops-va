"""Full SGM builder: login → game → click Same Game Multi tab → expand "To Score 10+ Points"
→ click Podziemski + Gui Santos → capture SGM pricing API call → report odds.

STOPS BEFORE place-bet. No stake entered.
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
log = logging.getLogger("sgm_full")

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


async def click_sgm_tab(page):
    for getloc in [
        lambda: page.get_by_text("Same Game Multi", exact=True),
        lambda: page.locator("[role='tab']:has-text('Same Game Multi')"),
        lambda: page.locator("button:has-text('Same Game Multi')"),
    ]:
        try:
            loc = getloc()
            ct = await loc.count()
            for i in range(min(ct, 5)):
                if await loc.nth(i).is_visible():
                    await loc.nth(i).click(timeout=5000)
                    log.info(f"  SGM tab clicked (idx {i})")
                    return True
        except Exception:
            continue
    return False


async def expand_market(page, market_name: str) -> bool:
    """Click the market header to expand it."""
    result = await page.evaluate("""(market) => {
        const all = document.querySelectorAll('*');
        for (const el of all) {
            if (el.children.length > 0) continue;
            if ((el.textContent || '').trim() === market) {
                // walk up to find a clickable parent (the accordion header)
                let p = el;
                for (let i = 0; i < 6 && p; i++) {
                    p.scrollIntoView({block: 'center'});
                    p.click();
                    p = p.parentElement;
                }
                return {ok: true};
            }
        }
        return {ok: false};
    }""", market_name)
    await page.wait_for_timeout(1500)
    return result.get("ok", False)


async def click_leg(page, player: str, odds: str) -> bool:
    """Find player's leg in any expanded market and click the matching odds button."""
    result = await page.evaluate("""([player, odds]) => {
        const all = document.querySelectorAll('*');
        // find leaf element with player name
        for (const el of all) {
            if (el.children.length > 0) continue;
            if ((el.textContent || '').includes(player)) {
                // walk up to find row with an odds button matching
                let row = el.parentElement;
                for (let i = 0; i < 10 && row; i++) {
                    const btns = row.querySelectorAll('button');
                    for (const b of btns) {
                        const bt = (b.textContent || '').trim();
                        if (bt === odds) {
                            b.scrollIntoView({block: 'center'});
                            b.click();
                            return {ok: true, found_player: el.textContent.slice(0, 60)};
                        }
                    }
                    row = row.parentElement;
                }
            }
        }
        return {ok: false};
    }""", [player, odds])
    return result.get("ok", False)


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

    captured = []
    captured_resp = []
    async def on_req(req):
        if any(k in req.url for k in ["sgm", "same-game", "price", "bet-request", "quote", "PriceBetRequest", "v2/bet"]):
            captured.append({"method": req.method, "url": req.url,
                             "headers": {k: v for k, v in req.headers.items() if k.lower() in ("authorization", "content-type", "x-kpsdk-ct", "x-kpsdk-cd")},
                             "post_data": req.post_data})
    async def on_resp(resp):
        if any(k in resp.url for k in ["sgm", "same-game", "price", "bet-request", "quote", "PriceBetRequest"]):
            try:
                text = await resp.text()
                captured_resp.append({"url": resp.url, "status": resp.status, "body": text[:3000]})
            except Exception:
                pass
    page.on("request", on_req)
    page.on("response", on_resp)

    try:
        log.info("login")
        await login(page, base_url, username, password)
        log.info(f"  URL: {page.url[:120]}")

        log.info("nav to game")
        await page.goto(GAME_URL, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(10000)
        log.info(f"  URL: {page.url[:120]}")

        log.info("click SGM tab")
        await click_sgm_tab(page)
        await page.wait_for_timeout(3500)
        await page.screenshot(path=f"/tmp/full_{ts}_01_sgm_tab.png")

        # Scroll down to reveal To Score 10+ Points
        for _ in range(4):
            await page.mouse.wheel(0, 700)
            await page.wait_for_timeout(500)

        log.info("expand 'To Score 10+ Points'")
        await expand_market(page, "To Score 10+ Points")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=f"/tmp/full_{ts}_02_expanded.png")

        log.info("click Podziemski 10+ (1.20)")
        ok1 = await click_leg(page, "Brandin Podziemski", "1.20")
        log.info(f"  ok1={ok1}")
        await page.wait_for_timeout(2000)

        log.info("click Gui Santos 10+ (1.35)")
        ok2 = await click_leg(page, "Gui Santos", "1.35")
        log.info(f"  ok2={ok2}")
        await page.wait_for_timeout(3000)
        await page.screenshot(path=f"/tmp/full_{ts}_03_legs_clicked.png")

        # Try opening betslip if not auto-visible
        try:
            bs = page.get_by_text("Betslip", exact=True)
            ct = await bs.count()
            for i in range(min(ct, 3)):
                if await bs.nth(i).is_visible():
                    await bs.nth(i).click(timeout=3000)
                    log.info(f"  clicked Betslip (idx {i})")
                    break
        except Exception as e:
            log.info(f"  betslip click: {e!r}")
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"/tmp/full_{ts}_04_betslip.png", full_page=True)

        # Dump body looking for odds/price indicators
        body = await page.inner_text("body")
        Path(f"/tmp/full_{ts}_body.txt").write_text(body[:40000])

        # Extract SGM-related odds patterns
        patt = re.findall(r"(Same Game Multi|SGM|Combined Odds|Total Odds)[^\d]{0,40}([\d]+\.[\d]{2})", body)
        for m in patt[:10]:
            log.info(f"  odds pattern: {m}")

        # Save network capture
        Path(f"/tmp/full_{ts}_req.json").write_text(json.dumps(captured, indent=2, default=str))
        Path(f"/tmp/full_{ts}_resp.json").write_text(json.dumps(captured_resp, indent=2, default=str))
        log.info(f"  captured {len(captured)} requests, {len(captured_resp)} responses")
    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    import traceback
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"TOP: {e!r}")
        traceback.print_exc()
        sys.exit(1)

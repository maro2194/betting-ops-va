"""Deeper recon: click into Suns v Warriors, find Podziemski + Gui Santos 10+ points."""
import asyncio
import logging
import re
import sys
import time
from pathlib import Path
from patchright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("recon2")

CHROME_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36"


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


async def main():
    brand, username, password = sys.argv[1:4]
    base_url = f"https://www.{brand}.com.au/"
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(channel="chrome", headless=True,
        args=["--disable-blink-features=AutomationControlled"])
    context = await browser.new_context(user_agent=CHROME_UA,
        viewport={"width": 1920, "height": 1080}, locale="en-AU", timezone_id="Australia/Sydney")
    page = await context.new_page()

    try:
        await login(page, base_url, username, password)
        log.info("logged in")

        # Go to NBA
        await page.goto(f"{base_url.rstrip('/')}/sports/basketball/usa/nba",
                        wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)

        # Click the Suns v Warriors fixture. Try finding an anchor containing both names.
        # Dump all <a> elements
        anchors = await page.query_selector_all("a")
        log.info(f"{len(anchors)} anchors on NBA page")
        target_href = None
        for a in anchors:
            try:
                text = (await a.text_content() or "").strip().lower()
                href = await a.get_attribute("href") or ""
                if ("phoenix" in text or "suns" in text) and ("warriors" in text or "golden" in text):
                    log.info(f"  MATCH: text={text[:80]!r} href={href[:160]}")
                    target_href = href
                    break
            except Exception:
                continue

        # Alternative: click on any element with both team names
        if not target_href:
            # Query divs that contain both team names
            found = await page.evaluate("""() => {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const t = (el.textContent || '').toLowerCase();
                    if (t.includes('phoenix') && t.includes('warriors') && t.length < 600) {
                        // find closest <a>
                        const a = el.closest('a');
                        if (a) return { href: a.href, text: el.textContent.slice(0, 120) };
                    }
                }
                return null;
            }""")
            if found:
                log.info(f"  DOM search match: {found}")
                target_href = found["href"]

        if target_href:
            log.info(f"navigating to game: {target_href}")
            # Convert to absolute URL if relative
            if target_href.startswith("/"):
                target_href = base_url.rstrip("/") + target_href
            await page.goto(target_href, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(6000)
            log.info(f"on game page: {page.url[:200]}")
        else:
            log.error("could not find Suns v Warriors link")

        # On game page — search for player props tabs
        body_text = await page.inner_text("body")
        # Look for tab-like labels
        for label in ["Player Props", "Player Points", "Points", "Same Game Multi", "SGM", "Props"]:
            if label.lower() in body_text.lower():
                log.info(f"  label found: {label}")

        # Check for Podziemski / Gui Santos
        for name in ["Podziemski", "Gui Santos", "Santos"]:
            if name.lower() in body_text.lower():
                log.info(f"  player found on page: {name}")
            else:
                log.info(f"  player NOT yet visible: {name}")

        # Screenshot
        ts = int(time.time())
        await page.screenshot(path=f"/tmp/recon2_{ts}.png", full_page=True)
        Path(f"/tmp/recon2_{ts}.txt").write_text(body_text[:30000])
        log.info(f"screenshot /tmp/recon2_{ts}.png  body /tmp/recon2_{ts}.txt")

        # Also dump all top-level tab/button labels
        buttons = await page.query_selector_all("button, [role='tab'], [role='button'], nav a")
        tab_texts = set()
        for b in buttons[:150]:
            try:
                t = (await b.text_content() or "").strip()
                if 2 <= len(t) <= 40:
                    tab_texts.add(t)
            except Exception:
                continue
        log.info(f"--- tab/button labels ({len(tab_texts)}) ---")
        for t in sorted(tab_texts):
            log.info(f"    {t!r}")
    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())

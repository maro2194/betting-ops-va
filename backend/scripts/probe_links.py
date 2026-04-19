"""Dump all anchors on NBA page that look like event/game links."""
import asyncio
from patchright.async_api import async_playwright

CHROME_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36"

async def main():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(channel="chrome", headless=True,
        args=["--disable-blink-features=AutomationControlled"])
    ctx = await browser.new_context(user_agent=CHROME_UA,
        viewport={"width": 1920, "height": 1080},
        locale="en-AU", timezone_id="Australia/Sydney")
    page = await ctx.new_page()
    await page.goto("https://www.neds.com.au/sports/basketball/usa/nba",
                    wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(8000)
    all_links = await page.evaluate("""() => {
        const links = document.querySelectorAll('a[href]');
        return Array.from(links).map(a => a.href).filter((v, i, arr) => arr.indexOf(v) === i);
    }""")
    for l in all_links:
        if "basketball" in l.lower() or "nba" in l.lower() or "suns" in l.lower() or "warriors" in l.lower() or "event" in l.lower():
            print(l)
    print(f"TOTAL: {len(all_links)}")
    await browser.close()
    await pw.stop()

asyncio.run(main())

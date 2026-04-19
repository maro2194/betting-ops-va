"""Click the Suns v Warriors fixture row, see where we navigate."""
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

    # Try clicking "384 Markets" text area
    markets_text = page.get_by_text("384 Markets", exact=True)
    ct = await markets_text.count()
    print(f"'384 Markets' elements: {ct}")
    if ct > 0:
        try:
            await markets_text.first.click(timeout=8000)
            print("clicked 384 Markets")
            await page.wait_for_timeout(6000)
            print(f"URL after: {page.url[:200]}")
            body = await page.inner_text("body")
            for name in ["Podziemski", "Gui Santos", "Player Props", "Same Game Multi"]:
                if name.lower() in body.lower():
                    print(f"  found on page: {name}")
                else:
                    print(f"  not present: {name}")
            return
        except Exception as e:
            print(f"click failed: {e}")

    # Fallback — walk from Phoenix Suns text upwards, click the smallest ancestor that has both team names AND is clickable
    result = await page.evaluate("""() => {
        const all = document.querySelectorAll('*');
        for (const el of all) {
            const t = (el.textContent || '').trim();
            if (t === 'Phoenix Suns' && el.children.length === 0) {
                let p = el.parentElement;
                for (let i = 0; i < 15 && p; i++) {
                    const txt = p.textContent || '';
                    if (txt.includes('Golden State Warriors') && txt.includes('384 Markets')) {
                        p.setAttribute('data-probe', 'fixture-row');
                        return {ok: true, level: i, tag: p.tagName, cls: p.className.toString().slice(0,200)};
                    }
                    p = p.parentElement;
                }
            }
        }
        return {ok: false};
    }""")
    print("fixture DOM walk:", result)
    if result.get("ok"):
        try:
            await page.locator("[data-probe='fixture-row']").click(timeout=8000)
            print("clicked fixture row")
            await page.wait_for_timeout(6000)
            print(f"URL after: {page.url[:200]}")
        except Exception as e:
            print(f"fixture row click failed: {e}")

    await browser.close()
    await pw.stop()

asyncio.run(main())

"""Click the fixture title 'Phoenix Suns vs Golden State Warriors'."""
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

    # Find candidates: elements whose direct text contains both team names
    candidates = await page.evaluate("""() => {
        const all = document.querySelectorAll('*');
        const out = [];
        for (const el of all) {
            if (el.children.length === 0) continue;
            // Only look at elements whose full textContent (without descendants' odds) starts with Phoenix
            const t = (el.textContent || '').trim();
            if (t.startsWith('Phoenix Suns') && t.includes('Golden State Warriors') && t.length < 120) {
                const rect = el.getBoundingClientRect();
                out.push({
                    tag: el.tagName,
                    cls: (el.className.toString() || '').slice(0, 80),
                    text: t,
                    w: Math.round(rect.width), h: Math.round(rect.height),
                    x: Math.round(rect.x), y: Math.round(rect.y),
                    clickable: el.tagName === 'A' || el.onclick !== null || el.hasAttribute('onclick') || el.hasAttribute('role') || el.getAttribute('role') === 'button',
                });
            }
        }
        return out.slice(0, 20);
    }""")
    for c in candidates:
        print(c)

    # Try clicking the first candidate
    if candidates:
        # Use a unique attribute to find it
        result = await page.evaluate("""() => {
            const all = document.querySelectorAll('*');
            for (const el of all) {
                if (el.children.length === 0) continue;
                const t = (el.textContent || '').trim();
                if (t.startsWith('Phoenix Suns') && t.includes('Golden State Warriors') && t.length < 120) {
                    el.setAttribute('data-click-target', 'fixture-title');
                    return true;
                }
            }
            return false;
        }""")
        if result:
            try:
                await page.locator("[data-click-target='fixture-title']").click(timeout=8000)
                print("clicked")
                await page.wait_for_timeout(6000)
                print(f"URL after click: {page.url[:200]}")
            except Exception as e:
                print(f"click failed: {e}")

    await browser.close()
    await pw.stop()

asyncio.run(main())

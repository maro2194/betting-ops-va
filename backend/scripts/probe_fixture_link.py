"""Find the Suns v Warriors fixture link via DOM walk (unauth page)."""
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
    matches = await page.evaluate("""() => {
        const all = document.querySelectorAll('*');
        const out = [];
        for (const el of all) {
            const t = (el.textContent || '').trim();
            if (t === 'Phoenix Suns' && el.children.length === 0) {
                let p = el.parentElement;
                for (let i = 0; i < 12 && p; i++) {
                    if ((p.textContent || '').includes('Golden State Warriors')) {
                        const as = p.querySelectorAll('a[href]');
                        for (const a of as) {
                            out.push({href: a.href, level: i});
                        }
                        return out;
                    }
                    p = p.parentElement;
                }
            }
        }
        return out;
    }""")
    for m in matches[:20]:
        print(m)
    await browser.close()
    await pw.stop()


asyncio.run(main())
